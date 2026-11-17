"""ATH 适配层(DR-102):授权页、令牌、按范围放行与拒绝。不需要网关,也不需要运行中的 DataMind。"""
import os
import tempfile
import time

os.environ["DATAMIND_ATH_STATE"] = tempfile.mkdtemp()
from ath_adapter import provider as P  # noqa: E402

P.bootstrap()
CB = "http://localhost:3000/ath/callback"
client = P.app.test_client()


def _token(scopes):
    P._tokens["t"] = {"user": "analyst", "client_id": "ath-gateway", "scopes": scopes, "exp": time.time() + 60}
    return {"Authorization": "Bearer t"}


def _authorize(**extra):
    q = {"response_type": "code", "client_id": "ath-gateway", "redirect_uri": CB, "scope": "datamind:catalog.read",
         "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s", **extra}
    return client.get("/authorize", query_string=q)


def test_consent_page_drops_unknown_params_and_sets_frame_headers():
    r = _authorize(**{'"><script>alert(1)</script>': "x"})
    assert r.status_code == 200
    assert b"<script>" not in r.data
    assert r.headers["X-Frame-Options"] == "DENY"


def test_path_traversal_is_rejected():
    h = _token(["datamind:ontology.read"])
    for path in ("/api/graph/../sparql", "/api/graph/./x/../../sparql", "/api/graph//x"):
        assert client.get(path, headers=h).status_code == 400, path


def test_unknown_endpoint_and_missing_scope():
    h = _token(["datamind:catalog.read"])
    assert client.get("/api/admin", headers=h).status_code == 404
    assert client.post("/api/sparql", headers=h, json={"query": "ASK {}"}).status_code == 403
    assert client.get("/api/sysinfo").status_code == 401


def test_approve_cannot_add_unknown_scopes_or_skip_pkce():
    import json
    creds = json.loads((P.STATE / "initial_password.json").read_text())
    form = {"client_id": "ath-gateway", "redirect_uri": CB, "state": "s", "scope": "datamind:catalog.read evil:admin",
            "code_challenge": "x" * 43, "code_challenge_method": "S256", "username": creds["user"],
            "password": creds["password"], "decision": "approve", "grant": ["datamind:catalog.read", "evil:admin"]}
    r = client.post("/authorize/approve", data=form)
    assert r.status_code == 302
    code = r.headers["Location"].split("code=")[1].split("&")[0]
    assert P._codes[code]["scopes"] == ["datamind:catalog.read"]
    form.pop("code_challenge")
    assert client.post("/authorize/approve", data=form).status_code == 400


def test_malformed_basic_auth_is_invalid_client():
    r = client.post("/token", headers={"Authorization": "Basic !!!"}, data={"grant_type": "authorization_code"})
    assert r.status_code == 401


def test_token_response_is_not_cacheable():
    import base64
    import hashlib
    import json
    creds = json.loads((P.STATE / "initial_password.json").read_text())
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = client.post("/authorize/approve", data={"client_id": "ath-gateway", "redirect_uri": CB, "state": "s",
                    "scope": "datamind:catalog.read", "code_challenge": challenge, "code_challenge_method": "S256",
                    "username": creds["user"], "password": creds["password"], "decision": "approve", "grant": "datamind:catalog.read"})
    code = r.headers["Location"].split("code=")[1].split("&")[0]
    secret = json.loads((P.STATE / "clients.json").read_text())["ath-gateway"]["client_secret"]
    r = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CB,
                                    "client_id": "ath-gateway", "client_secret": secret, "code_verifier": verifier})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    r = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CB,
                                    "client_id": "ath-gateway", "client_secret": secret, "code_verifier": verifier})
    assert r.status_code == 400  # one-time code


def test_agent_card_points_at_gateway():
    card = client.get("/.well-known/agent-card.json").get_json()
    assert card["url"].endswith("/ath/proxy/datamind/a2a") and card["protocolVersion"] == "0.3.0"
    assert card["preferredTransport"] == "JSONRPC"


# ── 令牌限定数据来源、深度问数范围、请求号贯通(DR-104) ─────────────────

class _Upstream:
    """替身 DataMind:记录适配层转发出去的每个请求。"""
    def __init__(self, monkeypatch, body=b'{"ok": true}'):
        self.calls = []

        class _R:
            status = 200
            headers = {"Content-Type": "application/json"}

            def read(_s):
                return body

            def __enter__(_s):
                return _s

            def __exit__(_s, *a):
                return False

        def fake(req, timeout=None):
            self.calls.append({"url": req.full_url, "headers": {k.lower(): v for k, v in req.header_items()},
                               "timeout": timeout, "data": req.data})
            return _R()
        monkeypatch.setattr(P.urllib.request, "urlopen", fake)


def _tok(scopes, source="conn_plant2", client="agent-7"):
    P._tokens["t2"] = {"user": "analyst", "client_id": client, "scopes": scopes, "source": source, "exp": time.time() + 60}
    return {"Authorization": "Bearer t2"}


def _audit_tail(n=1):
    import json
    lines = (P.STATE / "audit.jsonl").read_text().splitlines()
    return [json.loads(x) for x in lines[-n:]]


def test_requests_carry_the_tokens_data_source_actor_and_a_request_id(monkeypatch):
    up = _Upstream(monkeypatch)
    r = client.get("/api/tables", headers=_tok(["datamind:catalog.read"]))
    assert r.status_code == 200
    h = up.calls[0]["headers"]
    assert h["x-datamind-source"] == "conn_plant2" and h["x-datamind-actor"] == "ath:agent-7:analyst"
    rec = _audit_tail()[0]
    assert rec["event"] == "resource.allowed" and rec["source"] == "conn_plant2"
    assert rec["request_id"] == h["x-request-id"] and len(rec["request_id"]) >= 12


def test_a_caller_cannot_override_the_data_source(monkeypatch):
    up = _Upstream(monkeypatch)
    client.get("/api/tables?src=demo", headers={**_tok(["datamind:catalog.read"]), "X-DataMind-Source": "demo"})
    assert up.calls[0]["headers"]["x-datamind-source"] == "conn_plant2"
    assert "src=demo" not in up.calls[0]["url"]


def test_consent_lets_the_user_pick_a_source_and_binds_it_to_the_token(monkeypatch):
    import base64
    import hashlib
    import json
    monkeypatch.setattr(P, "_sources", lambda: [{"id": "demo", "name": "示例主库"}, {"id": "conn_plant2", "name": "二厂"}])
    page = _authorize().get_data(as_text=True)
    assert 'name="source"' in page and "conn_plant2" in page and "二厂" in page
    creds = json.loads((P.STATE / "initial_password.json").read_text())
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    form = {"client_id": "ath-gateway", "redirect_uri": CB, "state": "s", "scope": "datamind:catalog.read",
            "code_challenge": challenge, "code_challenge_method": "S256", "username": creds["user"],
            "password": creds["password"], "decision": "approve", "grant": "datamind:catalog.read"}
    assert client.post("/authorize/approve", data={**form, "source": "conn_other"}).status_code == 400
    r = client.post("/authorize/approve", data={**form, "source": "conn_plant2"})
    code = r.headers["Location"].split("code=")[1].split("&")[0]
    secret = json.loads((P.STATE / "clients.json").read_text())["ath-gateway"]["client_secret"]
    tok = client.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CB,
                                      "client_id": "ath-gateway", "client_secret": secret, "code_verifier": verifier}).get_json()
    assert P._tokens[tok["access_token"]]["source"] == "conn_plant2"


def test_asking_questions_needs_its_own_scope_and_gets_a_longer_timeout(monkeypatch):
    up = _Upstream(monkeypatch, b'{"narrative": "ok", "degraded": true}')
    body = {"q": "上月各车间产量"}
    assert client.post("/api/chat", headers=_tok(["datamind:ontology.read"]), json=body).status_code == 403
    assert up.calls == []
    r = client.post("/api/chat", headers=_tok(["datamind:qa.ask"]), json=body)
    assert r.status_code == 200 and r.get_json()["degraded"] is True
    assert up.calls[0]["url"].endswith("/api/chat") and up.calls[0]["timeout"] >= 180
    assert up.calls[0]["headers"]["x-datamind-source"] == "conn_plant2"


def _mcp_capture(monkeypatch, text="构建作业已发起:job_id=bj_0123456789(source=conn_plant2)。"):
    seen = {}

    def fake(messages, ctx=None):
        seen.update(messages=messages, ctx=ctx)
        mid = messages[-1].get("id")
        return [{"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": False}}]
    monkeypatch.setattr(P, "_mcp_call", fake)
    return seen


def _start(args):
    return {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "start_build", "arguments": args}}


def test_a_build_through_mcp_is_pinned_to_the_tokens_source_and_logged_with_its_job(monkeypatch):
    seen = _mcp_capture(monkeypatch)
    h = _tok(["datamind:build.run"])
    r = client.post("/mcp", headers=h, json=_start({"q": "建本体"}))
    assert r.status_code == 200
    assert seen["messages"][-1]["params"]["arguments"]["source"] == "conn_plant2"
    assert seen["ctx"]["source"] == "conn_plant2" and seen["ctx"]["actor"] == "ath:agent-7:analyst"
    rec = _audit_tail()[0]
    assert rec["event"] == "mcp.allowed" and rec["job_id"] == "bj_0123456789"
    assert rec["request_id"] == seen["ctx"]["request_id"]
    assert client.post("/mcp", headers=h, json=_start({"q": "建本体", "source": "demo"})).status_code == 403


def test_mcp_server_forwards_source_actor_and_request_id_from_its_environment(monkeypatch):
    import mcp_build_server as M
    sent = {}

    class _R:
        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(M.urllib.request, "urlopen",
                        lambda req, timeout=None: sent.update({k.lower(): v for k, v in req.header_items()}) or _R())
    monkeypatch.setenv("DATAMIND_SOURCE", "conn_plant2")
    monkeypatch.setenv("DATAMIND_ACTOR", "ath:agent-7:analyst")
    monkeypatch.setenv("DATAMIND_REQUEST_ID", "req-9")
    M._http("GET", "/api/build/sources")
    assert sent["x-datamind-source"] == "conn_plant2" and sent["x-datamind-actor"] == "ath:agent-7:analyst"
    assert sent["x-request-id"] == "req-9"

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

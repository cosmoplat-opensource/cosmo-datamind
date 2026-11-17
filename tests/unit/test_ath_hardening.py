"""ATH 适配层加固(DR-110):探索性测试查出的缺陷。授权页只能选示例库、审计日志权限、口令不限次、
上游不可达与畸形请求返回 500、issuer 取自请求头、导出类型、令牌撤销与自省、数据来源清单越界。"""
import json
import os
import tempfile
import time
import urllib.error

import pytest

os.environ.setdefault("DATAMIND_ATH_STATE", tempfile.mkdtemp())
from ath_adapter import provider as P  # noqa: E402

P.bootstrap()
CB = "http://localhost:3000/ath/callback"
client = P.app.test_client()


def _tok(scopes, source="demo", name="h1"):
    P._tokens[name] = {"user": "analyst", "client_id": "ath-gateway", "scopes": scopes, "source": source,
                       "exp": time.time() + 60}
    return {"Authorization": "Bearer " + name}


class _Resp:
    def __init__(self, body, status=200, ctype="application/json"):
        self._b, self.status, self.headers = body, status, {"Content-Type": ctype}

    def read(self, *a):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _upstream(monkeypatch, body=b"[]", ctype="application/json", calls=None):
    def fake(req, timeout=None):
        if calls is not None:
            calls.append(req.full_url if hasattr(req, "full_url") else req)
        return _Resp(body, ctype=ctype)
    monkeypatch.setattr(P.urllib.request, "urlopen", fake)


def _creds():
    return json.loads((P.STATE / "initial_password.json").read_text())


def _approve(password=None, source=None, username=None):
    c = _creds()
    form = {"client_id": "ath-gateway", "redirect_uri": CB, "state": "s", "scope": "datamind:catalog.read",
            "code_challenge": "x" * 43, "code_challenge_method": "S256", "username": username or c["user"],
            "password": password or c["password"], "decision": "approve", "grant": "datamind:catalog.read"}
    if source:
        form["source"] = source
    return client.post("/authorize/approve", data=form)


@pytest.fixture(autouse=True)
def _reset_throttle():
    getattr(P, "_login_failures", {}).clear()
    yield
    getattr(P, "_login_failures", {}).clear()


# ── 授权页的数据来源 ────────────────────────────────────────────────

def test_consent_page_offers_every_ready_sqlite_source(monkeypatch):
    body = json.dumps({"assets": [], "sources": [
        {"id": "demo", "name": "示例主库", "kind": "sqlite", "ready": True},
        {"id": "uploads", "name": "上传数据库", "kind": "sqlite", "ready": True},
        {"id": "conn_x", "name": "外部库", "kind": "mysql", "ready": True},
        {"id": "conn_y", "name": "未就绪", "kind": "sqlite", "ready": False}]}).encode()
    _upstream(monkeypatch, body)
    assert [s["id"] for s in P._sources()] == ["demo", "uploads"]
    assert _approve(source="uploads").status_code == 302
    assert _approve(source="conn_x").status_code == 400


def test_sources_still_accepts_a_bare_list(monkeypatch):
    _upstream(monkeypatch, json.dumps([{"id": "demo", "kind": "sqlite", "ready": True}]).encode())
    assert [s["id"] for s in P._sources()] == ["demo"]


# ── 审计日志权限 ────────────────────────────────────────────────────

def test_audit_log_is_owner_only():
    P.audit("probe")
    assert (P.STATE / "audit.jsonl").stat().st_mode & 0o777 == 0o600


# ── 口令限次 ────────────────────────────────────────────────────────

def test_repeated_wrong_passwords_lock_the_account_for_a_while(monkeypatch):
    _upstream(monkeypatch, b"[]")
    for _ in range(P.LOGIN_MAX_FAILURES):
        assert _approve(password="wrong").status_code == 401
    r = _approve(password="wrong")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert _approve().status_code == 429                      # 锁定期内正确口令也不放行
    P._login_failures["analyst"]["until"] = time.time() - 1   # 锁定期过后恢复
    assert _approve().status_code == 302


def test_a_successful_login_clears_the_failure_count(monkeypatch):
    _upstream(monkeypatch, b"[]")
    for _ in range(P.LOGIN_MAX_FAILURES - 1):
        _approve(password="wrong")
    assert _approve().status_code == 302
    assert _approve(password="wrong").status_code == 401


# ── 上游不可达、畸形请求 ────────────────────────────────────────────

def test_upstream_unreachable_is_a_502_not_a_crash(monkeypatch):
    def down(req, timeout=None):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(P.urllib.request, "urlopen", down)
    r = client.get("/api/tables", headers=_tok(["datamind:catalog.read"]))
    assert r.status_code == 502 and r.get_json()["error"] == "upstream_unavailable"


def test_graph_keys_with_spaces_or_cjk_are_forwarded_percent_encoded(monkeypatch):
    calls = []
    _upstream(monkeypatch, b"{}", calls=calls)
    h = _tok(["datamind:ontology.read"])
    assert client.get("/api/graph/a b", headers=h).status_code == 200
    assert client.get("/api/graph/图谱", headers=h).status_code == 200
    assert calls[0].endswith("/api/graph/a%20b") and calls[1].endswith("/api/graph/%E5%9B%BE%E8%B0%B1")


def test_an_encoded_question_mark_cannot_smuggle_query_parameters(monkeypatch):
    calls = []
    _upstream(monkeypatch, b"{}", calls=calls)
    client.get("/api/graph/demo%3Fsrc=other", headers=_tok(["datamind:ontology.read"]))
    assert calls and "?" not in calls[0] and calls[0].endswith("/api/graph/demo%3Fsrc%3Dother")


@pytest.mark.parametrize("body", [
    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": "x"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": ["start_build"]}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": 7, "arguments": {}}},
])
def test_malformed_mcp_calls_get_a_json_rpc_error(body):
    r = client.post("/mcp", headers=_tok(["datamind:build.read", "datamind:build.run"]), json=body)
    assert r.status_code == 400 and r.get_json()["error"]["code"] == -32602


def test_mcp_notification_gets_no_body(monkeypatch):
    monkeypatch.setattr(P, "_mcp_call", lambda *a, **k: [])
    r = client.post("/mcp", headers=_tok(["datamind:build.read"]), json={"jsonrpc": "2.0", "method": "tools/list"})
    assert r.status_code == 204 and r.data == b""


@pytest.mark.parametrize("params", [
    "x", {"message": "x"}, {"message": {"parts": ["x"]}}, {"message": {"metadata": "x"}},
    {"message": {"metadata": {"skill": ["catalog.tables"]}}}, {"message": {"parts": [{"kind": "data", "data": "x"}]}},
])
def test_malformed_a2a_messages_get_a_json_rpc_error(params):
    r = client.post("/a2a", headers=_tok(["datamind:catalog.read"]),
                    json={"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": params})
    assert r.status_code == 400 and r.get_json()["error"]["code"] == -32602


# ── issuer、响应类型 ────────────────────────────────────────────────

def test_issuer_is_not_taken_from_an_arbitrary_host_header():
    d = client.get("/.well-known/oauth-authorization-server", headers={"Host": "evil.example"}).get_json()
    assert "evil.example" not in json.dumps(d) and d["issuer"] == f"http://127.0.0.1:{P.PORT}"
    local = client.get("/.well-known/oauth-authorization-server", headers={"Host": f"localhost:{P.PORT}"}).get_json()
    assert local["issuer"] == f"http://localhost:{P.PORT}"
    assert d["revocation_endpoint"].endswith("/revoke") and d["introspection_endpoint"].endswith("/introspect")


def test_upstream_content_type_is_passed_through(monkeypatch):
    _upstream(monkeypatch, b"@prefix : <x> .", ctype="text/turtle; charset=utf-8")
    r = client.get("/api/graph/demo/export.ttl", headers=_tok(["datamind:ontology.read"]))
    assert r.headers["Content-Type"].startswith("text/turtle")


# ── 撤销与自省 ──────────────────────────────────────────────────────

def _basic():
    import base64
    sec = json.loads((P.STATE / "clients.json").read_text())["ath-gateway"]["client_secret"]
    return {"Authorization": "Basic " + base64.b64encode(f"ath-gateway:{sec}".encode()).decode()}


def test_introspect_and_revoke_need_client_authentication():
    assert client.post("/revoke", data={"token": "x"}).status_code == 401
    assert client.post("/introspect", data={"token": "x"}).status_code == 401


def test_a_revoked_token_stops_working(monkeypatch):
    _upstream(monkeypatch, b"[]")
    h = _tok(["datamind:catalog.read"], source="uploads", name="rv1")
    info = client.post("/introspect", headers=_basic(), data={"token": "rv1"}).get_json()
    assert info["active"] is True and info["scope"] == "datamind:catalog.read" and info["source"] == "uploads"
    assert client.post("/revoke", headers=_basic(), data={"token": "rv1"}).status_code == 200
    assert client.post("/introspect", headers=_basic(), data={"token": "rv1"}).get_json() == {"active": False}
    assert client.get("/api/tables", headers=h).status_code == 401
    assert client.post("/revoke", headers=_basic(), data={"token": "never-issued"}).status_code == 200   # RFC 7009 §2.2


def test_a_client_cannot_revoke_or_inspect_another_clients_token():
    P._tokens["other"] = {"user": "analyst", "client_id": "someone-else", "scopes": [], "source": "demo", "exp": time.time() + 60}
    assert client.post("/introspect", headers=_basic(), data={"token": "other"}).get_json() == {"active": False}
    client.post("/revoke", headers=_basic(), data={"token": "other"})
    assert "other" in P._tokens


# ── 构建工具只看得到令牌的数据来源 ──────────────────────────────────

def test_source_listing_is_limited_to_the_bound_source(monkeypatch):
    import mcp_build_server as M
    payload = {"sources": [{"id": "demo", "name": "示例主库", "kind": "sqlite", "tables": 108, "ready": True},
                           {"id": "uploads", "name": "上传数据库", "kind": "sqlite", "tables": 3, "ready": True}], "assets": []}
    monkeypatch.setattr(M, "_http", lambda *a, **k: (json.loads(json.dumps(payload)), None))
    monkeypatch.setenv("DATAMIND_SOURCE", "uploads")
    text, err = M.call_tool("list_build_sources", {})
    assert not err and "uploads" in text and "demo" not in text and "示例主库" not in text
    monkeypatch.delenv("DATAMIND_SOURCE")
    text, _ = M.call_tool("list_build_sources", {})
    assert "demo" in text and "uploads" in text

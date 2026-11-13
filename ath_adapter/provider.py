#!/usr/bin/env python3
"""COSMO-DataMind as an ATH-protected service provider (gateway deployment mode).

The ATH gateway acts as an OAuth 2.0 client of this provider.  This process adds, in front of an
unmodified DataMind (default http://127.0.0.1:8092):

  * an OAuth 2.0 authorization server for the user-side half of the ATH trusted handshake
      GET  /authorize            consent page: the DataMind user signs in and approves (or narrows) scopes
      POST /authorize/approve    issues a one-time authorization code bound to client, redirect_uri, PKCE
      POST /token                authorization_code grant, client authentication, PKCE S256 (RFC 7636)
      GET  /.well-known/oauth-authorization-server   (RFC 8414 metadata)
  * a resource server that enforces scopes per endpoint and forwards to DataMind
      GET  /api/sysinfo|tables|overview|glossary            datamind:catalog.read
      GET  /api/graphs|graph/<key>|metrics                  datamind:ontology.read
      POST /api/sparql                                      datamind:sparql.query
      POST /mcp   MCP JSON-RPC (tools/list, tools/call) bridged to DataMind's MCP build server;
                  read tools need datamind:build.read, start_build needs datamind:build.run
      POST /a2a   A2A 0.3.0 JSON-RPC message/send; each skill maps to one scope
      GET  /.well-known/agent-card.json   DataMind's A2A AgentCard (public; its url is the gateway proxy)
  * an append-only JSONL audit log of every consent, token and allow/deny decision.

None of DataMind's MCP tools can mark a relation verified/asserted or delete a graph, so an agent that
passes the ATH handshake cannot change reviewed conclusions even with datamind:build.run.

State (OAuth client secret, user password hash, audit log) lives in DATAMIND_ATH_STATE (default
workdir/ath_state, git-ignored) with mode 0600.  Codes and access tokens are kept in memory; restarting
the adapter invalidates them.

Build jobs started through the MCP bridge are held by the DataMind server (DR-101), so they outlive the
per-request MCP process this adapter spawns.

Run:  python3 -m ath_adapter.provider      (port DATAMIND_ATH_PORT, default 8093)
"""
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("DATAMIND_ATH_STATE") or
             Path(os.environ.get("DATAMIND_WORKDIR") or ROOT / "workdir") / "ath_state")
STATE.mkdir(parents=True, exist_ok=True)
DATAMIND = os.environ.get("DATAMIND_URL", "http://127.0.0.1:8092").rstrip("/")
MCP_SERVER = os.environ.get("DATAMIND_MCP_SERVER", str(ROOT / "mcp_build_server.py"))
ORGANIZATION = os.environ.get("DATAMIND_ATH_ORGANIZATION", "COSMO DataMind")
PROJECT_URL = os.environ.get("DATAMIND_ATH_PROJECT_URL", "https://github.com/cosmoplat-opensource/cosmo-datamind")
PORT = int(os.environ.get("DATAMIND_ATH_PORT", "8093"))
GATEWAY = os.environ.get("ATH_GATEWAY_URL", "http://localhost:3000").rstrip("/")
AUTHORIZE_PARAMS = ("response_type", "client_id", "redirect_uri", "scope", "state", "code_challenge",
                    "code_challenge_method", "resource")
CODE_TTL, TOKEN_TTL = 300, 3600

SCOPES = {
    "datamind:catalog.read": "读取数据目录：库表清单、概览、业务术语",
    "datamind:ontology.read": "读取本体图谱与指标定义",
    "datamind:sparql.query": "对本体执行只读 SPARQL 查询",
    "datamind:build.read": "查看本体构建数据源、产物、验收结果与人审队列",
    "datamind:build.run": "发起一次本体半自动构建（唯一写操作）",
}
ROUTES = [  # (method, path prefix, required scope)
    ("GET", "/api/sysinfo", "datamind:catalog.read"),
    ("GET", "/api/tables", "datamind:catalog.read"),
    ("GET", "/api/overview", "datamind:catalog.read"),
    ("GET", "/api/glossary", "datamind:catalog.read"),
    ("GET", "/api/graphs", "datamind:ontology.read"),
    ("GET", "/api/graph/", "datamind:ontology.read"),
    ("GET", "/api/metrics", "datamind:ontology.read"),
    ("POST", "/api/sparql", "datamind:sparql.query"),
]
MCP_TOOL_SCOPE = {"start_build": "datamind:build.run"}  # every other tool: datamind:build.read

app = Flask(__name__)
_lock = threading.Lock()
_codes: dict[str, dict] = {}
_tokens: dict[str, dict] = {}


# ---------------------------------------------------------------- local state (clients, users, audit)
def _load(name, default):
    f = STATE / name
    return json.loads(f.read_text()) if f.exists() else default


def _save(name, data):
    f = STATE / name
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.chmod(f, 0o600)


def _hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()


def bootstrap():
    """Create the gateway's OAuth client and one DataMind user on first start (secrets stay in state/, 0600)."""
    clients = _load("clients.json", {})
    if not clients:
        callback = os.environ.get("ATH_GATEWAY_CALLBACK", "http://localhost:3000/ath/callback")
        clients["ath-gateway"] = {"client_secret": secrets.token_urlsafe(32), "redirect_uris": [callback]}
        _save("clients.json", clients)
    users = _load("users.json", {})
    if not users:
        pw, salt = secrets.token_urlsafe(18), secrets.token_hex(16)
        users["analyst"] = {"display_name": "数据分析师（DataMind 用户）", "salt": salt, "hash": _hash(pw, salt)}
        _save("users.json", users)
        _save("initial_password.json", {"user": "analyst", "password": pw})
    return clients


def audit(event, **fields):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    with _lock, open(STATE / "audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _client_ok(client_id, client_secret):
    c = _load("clients.json", {}).get(client_id)
    return bool(c) and hmac.compare_digest(c["client_secret"], client_secret or "")


def _client_auth():
    """client_secret_basic (RFC 6749 §2.3.1) or client_secret_post."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            cid, _, sec = base64.b64decode(auth[6:], validate=True).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return "", ""
        return urllib.parse.unquote_plus(cid), urllib.parse.unquote_plus(sec)
    return request.form.get("client_id", ""), request.form.get("client_secret", "")


def _purge():
    """Drop expired authorization codes and access tokens (call with _lock held)."""
    now = time.time()
    for store in (_codes, _tokens):
        for k in [k for k, v in store.items() if v["exp"] < now]:
            del store[k]


# ---------------------------------------------------------------- authorization server
@app.get("/.well-known/oauth-authorization-server")
def metadata():
    base = request.host_url.rstrip("/")
    return jsonify(issuer=base, authorization_endpoint=f"{base}/authorize", token_endpoint=f"{base}/token",
                   response_types_supported=["code"], grant_types_supported=["authorization_code"],
                   code_challenge_methods_supported=["S256"], scopes_supported=list(SCOPES),
                   token_endpoint_auth_methods_supported=["client_secret_basic", "client_secret_post"])


@app.get("/authorize")
def authorize():
    q = request.args
    if q.get("response_type") != "code":
        return jsonify(error="unsupported_response_type"), 400
    client = _load("clients.json", {}).get(q.get("client_id", ""))
    if not client:
        return jsonify(error="invalid_client"), 400
    if q.get("redirect_uri") not in client["redirect_uris"]:  # exact match (RFC 6749 §3.1.2.3)
        audit("authorize.bad_redirect_uri", client_id=q.get("client_id"), redirect_uri=q.get("redirect_uri"))
        return jsonify(error="invalid_request", error_description="redirect_uri not registered"), 400
    if q.get("code_challenge_method") != "S256" or not q.get("code_challenge"):
        return jsonify(error="invalid_request", error_description="PKCE S256 required"), 400
    requested = [s for s in q.get("scope", "").split() if s]
    unknown = [s for s in requested if s not in SCOPES]
    if unknown or not requested:
        return jsonify(error="invalid_scope", error_description=f"unknown scopes: {unknown}"), 400
    hidden = "".join(f'<input type="hidden" name="{k}" value="{html.escape(q[k])}">' for k in AUTHORIZE_PARAMS if k in q)
    boxes = "".join(f'<label><input type="checkbox" name="grant" value="{s}" checked> <code>{s}</code> {html.escape(SCOPES[s])}</label><br>'
                    for s in requested)  # requested scopes are all keys of SCOPES at this point
    page = f"""<!doctype html><meta charset="utf-8"><title>COSMO-DataMind 授权确认</title>
<style>body{{font-family:Helvetica,"PingFang SC",sans-serif;max-width:560px;margin:40px auto;color:#1f2329}}
h2{{color:#4A5FF3}}label{{line-height:2}}button{{background:#4A5FF3;color:#fff;border:0;padding:8px 20px;border-radius:4px}}</style>
<h2>COSMO-DataMind · 智能体访问授权</h2>
<p>一个经 ATH 网关登记的智能体请求代表你访问 DataMind。你可以取消勾选不愿授予的权限。</p>
<form method="post" action="/authorize/approve">{hidden}
<p>{boxes}</p><p>用户名 <input name="username"> 密码 <input name="password" type="password"></p>
<button name="decision" value="approve">同意</button> <button name="decision" value="deny">拒绝</button></form>"""
    resp = Response(page, mimetype="text/html")
    resp.headers.update({"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'", "Cache-Control": "no-store"})
    return resp


@app.post("/authorize/approve")
def approve():
    f = request.form
    redirect_uri, state = f.get("redirect_uri", ""), f.get("state", "")
    client = _load("clients.json", {}).get(f.get("client_id", ""))
    if not client or redirect_uri not in client["redirect_uris"]:
        return jsonify(error="invalid_request", error_description="redirect_uri not registered"), 400
    sep = "&" if "?" in redirect_uri else "?"
    if f.get("decision") != "approve":
        audit("consent.denied", client_id=f.get("client_id"), scopes=f.get("scope"))
        return redirect(f"{redirect_uri}{sep}{urllib.parse.urlencode({'error': 'access_denied', 'state': state})}")
    user = _load("users.json", {}).get(f.get("username", ""))
    if not user or not hmac.compare_digest(user["hash"], _hash(f.get("password", ""), user["salt"])):
        audit("consent.login_failed", username=f.get("username"))
        return Response("用户名或密码错误", status=401)
    requested = [s for s in f.get("scope", "").split() if s in SCOPES]
    granted = [s for s in f.getlist("grant") if s in requested]  # the user may narrow, never widen
    if not f.get("code_challenge") or f.get("code_challenge_method") != "S256":
        return jsonify(error="invalid_request", error_description="PKCE S256 required"), 400
    if not granted:
        return redirect(f"{redirect_uri}{sep}{urllib.parse.urlencode({'error': 'access_denied', 'state': state})}")
    code = secrets.token_urlsafe(24)
    with _lock:
        _purge()
        _codes[code] = {"client_id": f.get("client_id"), "redirect_uri": redirect_uri, "scopes": granted,
                        "user": f.get("username"), "challenge": f.get("code_challenge"), "exp": time.time() + CODE_TTL,
                        "resource": f.get("resource")}
    audit("consent.approved", client_id=f.get("client_id"), user=f.get("username"), requested=requested, granted=granted)
    return redirect(f"{redirect_uri}{sep}{urllib.parse.urlencode({'code': code, 'state': state})}")


@app.post("/token")
def token():
    cid, sec = _client_auth()
    if not _client_ok(cid, sec):
        audit("token.invalid_client", client_id=cid)
        return jsonify(error="invalid_client"), 401
    if request.form.get("grant_type") != "authorization_code":
        return jsonify(error="unsupported_grant_type"), 400
    with _lock:
        rec = _codes.pop(request.form.get("code", ""), None)  # one-time use
    if not rec or rec["exp"] < time.time() or rec["client_id"] != cid or rec["redirect_uri"] != request.form.get("redirect_uri"):
        audit("token.invalid_grant", client_id=cid)
        return jsonify(error="invalid_grant"), 400
    verifier = request.form.get("code_verifier", "")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(challenge, rec["challenge"] or ""):
        audit("token.pkce_failed", client_id=cid)
        return jsonify(error="invalid_grant", error_description="PKCE verification failed"), 400
    access = secrets.token_urlsafe(32)
    with _lock:
        _tokens[access] = {"user": rec["user"], "client_id": cid, "scopes": rec["scopes"], "exp": time.time() + TOKEN_TTL}
    audit("token.issued", client_id=cid, user=rec["user"], scopes=rec["scopes"])
    resp = jsonify(access_token=access, token_type="Bearer", expires_in=TOKEN_TTL, scope=" ".join(rec["scopes"]))
    resp.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})  # RFC 6749 §5.1
    return resp


# ---------------------------------------------------------------- resource server
def _bearer():
    auth = request.headers.get("Authorization", "")
    tok = _tokens.get(auth[7:]) if auth.startswith("Bearer ") else None
    return tok if tok and tok["exp"] > time.time() else None


def _deny(status, error, **fields):
    audit("resource.denied", path=request.path, error=error, **fields)
    return jsonify(error=error, **fields), status


@app.route("/api/<path:rest>", methods=["GET", "POST"])
def resource(rest):
    tok = _bearer()
    if not tok:
        return _deny(401, "invalid_token")
    path = "/api/" + rest
    if any(seg in ("", ".", "..") for seg in rest.split("/")[:-1]) or rest.split("/")[-1] in (".", ".."):
        return _deny(400, "invalid_path", user=tok["user"])
    need = next((s for m, p, s in ROUTES if m == request.method and (path == p or (p.endswith("/") and path.startswith(p)))), None)
    if need is None:
        return _deny(404, "endpoint_not_exposed", user=tok["user"])
    if need not in tok["scopes"]:
        return _deny(403, "insufficient_scope", required=need, user=tok["user"])
    url = DATAMIND + path + ("?" + request.query_string.decode() if request.query_string else "")
    data = request.get_data() if request.method == "POST" else None
    req = urllib.request.Request(url, data=data, method=request.method, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, status = e.read(), e.code
    audit("resource.allowed", path=path, method=request.method, user=tok["user"], scope=need, status=status,
          ms=round(1000 * (time.time() - t0)))
    return Response(body, status=status, mimetype="application/json")


def _mcp_call(messages):
    """Run DataMind's stdio MCP build server for one exchange and return its JSON-RPC responses."""
    env = dict(os.environ, DATAMIND_URL=DATAMIND)
    lines = "".join(json.dumps(m) + "\n" for m in messages)
    try:
        out = subprocess.run([sys.executable, MCP_SERVER], input=lines, capture_output=True, text=True, timeout=180, env=env)
    except subprocess.TimeoutExpired:
        return []
    res = []
    for line in out.stdout.splitlines():
        if line.strip().startswith("{"):
            try:
                res.append(json.loads(line))
            except ValueError:
                pass
    return res


@app.post("/mcp")
def mcp():
    tok = _bearer()
    if not tok:
        return _deny(401, "invalid_token")
    msg = request.get_json(force=True, silent=True)
    if not isinstance(msg, dict):
        return jsonify(jsonrpc="2.0", id=None, error={"code": -32700, "message": "parse error"}), 400
    init = {"jsonrpc": "2.0", "id": "_init", "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "datamind-ath-bridge", "version": "0.1"}}}
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    if msg.get("method") == "tools/call":
        tool = (msg.get("params") or {}).get("name", "")
        need = MCP_TOOL_SCOPE.get(tool, "datamind:build.read")
        if need not in tok["scopes"]:
            audit("mcp.denied", tool=tool, required=need, user=tok["user"])
            return jsonify(jsonrpc="2.0", id=msg.get("id"), error={"code": -32001, "message": "insufficient_scope", "data": {"required": need}}), 403
    elif msg.get("method") == "tools/list":
        if "datamind:build.read" not in tok["scopes"] and "datamind:build.run" not in tok["scopes"]:
            audit("mcp.denied", method="tools/list", required="datamind:build.read", user=tok["user"])
            return jsonify(jsonrpc="2.0", id=msg.get("id"), error={"code": -32001, "message": "insufficient_scope"}), 403
    else:
        return jsonify(jsonrpc="2.0", id=msg.get("id"), error={"code": -32601, "message": "method not exposed"}), 404
    t0 = time.time()
    res = [r for r in _mcp_call([init, note, msg]) if r.get("id") == msg.get("id")]
    audit("mcp.allowed", method=msg.get("method"), tool=(msg.get("params") or {}).get("name"), user=tok["user"],
          ms=round(1000 * (time.time() - t0)))
    if not res:
        return jsonify(jsonrpc="2.0", id=msg.get("id"), error={"code": -32603, "message": "no response from MCP server"}), 502
    return jsonify(res[0])


# ---------------------------------------------------------------- A2A server (DataMind as a remote agent)
A2A_SKILLS = {  # skill id -> (required scope, handler description)
    "catalog.tables": ("datamind:catalog.read", "列出数据目录中的库表"),
    "ontology.graphs": ("datamind:ontology.read", "列出本体图谱"),
    "build.list": ("datamind:build.read", "列出已构建本体及验收状态"),
}


@app.get("/.well-known/agent-card.json")
def agent_card():
    """DataMind's A2A 0.3.0 AgentCard.  Its url is the ATH gateway proxy, so every A2A call carries an ATH token."""
    return jsonify(name="COSMO-DataMind", description="工业数据本体构建与智能问数平台", version="0.1.0",
                   url=f"{GATEWAY}/ath/proxy/datamind/a2a", protocolVersion="0.3.0", preferredTransport="JSONRPC",
                   provider={"organization": ORGANIZATION, "url": PROJECT_URL},
                   capabilities={"streaming": False, "pushNotifications": False},
                   defaultInputModes=["application/json"], defaultOutputModes=["application/json"],
                   securitySchemes={"ath": {"type": "http", "scheme": "bearer", "description": "ATH access token issued by the ATH gateway"}},
                   security=[{"ath": []}],
                   skills=[{"id": k, "name": v[1], "description": f"{v[1]}（需要作用域 {v[0]}）", "tags": [v[0]]} for k, v in A2A_SKILLS.items()])


def _a2a_run(skill):
    if skill == "catalog.tables":
        with urllib.request.urlopen(DATAMIND + "/api/tables", timeout=60) as r:
            tables = json.loads(r.read())
        return {"count": len(tables), "tables": [t.get("name", t) if isinstance(t, dict) else t for t in tables][:20]}
    if skill == "ontology.graphs":
        with urllib.request.urlopen(DATAMIND + "/api/graphs", timeout=60) as r:
            return {"graphs": json.loads(r.read())}
    res = _mcp_call([{"jsonrpc": "2.0", "id": "_init", "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "datamind-a2a", "version": "0.1"}}},
                     {"jsonrpc": "2.0", "method": "notifications/initialized"},
                     {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_built_graphs", "arguments": {}}}])
    text = next((r["result"]["content"][0]["text"] for r in res if r.get("id") == 1 and "result" in r), "")
    if not text:
        raise ValueError("no response from the MCP build server")
    return {"summary": text.splitlines()[0], "text": text}


@app.post("/a2a")
def a2a():
    """A2A JSON-RPC message/send. The skill is named in message.metadata.skill or in a data part."""
    tok = _bearer()
    if not tok:
        return _deny(401, "invalid_token")
    msg = request.get_json(force=True, silent=True)
    if not isinstance(msg, dict):
        return jsonify(jsonrpc="2.0", id=None, error={"code": -32700, "message": "parse error"}), 400
    if msg.get("method") != "message/send":
        return jsonify(jsonrpc="2.0", id=msg.get("id"), error={"code": -32601, "message": "method not supported"}), 404
    m = (msg.get("params") or {}).get("message") or {}
    skill = (m.get("metadata") or {}).get("skill") or next((p.get("data", {}).get("skill") for p in m.get("parts", []) if p.get("kind") == "data"), None)
    if skill not in A2A_SKILLS:
        return jsonify(jsonrpc="2.0", id=msg.get("id"), error={"code": -32602, "message": f"unknown skill {skill}"}), 400
    need = A2A_SKILLS[skill][0]
    if need not in tok["scopes"]:
        audit("a2a.denied", skill=skill, required=need, user=tok["user"])
        return jsonify(jsonrpc="2.0", id=msg.get("id"), error={"code": -32001, "message": "insufficient_scope", "data": {"required": need}}), 403
    t0 = time.time()
    task_id = secrets.token_hex(8)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        data = _a2a_run(skill)
    except (OSError, ValueError, KeyError, IndexError) as e:  # upstream DataMind or MCP server failure
        audit("a2a.failed", skill=skill, user=tok["user"], error=type(e).__name__)
        return jsonify(jsonrpc="2.0", id=msg.get("id"), result={
            "kind": "task", "id": task_id, "contextId": m.get("contextId") or task_id,
            "status": {"state": "failed", "timestamp": stamp,
                       "message": {"kind": "message", "role": "agent", "messageId": f"{task_id}-err",
                                   "parts": [{"kind": "text", "text": f"DataMind 调用失败：{type(e).__name__}"}]}}})
    audit("a2a.allowed", skill=skill, scope=need, user=tok["user"], ms=round(1000 * (time.time() - t0)))
    return jsonify(jsonrpc="2.0", id=msg.get("id"), result={
        "kind": "task", "id": task_id, "contextId": m.get("contextId") or task_id,
        "status": {"state": "completed", "timestamp": stamp},
        "artifacts": [{"artifactId": f"{task_id}-1", "name": skill, "parts": [{"kind": "data", "data": data}]}]})


@app.get("/health")
def health():
    return jsonify(status="ok", provider="cosmo-datamind", upstream=DATAMIND)


if __name__ == "__main__":
    bootstrap()
    app.run(host="127.0.0.1", port=PORT, threaded=True)

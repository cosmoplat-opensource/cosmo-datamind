#!/usr/bin/env python3
"""End-to-end verification of ATH on COSMO-DataMind (gateway mode).

Starts, as separate processes: the DataMind OAuth/resource adapter (8093), an agent identity server that
publishes the agent's A2A AgentCard with its ATH public key (8094), and the ATH reference gateway (3000)
with attestation signature verification on.  DataMind itself must already run on 8092 (./start.sh).  ATH_GATEWAY_DIR must point at a checkout of the
ATH reference gateway with its dependencies installed; it is not part of this repository.

Runs the trusted handshake with a DataMind user consent submitted through the consent form, calls
DataMind through the gateway over REST, MCP JSON-RPC and A2A JSON-RPC, runs negative tests and measures
latency.  Writes reports/<name>/e2e_report.json and e2e_report.md.

Environment: ATH_SDK=official (default, python-sdk with the user_token option) or own (ath_client.py);
ATH_REPORT names the report folder (default: the SDK name).
"""
import html
import http.client
import http.server
import json
import os
import re
import socketserver
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ath_client as A  # noqa: E402

SDK = os.environ.get("ATH_SDK", "official")
if SDK == "official":
    from official_adapter import OfficialGatewayClient as Client  # official ath-sdk (python-sdk repo)
else:
    Client = A.ATHGatewayClient

ROOT = HERE.parents[1]
if not os.environ.get("ATH_GATEWAY_DIR"):
    sys.exit("ATH_GATEWAY_DIR is not set: point it at a checkout of the ATH reference gateway (see ath_adapter/README.md)")
GATEWAY_DIR = Path(os.environ["ATH_GATEWAY_DIR"]).resolve()
STATE = Path(os.environ.get("DATAMIND_ATH_STATE") or ROOT / "workdir" / "ath_state")
ORGANIZATION = os.environ.get("DATAMIND_ATH_ORGANIZATION", "COSMO DataMind")
GW, PROV, IDP, DM = "http://localhost:3000", "http://127.0.0.1:8093", "http://127.0.0.1:8094", "http://127.0.0.1:8092"
AGENT_ID = f"{IDP}/.well-known/agent-card.json"  # A2A 0.3 well-known path, also the ATH agent_id
REPORTS = Path(os.environ.get("ATH_REPORT_DIR") or ROOT / ".check-results" / "ath") / os.environ.get("ATH_REPORT", os.environ.get("ATH_SDK", "official"))
REPORTS.mkdir(parents=True, exist_ok=True)
results, timings, procs = [], {}, []


def record(name, ok, detail=""):
    results.append({"check": name, "pass": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def wait_http(url, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def raw(method, url, body=None, headers=None):
    """One HTTP exchange without following redirects."""
    u = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=60)
    path = u.path + ("?" + u.query if u.query else "")
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    return r.status, dict(r.getheaders()), data


# ---------------------------------------------------------------- processes
VICTIM_ID = f"{IDP}/victim/.well-known/agent-card.json"
ATTACKER_ID = f"{IDP}/attacker/.well-known/agent-card.json"


def start_identity_server(doc, victim_doc, attacker_doc):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            docs = {urllib.parse.urlsplit(AGENT_ID).path: doc, urllib.parse.urlsplit(VICTIM_ID).path: victim_doc,
                    urllib.parse.urlsplit(ATTACKER_ID).path: attacker_doc}
            if self.path in docs:
                b = json.dumps(docs[self.path], ensure_ascii=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
            else:
                self.send_response(404); self.end_headers()

        def log_message(self, *a):
            pass
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 8094), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def start_provider():
    (STATE / "audit.jsonl").unlink(missing_ok=True)  # one audit trail per run
    env = dict(os.environ, DATAMIND_URL=DM, ATH_GATEWAY_URL=GW, DATAMIND_ATH_STATE=str(STATE))
    p = subprocess.Popen([sys.executable, "-m", "ath_adapter.provider"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    procs.append(p)
    assert wait_http(f"{PROV}/health"), "provider did not start"
    return json.loads((STATE / "clients.json").read_text())["ath-gateway"]["client_secret"]


def start_gateway(client_secret):
    cfg = STATE / "gateway"
    cfg.mkdir(parents=True, exist_ok=True)
    providers = {"datamind": {
        "display_name": "COSMO-DataMind",
        # app-side policy: agents may read; starting a build is NOT offered to agents by this deployment
        "available_scopes": ["datamind:catalog.read", "datamind:ontology.read", "datamind:sparql.query", "datamind:build.read"],
        "authorize_endpoint": f"{PROV}/authorize", "token_endpoint": f"{PROV}/token", "api_base_url": PROV,
        "client_id": "ath-gateway", "client_secret": client_secret}}
    (cfg / "providers.json").write_text(json.dumps(providers, indent=1))
    os.chmod(cfg / "providers.json", 0o600)
    env = dict(os.environ, ATH_CONFIG_DIR=str(cfg), ATH_PORT="3000", ATH_GATEWAY_HOST=GW,
               ATH_JWT_SECRET=os.urandom(24).hex(), ATH_GATEWAY_SECRET=os.urandom(24).hex())
    env.pop("ATH_SKIP_ATTESTATION_VERIFICATION", None)
    log = open(STATE / "gateway.log", "w")
    p = subprocess.Popen([str(GATEWAY_DIR / "node_modules/.bin/tsx"), "src/server.ts"], cwd=GATEWAY_DIR, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    procs.append(p)
    assert wait_http(f"{GW}/health"), "gateway did not start"
    time.sleep(0.5)
    m = re.search(r"username: root\s+password: (\S+)", (STATE / "gateway.log").read_text())
    return m.group(1) if m else None


def gateway_login(password):
    req = urllib.request.Request(f"{GW}/auth/login", data=json.dumps({"username": "root", "password": password}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["token"]


# ---------------------------------------------------------------- the user-side consent (steps 6-7)
def user_consents(authorization_url, grant=None, decision="approve"):
    """The DataMind user opens the consent page, signs in and approves (possibly narrowing scopes)."""
    st, _, page = raw("GET", authorization_url)
    assert st == 200, f"consent page {st}: {page[:200]}"
    form = {html.unescape(k): html.unescape(v) for k, v in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page.decode())}
    creds = json.loads((STATE / "initial_password.json").read_text())
    fields = list(form.items()) + [("username", creds["user"]), ("password", creds["password"]), ("decision", decision)]
    for s in (grant if grant is not None else form["scope"].split()):
        fields.append(("grant", s))
    st, hdr, _ = raw("POST", f"{PROV}/authorize/approve", urllib.parse.urlencode(fields), {"Content-Type": "application/x-www-form-urlencoded"})
    assert st in (301, 302, 303), f"approve returned {st}"
    callback = hdr.get("Location")
    st, hdr, body = raw("GET", callback)  # browser follows to the gateway's /ath/callback
    return callback, st, hdr.get("Location"), body


def direct_token(scopes, source="demo"):
    """Adapter-only OAuth flow (authorization code + PKCE) as the gateway's OAuth client, without the ATH gateway.
    Used for capabilities this deployment's app-side policy does not offer to agents through the gateway
    (starting builds, asking questions).  Returns the access token, or None when the adapter refuses."""
    import base64, hashlib, secrets  # noqa: E401
    secret = json.loads((STATE / "clients.json").read_text())["ath-gateway"]["client_secret"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    cb = f"{GW}/ath/callback"
    q = {"response_type": "code", "client_id": "ath-gateway", "redirect_uri": cb, "scope": " ".join(scopes),
         "state": "direct", "code_challenge": challenge, "code_challenge_method": "S256"}
    st, _, page = raw("GET", f"{PROV}/authorize?" + urllib.parse.urlencode(q))
    assert st == 200, f"consent page {st}"
    form = {html.unescape(k): html.unescape(v) for k, v in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page.decode())}
    creds = json.loads((STATE / "initial_password.json").read_text())
    fields = list(form.items()) + [("username", creds["user"]), ("password", creds["password"]), ("decision", "approve"), ("source", source)]
    fields += [("grant", x) for x in scopes]
    st, hdr, _ = raw("POST", f"{PROV}/authorize/approve", urllib.parse.urlencode(fields), {"Content-Type": "application/x-www-form-urlencoded"})
    if st not in (301, 302, 303):
        return None
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(hdr.get("Location")).query).get("code", [""])[0]
    st, _, body = raw("POST", f"{PROV}/token", urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code, "redirect_uri": cb, "client_id": "ath-gateway",
        "client_secret": secret, "code_verifier": verifier}), {"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(body)["access_token"] if st == 200 else None


def prov(method, path, token, body=None):
    """One call to the adapter with a bearer token; returns (status, parsed JSON or None)."""
    st, _, data = raw(method, PROV + path, json.dumps(body).encode() if body is not None else None,
                      {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        return st, json.loads(data)
    except ValueError:
        return st, None


def mcp_tool(token, name, args, rid=1):
    st, d = prov("POST", "/mcp", token, {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name, "arguments": args}})
    text = ((d or {}).get("result") or {}).get("content", [{}])[0].get("text", "") if st == 200 else json.dumps(d, ensure_ascii=False)
    return st, text


def wait_job(token, job, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, text = mcp_tool(token, "wait_build", {"job_id": job, "timeout": 60}, rid=40)   # a new MCP process per call
        if "构建进行中" not in text:
            return text
    return "超时：构建仍在进行"


def new_capability_checks(c):
    """Checks added with DR-097/101/103/104: source binding, question answering, server-held builds, audit link,
    and that an agent-triggered rebuild keeps human review decisions."""
    def dm(method, path, body=None):
        st, _, data = raw(method, DM + path, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"})
        return st, json.loads(data)

    # ---- data source bound to the token
    plain = c.proxy("datamind", "GET", "/api/tables")
    forced = c.proxy("datamind", "GET", "/api/tables?src=uploads")
    record("数据来源：调用方自带的来源参数被丢弃，结果仍来自令牌绑定的来源",
           isinstance(plain, list) and [t.get("name") for t in forced] == [t.get("name") for t in plain], f"{len(plain)} 张表")
    record("数据来源：授权页提交未列出的来源被拒", direct_token(["datamind:catalog.read"], source="conn_not_listed") is None)

    # ---- question answering needs its own scope
    try:
        c.proxy("datamind", "POST", "/api/chat", {"q": "各月收入趋势"})
        record("问数：令牌无 qa.ask，经网关提问被拒", False)
    except A.ATHClientError as e:
        record("问数：令牌无 qa.ask，经网关提问被拒", e.status == 403, f"{e.status}")
    tok = direct_token(["datamind:qa.ask", "datamind:build.run", "datamind:build.read"])
    st, ans = prov("POST", "/api/chat", tok, {"q": "各月收入趋势"})
    record("问数：有 qa.ask 的令牌得到答案，响应标明是否为引擎未响应时的内置模板结果",
           st == 200 and bool(ans.get("results")) and isinstance(ans.get("degraded", False), bool),
           f"结果 {len(ans.get('results') or [])} 组；degraded={ans.get('degraded', False)}")

    # ---- builds are held by the server and pinned to the token's source
    st, text = mcp_tool(tok, "start_build", {"q": "构建销售与生产本体", "source": "uploads"})
    record("构建：令牌绑定来源之外的数据来源被拒", st == 403, f"{st}")
    st, text = mcp_tool(tok, "start_build", {"q": "构建销售与生产本体", "name": "ATH端到端"})
    job = (re.search(r"job_id=(bj_[0-9a-f]{10})", text) or [None, ""])[1]
    final = wait_job(tok, job) if job else text
    key = (re.search(r"key=(built_\w+)", final) or [None, ""])[1]
    record("构建：经 MCP 发起后发起进程即结束，之后凭作业号在新进程中查到构建完成", bool(job and key), final.splitlines()[0][:150])

    # ---- one request id in both audit trails
    mine = [json.loads(x) for x in (STATE / "audit.jsonl").read_text().splitlines() if '"mcp.allowed"' in x and job and job in x]
    st, aud = dm("GET", "/api/ont/audit/" + key) if key else (0, {})
    built = ((aud.get("builds") or [{}])[-1]).get("initiated_by") or {}
    record("审计贯通：同一请求号与作业号出现在适配层审计日志和 DataMind 的图谱构建记录中",
           bool(mine) and built.get("request_id") == mine[-1].get("request_id") and built.get("job_id") == job
           and str(built.get("actor", "")).startswith("ath:"), json.dumps(built, ensure_ascii=False))

    # ---- an agent-triggered rebuild keeps human review decisions
    st, rv = dm("GET", "/api/ont/review?graph=" + key) if key else (0, {})
    row = next((r for r in rv.get("rows") or [] if r.get("status") == "candidate" and not r.get("human_review")), None)
    if not row:
        record("人审保值：智能体发起继续构建后，人工确认过的关系仍在图谱中且保持人工结论", False, "构建产物中没有可供确认的候选关系")
        return
    target = f"rel:{row['s']}->{row['t']}"
    dm("POST", "/api/ont/apply", {"graph": key, "reviewer": "e2e-reviewer",
                                  "op": {"op": "confirm_relation", "target": target, "reason": "端到端测试：业务确认"}})
    st, text = mcp_tool(tok, "start_build", {"q": "补充生产相关对象", "base_graph": key})
    job2 = (re.search(r"job_id=(bj_[0-9a-f]{10})", text) or [None, ""])[1]
    final2 = wait_job(tok, job2) if job2 else text
    st, rv2 = dm("GET", "/api/ont/review?graph=" + key)
    kept = next((r for r in rv2.get("rows") or [] if r["s"] == row["s"] and r["t"] == row["t"]), None)
    record("人审保值：智能体发起继续构建后，人工确认过的关系仍在图谱中且保持人工结论",
           "构建完成" in final2 and bool(kept) and kept.get("human_review") == "approved" and kept.get("status") in ("asserted", "verified")
           and not rv2.get("replay_failed"),
           f"{target} → {(kept or {}).get('status')} / {(kept or {}).get('human_review')}；回放失败 {len(rv2.get('replay_failed') or [])} 条")


def handshake(client, scopes, grant=None):
    t = {}
    t0 = time.time(); auth = client.authorize("datamind", scopes); t["authorize_ms"] = 1000 * (time.time() - t0)
    t0 = time.time(); cb, _, _, _ = user_consents(auth["authorization_url"], grant); t["consent_ms"] = 1000 * (time.time() - t0)
    code = urllib.parse.parse_qs(urllib.parse.urlsplit(cb).query).get("code", [""])[0]
    t0 = time.time(); tok = client.exchange_token(code, auth["ath_session_id"]); t["token_ms"] = 1000 * (time.time() - t0)
    return auth, tok, t


def main():
    key = A.new_key()
    card = A.identity_document(
        AGENT_ID, "数据助手智能体", "代表业务用户查询 DataMind 数据目录、本体与构建结果", IDP,
        [{"id": "catalog", "name": "数据目录查询", "description": "读取库表与业务术语", "tags": ["data"]},
         {"id": "ontology", "name": "本体与指标查询", "description": "读取本体图谱、指标与构建验收结果", "tags": ["ontology"]}],
        key, provider={"organization": ORGANIZATION, "url": "https://github.com/cosmoplat-opensource"})
    victim_key, attacker = A.new_key(), A.new_key()
    victim_card = A.identity_document(VICTIM_ID, "另一家企业的智能体", "未在本网关注册", IDP, [], victim_key)
    attacker_card = A.identity_document(ATTACKER_ID, "攻击方智能体", "持有合法身份文档和私钥", IDP, [], attacker)
    start_identity_server(card, victim_card, attacker_card)
    client_secret = start_provider()
    root_pw = start_gateway(client_secret)
    assert root_pw, "gateway root password not found in state/gateway.log"
    user_token = gateway_login(root_pw)
    c = Client(GW, AGENT_ID, key, user_token=user_token)

    # ---- discovery and A2A identity
    disc = c.discover()
    record("发现：/.well-known/ath.json 列出 DataMind", any(p.get("provider_id") == "datamind" for p in disc.get("supported_providers", [])), json.dumps(disc.get("supported_providers"), ensure_ascii=False)[:160])
    card_got = json.loads(urllib.request.urlopen(AGENT_ID).read())
    record("身份：agent_id 指向 A2A AgentCard，且含 P-256 公钥", bool(card_got.get("skills")) and card_got.get("protocolVersion") == "0.3.0" and card_got.get("public_key", {}).get("crv") == "P-256")

    # ---- phase A: app-side authorisation (registration with signed attestation)
    reg = c.register({"name": ORGANIZATION, "id": "datamind-e2e"},
                     [{"provider_id": "datamind", "scopes": ["datamind:catalog.read", "datamind:ontology.read", "datamind:sparql.query", "datamind:build.read", "datamind:build.run"]}],
                     "代表业务用户查询 DataMind 数据目录与本体")
    ap = reg["approved_providers"][0]
    record("应用侧授权：代理证明验签通过，批准 4 项只读范围，拒绝 build.run",
           "datamind:build.run" in ap.get("denied_scopes", []) and len(ap["approved_scopes"]) == 4, f"approved={ap['approved_scopes']} denied={ap.get('denied_scopes')}")

    # ---- phase B: user-side authorisation; the user withholds sparql.query
    requested = ["datamind:catalog.read", "datamind:ontology.read", "datamind:sparql.query", "datamind:build.read"]
    auth, tok, t = handshake(c, requested, grant=["datamind:catalog.read", "datamind:ontology.read", "datamind:build.read"])
    timings["first_handshake"] = t
    eff = sorted(tok["effective_scopes"])
    record("用户侧授权与范围交集：用户收窄后，有效范围 = 代理已批准 ∩ 用户已同意 ∩ 已请求",
           eff == sorted(["datamind:catalog.read", "datamind:ontology.read", "datamind:build.read"]), json.dumps(tok["scope_intersection"], ensure_ascii=False))

    # ---- business calls through the gateway (REST)
    info = c.proxy("datamind", "GET", "/api/sysinfo")
    record("业务调用：经网关读取 DataMind 系统信息", info.get("health") == "ok", f"tables={info.get('tables')}")
    tables = c.proxy("datamind", "GET", "/api/tables")
    record("业务调用：经网关读取库表目录", isinstance(tables, list) and len(tables) > 0, f"{len(tables)} 张表")
    graphs = c.proxy("datamind", "GET", "/api/graphs")
    record("业务调用：经网关读取本体图谱列表", isinstance(graphs, list), f"{len(graphs) if isinstance(graphs, list) else '-'} 个图谱")
    try:
        c.proxy("datamind", "POST", "/api/sparql", {"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"})
        record("最小权限：用户未授予 sparql.query，SPARQL 查询被拒", False)
    except A.ATHClientError as e:
        record("最小权限：用户未授予 sparql.query，SPARQL 查询被拒", e.status == 403, f"{e.status} {e.details}")

    # ---- MCP over ATH (cross-protocol)
    tl = c.mcp("datamind", "tools/list")
    names = [x["name"] for x in tl.get("result", {}).get("tools", [])]
    record("MCP：tools/list 经 ATH 令牌鉴权，返回 DataMind 构建工具", "start_build" in names and "list_built_graphs" in names, ",".join(names))
    built = c.mcp("datamind", "tools/call", {"name": "list_built_graphs", "arguments": {}}, rid=2)
    record("MCP：tools/call list_built_graphs 成功", "result" in built and not built["result"].get("isError"), str(built.get("result", {}).get("content", [{}])[0].get("text", ""))[:120])
    try:
        c.mcp("datamind", "tools/call", {"name": "start_build", "arguments": {}}, rid=3)
        record("MCP：令牌无 build.run，调用 start_build 被拒", False)
    except A.ATHClientError as e:
        record("MCP：令牌无 build.run，调用 start_build 被拒", e.status == 403, f"{e.status}")

    # ---- A2A over ATH: the caller agent sends A2A message/send to the DataMind agent with the same token
    dm_card = json.loads(urllib.request.urlopen(f"{PROV}/.well-known/agent-card.json").read())
    record("A2A：DataMind 发布 AgentCard，入口指向 ATH 网关代理，声明 bearer 鉴权",
           dm_card.get("securitySchemes", {}).get("ath", {}).get("scheme") == "bearer" and dm_card.get("url") == f"{GW}/ath/proxy/datamind/a2a",
           ",".join(s["id"] for s in dm_card["skills"]))

    def a2a(client, skill, rid):
        return client.proxy("datamind", "POST", "/a2a", {"jsonrpc": "2.0", "id": rid, "method": "message/send", "params": {"message": {
            "kind": "message", "role": "user", "messageId": f"m{rid}", "parts": [{"kind": "text", "text": "请列出"}, {"kind": "data", "data": {"skill": skill}}]}}})
    t1 = a2a(c, "catalog.tables", 11)
    record("A2A：message/send（catalog.tables）返回已完成任务", t1.get("result", {}).get("status", {}).get("state") == "completed", f"{t1['result']['artifacts'][0]['parts'][0]['data'].get('count')} 张表")
    t2 = a2a(c, "build.list", 12)
    record("A2A：message/send（build.list）返回已完成任务，该技能内部调用 MCP 工具", t2.get("result", {}).get("status", {}).get("state") == "completed", t2["result"]["artifacts"][0]["parts"][0]["data"].get("summary", "")[:80])

    new_capability_checks(c)

    # ---- negative tests on the handshake
    forged = Client(GW, AGENT_ID, attacker, user_token=user_token)
    try:
        forged.register({"name": "x", "id": "x"}, [{"provider_id": "datamind", "scopes": ["datamind:catalog.read"]}], "forged")
        record("安全：冒用已注册的 agent_id、以他人密钥签名注册，被拒", False)
    except A.ATHClientError as e:
        record("安全：冒用已注册的 agent_id、以他人密钥签名注册，被拒", e.status == 401 and "INVALID_ATTESTATION" in json.dumps(e.details), f"{e.status} {e.code}")
    impostor = Client(GW, VICTIM_ID, attacker, user_token=user_token)
    try:
        impostor.register({"name": "x", "id": "x"}, [{"provider_id": "datamind", "scopes": ["datamind:catalog.read"]}], "impersonation")
        record("安全：冒用他人尚未注册的 agent_id 注册，被拒", False, "注册成功（201）")
    except A.ATHClientError as e:
        record("安全：冒用他人尚未注册的 agent_id 注册，被拒", e.status == 401 and "INVALID_ATTESTATION" in json.dumps(e.details), f"{e.status} {e.code}")
    own = Client(GW, ATTACKER_ID, attacker, user_token=user_token).attest()  # valid signature for the attacker's own id
    st, _, body = raw("POST", f"{GW}/ath/agents/register", json.dumps({
        "agent_id": VICTIM_ID, "agent_attestation": own, "developer": {"name": "x", "id": "x"},
        "requested_providers": [{"provider_id": "datamind", "scopes": ["datamind:catalog.read"]}], "purpose": "impersonation",
        "redirect_uris": [f"{GW}/ath/callback"]}), {"Content-Type": "application/json", "X-ATH-User-Token": user_token})
    record("安全：用自己的合法身份签名、在请求中填写他人 agent_id 注册，被拒", st == 401, f"{st} {json.loads(body or b'{}').get('code', '')}")
    try:
        c.authorize("datamind", ["datamind:build.run"])
        record("安全：申请应用侧未批准的范围，被拒", False)
    except A.ATHClientError as e:
        record("安全：申请应用侧未批准的范围，被拒", "SCOPE_NOT_APPROVED" in json.dumps(e.details) or e.code == "SCOPE_NOT_APPROVED", e.code)
    a2 = c.authorize("datamind", ["datamind:catalog.read"])
    replay = c.attest()
    st1, _, _ = raw("POST", f"{GW}/ath/authorize", json.dumps({"client_id": c.client_id, "agent_attestation": replay, "provider_id": "datamind", "scopes": ["datamind:catalog.read"], "user_redirect_uri": f"{GW}/ath/callback", "state": "s1"}), {"Content-Type": "application/json", "X-ATH-User-Token": user_token})
    st2, _, _ = raw("POST", f"{GW}/ath/authorize", json.dumps({"client_id": c.client_id, "agent_attestation": replay, "provider_id": "datamind", "scopes": ["datamind:catalog.read"], "user_redirect_uri": f"{GW}/ath/callback", "state": "s2"}), {"Content-Type": "application/json", "X-ATH-User-Token": user_token})
    record("安全：重放同一代理证明（相同 jti），被拒", st1 == 200 and st2 == 401, f"first={st1} replay={st2}")
    user_consents(a2["authorization_url"], decision="deny")
    try:
        c.exchange_token("", a2["ath_session_id"])
        record("用户侧授权：用户拒绝后无法换取令牌", False)
    except A.ATHClientError as e:
        record("用户侧授权：用户拒绝后无法换取令牌", e.code == "USER_DENIED" or "USER_DENIED" in json.dumps(e.details), e.code)
    try:
        c._request("GET", "/ath/proxy/datamind/api/sysinfo", headers={"Authorization": f"Bearer {c.token}", "X-ATH-Agent-ID": "https://other-agent.example.com/agent.json"})
        record("安全：令牌配其他智能体身份使用，被拒", False)
    except A.ATHClientError as e:
        record("安全：令牌配其他智能体身份使用，被拒", e.status == 403, e.code)
    try:
        c._request("GET", "/ath/proxy/datamind/api/sysinfo", headers={"Authorization": f"Bearer {c.token}"})
        record("安全：令牌不带 X-ATH-Agent-ID 请求头使用，被拒", False, "返回 200")
    except A.ATHClientError as e:
        record("安全：令牌不带 X-ATH-Agent-ID 请求头使用，被拒", e.status == 403, e.code)
    try:
        c.proxy("github", "GET", "/user")
        record("安全：令牌用于其他服务提供方，被拒", False)
    except A.ATHClientError as e:
        record("安全：令牌用于其他服务提供方，被拒", e.code == "PROVIDER_MISMATCH", e.code)
    st, _, _ = raw("POST", f"{PROV}/token", urllib.parse.urlencode({"grant_type": "authorization_code", "code": "x", "redirect_uri": f"{GW}/ath/callback", "client_id": "ath-gateway", "client_secret": "wrong", "code_verifier": "v"}), {"Content-Type": "application/x-www-form-urlencoded"})
    record("适配层：授权服务拒绝错误的客户端凭据", st == 401, f"{st}")
    st, _, _ = raw("GET", f"{PROV}/authorize?" + urllib.parse.urlencode({"response_type": "code", "client_id": "ath-gateway", "redirect_uri": "http://evil.example.com/cb", "scope": "datamind:catalog.read", "code_challenge": "x", "code_challenge_method": "S256", "state": "s"}))
    record("适配层：授权服务拒绝未登记的回调地址", st == 400, f"{st}")
    st, _, _ = raw("GET", f"{PROV}/api/sysinfo")
    record("适配层：不经网关、无令牌直接访问资源接口，被拒", st == 401, f"{st}")

    # ---- latency (measurements, not pass/fail checks); each handshake grants catalog.read only
    lat, cc = [], None
    for _ in range(10):
        cc = Client(GW, AGENT_ID, key, user_token=user_token)
        cc.client_id, cc.client_secret = c.client_id, c.client_secret
        t0 = time.time(); handshake(cc, ["datamind:catalog.read"]); lat.append(1000 * (time.time() - t0))
    timings["handshake_ms"] = {"n": len(lat), "median": statistics.median(lat), "min": min(lat), "max": max(lat)}
    direct, via = [], []
    for _ in range(30):
        t0 = time.time(); urllib.request.urlopen(f"{DM}/api/sysinfo").read(); direct.append(1000 * (time.time() - t0))
        t0 = time.time(); c.proxy("datamind", "GET", "/api/sysinfo"); via.append(1000 * (time.time() - t0))
    timings["sysinfo_direct_ms"] = statistics.median(direct)
    timings["sysinfo_via_gateway_ms"] = statistics.median(via)
    try:
        a2a(cc, "build.list", 13)
        record("A2A：令牌只有 catalog.read，调用 build.list 技能被拒", False)
    except A.ATHClientError as e:
        record("A2A：令牌只有 catalog.read，调用 build.list 技能被拒", e.status == 403, f"{e.status}")

    # ---- revocation
    c.revoke()
    c.token = tok["access_token"]
    try:
        c.proxy("datamind", "GET", "/api/sysinfo")
        record("撤销：撤销后 REST 调用被拒", False)
    except A.ATHClientError as e:
        record("撤销：撤销后 REST 调用被拒", e.code == "TOKEN_REVOKED", e.code)
    try:
        c.mcp("datamind", "tools/list", rid=98)
        record("撤销：撤销后 MCP 调用被拒", False)
    except A.ATHClientError as e:
        record("撤销：撤销后 MCP 调用被拒", e.code == "TOKEN_REVOKED", e.code)
    try:
        a2a(c, "catalog.tables", 99)
        record("撤销：撤销后 A2A 调用被拒", False)
    except A.ATHClientError as e:
        record("撤销：撤销后 A2A 调用被拒", e.code == "TOKEN_REVOKED", e.code)

    # ---- audit trail
    audit = [json.loads(l) for l in (STATE / "audit.jsonl").read_text().splitlines()]
    kinds = {}
    for a in audit:
        kinds[a["event"]] = kinds.get(a["event"], 0) + 1
    record("审计：适配层日志记录授权同意、令牌签发和每次放行、拒绝决定",
           all(kinds.get(k, 0) > 0 for k in ("consent.approved", "consent.denied", "token.issued", "resource.allowed", "resource.denied", "mcp.allowed", "mcp.denied", "a2a.allowed", "a2a.denied")),
           json.dumps(kinds, ensure_ascii=False))

    report = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "agent_id": AGENT_ID, "agent_sdk": SDK, "gateway_commit": subprocess.run(["git", "-C", str(GATEWAY_DIR), "log", "-1", "--format=%h"], capture_output=True, text=True).stdout.strip(),
              "results": results, "timings": timings, "audit_event_counts": kinds,
              "passed": sum(r["pass"] for r in results), "total": len(results)}
    (REPORTS / "e2e_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    sdk = "ATH 官方 Python SDK（ath-sdk 0.1.0，加 user_token 参数）" if SDK == "official" else "自研 ath_client"
    md = [f"# ATH × COSMO-DataMind 端到端测试结果\n\n生成时间 {report['generated']}；网关提交 {report['gateway_commit']}；智能体侧 {sdk}；通过 {report['passed']}/{report['total']}。\n",
          "| # | 检查项 | 结果 | 说明 |", "|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        md.append(f"| {i} | {r['check']} | {'通过' if r['pass'] else '未通过'} | {str(r['detail']).replace('|', '/').replace(chr(10), ' ')[:180]} |")
    md += ["", "## 耗时（测量值，不计入检查项）", "",
           f"- 用户侧授权完整握手（授权请求、用户同意、换取令牌），{timings['handshake_ms']['n']} 次：中位 {timings['handshake_ms']['median']:.1f} ms，最小 {timings['handshake_ms']['min']:.1f} ms，最大 {timings['handshake_ms']['max']:.1f} ms",
           f"- GET /api/sysinfo 30 次中位：直连 DataMind {timings['sysinfo_direct_ms']:.1f} ms；经网关和适配层 {timings['sysinfo_via_gateway_ms']:.1f} ms"]
    (REPORTS / "e2e_report.md").write_text("\n".join(md) + "\n")
    print(f"\n{report['passed']}/{report['total']} checks passed; report at {REPORTS}")


if __name__ == "__main__":
    try:
        main()
    finally:
        import signal
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGTERM)  # tsx forks a node child; stop the whole group
            except ProcessLookupError:
                pass
            p.wait(timeout=10)

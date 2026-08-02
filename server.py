#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cosmo DataMind · 数据智脑 — 自有品牌的数据治理+本体+深度问数原型(原创前后端)
整合:demo_metrics.db(真实数据) + 上游本体引擎(引擎/技能/IR) + outputs(成果库)
深度问数:hermes/claude-code(经 agent_runtime)生成 SQL 计划 → 本地 SQLite 执行 → 洞察;引擎不可用时走内置模板兜底。
启动:python3 server.py  → http://127.0.0.1:8092
"""
import json, os, re, sqlite3, subprocess, threading, time, uuid, sys, glob, importlib
import urllib.request
import requests as _rq
from flask import Flask, jsonify, request, send_from_directory, send_file

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ── 可配置路径:全部支持环境变量覆盖,便于部署时外置数据与引擎 ──
#   DATAMIND_DB          只读 SQLite 数据底座(必需;缺失时相关端点如实报错)
#   DATAMIND_ENGINE_DIR  上游本体引擎目录(可选;缺失则 LLM 构建降级为纯数据驱动)
#   DATAMIND_OUTPUTS_DIR 成果库目录(可选)
#   DATAMIND_HOST/PORT   监听地址与端口(默认仅本机 127.0.0.1:8092)
DB       = os.environ.get("DATAMIND_DB",          os.path.join(ROOT, "demo_metrics.db"))
PLATFORM = os.environ.get("DATAMIND_ENGINE_DIR",  os.path.join(ROOT, "ontology-engine"))
OUTPUTS  = os.environ.get("DATAMIND_OUTPUTS_DIR", os.path.join(ROOT, "outputs"))
UPLOAD_DB = os.path.join(HERE, "workdir", "uploads.db")
WORK = os.path.join(HERE, "workdir"); os.makedirs(WORK, exist_ok=True)
sys.path.insert(0, os.path.join(PLATFORM, "engine"))

# ── 上游引擎缺失时的降级垫片 ──────────────────────────────────────────
# 本仓库不含上游本体引擎(agent_runtime 由 DATAMIND_ENGINE_DIR 提供)。
# 未配置时注册一个同名空实现,使所有 `from agent_runtime import ...` 的调用点
# 都能导入成功并如实得到「无可用运行时」——而不是抛 ModuleNotFoundError 让端点 500。
# available() 返回空列表后,各处 `if drv not in available(): continue` 会自然跳过,
# 深度问数/构建随之走内置模板与纯数据驱动的兜底路径,行为与引擎离线时一致。
try:
    import agent_runtime as _ar                      # noqa: F401
    ENGINE_AVAILABLE = True
    try:                                             # DR-029:注册 OpenAI 兼容驱动
        import openai_runtime                        # 未配置端点则不注册,不制造"看似可用"
        openai_runtime.register(_ar)
    except Exception:
        pass
except Exception:
    import types as _types
    ENGINE_AVAILABLE = False
    _stub = _types.ModuleType("agent_runtime")
    _stub.__doc__ = "fallback shim — 未配置 DATAMIND_ENGINE_DIR"
    _stub.available = lambda: []
    def _no_runtime(*_a, **_k):
        raise RuntimeError("未配置上游本体引擎:请设置环境变量 DATAMIND_ENGINE_DIR "
                           "指向引擎目录;或使用纯数据驱动的构建路径(quick_build)。")
    _stub.get_runtime = _no_runtime
    sys.modules["agent_runtime"] = _stub

app = Flask(__name__, static_folder=None)

# ── CSRF 防护:阻止恶意网页跨站触发本机写/执行接口(deploy/build/skill/删除等)──
# 浏览器跨源写请求必带 Origin;同源 UI 的 Origin 即本机,放行。非浏览器工具(无 Origin/Referer)不在威胁模型内。
from urllib.parse import urlparse as _urlparse
@app.before_request
def _csrf_guard():
    if request.method in ("GET", "HEAD", "OPTIONS"): return
    origin = request.headers.get("Origin") or request.headers.get("Referer")
    if not origin: return                       # curl/requests 等无源,非 CSRF 面
    host = _urlparse(origin).netloc
    if host and host != request.host:
        return jsonify({"error": "跨站请求被拒绝(CSRF 防护)"}), 403

# ── IR 加载(本体图谱源:示例 数据本体 / 应用本体 / 构建产物)──
IR_SOURCES = {
    # workdir 为持久权威副本(优先);/tmp 仅作后备(重启即失效,且可能是旧版)
    "demo": {"name": "示例企业数据本体(数据驱动)", "paths": [os.path.join(WORK, "demo_ir.json"), "/tmp/demo_ir.json"]},
    "app":  {"name": "示例应用本体(概念+流程)", "paths": [os.path.join(WORK, "app_ontology_ir.json"), "/tmp/appont_flow/app_ontology_ir.json"]},
    "cq":   {"name": "生产制造本体(上游引擎构建)", "paths": [os.path.join(PLATFORM, "web", "ontology-data.js")]},
}
def _load_json(paths):
    for p in paths if isinstance(paths, list) else [paths]:
        if p and os.path.exists(p):
            try:
                txt = open(p).read()
                if p.endswith(".js"):
                    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
                    return json.loads(m.group(1)) if m else None
                return json.loads(txt)
            except Exception: pass
    return None
_GKEY = re.compile(r"^[A-Za-z0-9_.\-]+$")
def _bad_gkey(key):
    """图谱键合法性:仅字母数字下划线点连字符,且不含 '..' —— 防 forged_../.. 之类路径穿越读/写任意文件"""
    return (not isinstance(key, str)) or (not _GKEY.match(key)) or (".." in key)
def load_ir(key):
    if _bad_gkey(key): return None
    if key.startswith("built_"):
        return _load_json(os.path.join(WORK, key + ".json"))
    if key.startswith("forged_"):
        return _load_json(os.path.join(PLATFORM, "data", "forged", key[7:] + ".json"))
    return _load_json(IR_SOURCES.get(key, {}).get("paths", []))

# ── IOF/BFO 2020 接地(借鉴 Industrial Ontology Foundry:上层范畴 + 有根据关系 + 时间指标)──
# kind → BFO 2020 上层范畴(供互操作;object 缺省物质实体,event 缺省过程)
_KIND_BFO = {"object": "MaterialEntity", "event": "Process",
             "asset": "MaterialArtifact", "role": "Role", "ice": "InformationContentEntity"}
# 自由中文动词 → IOF Core 有根据关系(BFO),附时间指标 atAllTimes(始终成立)/atSomeTime(某时成立)
_FOUNDED_RELATIONS = {
    "归属": ("continuantPartOfAtAllTimes", "atAllTimes"),
    "包含": ("hasContinuantPartAtAllTimes", "atAllTimes"),
    "组成": ("hasContinuantPartAtAllTimes", "atAllTimes"),
    "产生": ("hasSpecifiedOutput", "atSomeTime"),
    "输出": ("hasSpecifiedOutput", "atSomeTime"),
    "触发": ("hasInput", "atSomeTime"),
    "输入": ("hasInput", "atSomeTime"),
    "服务": ("hasParticipantAtSomeTime", "atSomeTime"),
    "参与": ("hasParticipantAtSomeTime", "atSomeTime"),
    "承载": ("bearerOfAtSomeTime", "atSomeTime"),
    "实现": ("realizes", "atSomeTime"),
    "描述": ("describes", "atSomeTime"),
}
def _ground_verb(verb):
    """把中文关系动词接地到 BFO 有根据关系(互操作 + 时间指标);未知动词回退 relatedToAtSomeTime。"""
    v = (verb or "").strip()
    if v in _FOUNDED_RELATIONS: return _FOUNDED_RELATIONS[v]
    for k, val in _FOUNDED_RELATIONS.items():
        if k in v: return val
    return ("relatedToAtSomeTime", "atSomeTime")

def _iof_node_ann(o):
    """透传 IOF-AV 注释字段到图节点(缺失则从 kind 推 BFO 范畴);老 IR 无这些字段时优雅降级。"""
    kind = o.get("kind", "object")
    return {"bfo": o.get("bfo") or _KIND_BFO.get(kind, "Continuant"),
            "definition": o.get("definition", ""), "isPrimitive": o.get("isPrimitive"),
            "example": o.get("example", ""), "counterExample": o.get("counterExample", ""),
            "provenance": o.get("provenance"), "maturity": o.get("maturity", "")}
def _iof_edge_ann(r, verb):
    """透传/现算边的 BFO 有根据关系与时间指标(老 IR 无 founded_relation 时按动词回退接地)。"""
    fr, tq = r.get("founded_relation"), r.get("temporal")
    if not fr: fr, tq = _ground_verb(verb)
    return {"founded_relation": fr, "temporal": tq or "atSomeTime", "semantic": r.get("semantic", "")}

def ir_to_graph(key, ir):
    """统一成 {nodes:[{id,name,kind,...IOF-AV}], edges:[{s,t,verb,status,founded_relation,temporal}]}"""
    nodes, edges = [], []
    if not isinstance(ir, dict): return {"nodes": nodes, "edges": edges}
    if "relations" in ir and "objects" in ir and ir["objects"] and "kind" in ir["objects"][0]:
        # 应用本体 IR:节点显示名优先用中文名(cn),id 仍用 name 以保证边引用稳定
        for o in ir["objects"]:
            nodes.append({"id": o["name"], "name": o.get("cn") or o["name"], "kind": o.get("kind", "object"),
                          "candidate": bool(o.get("candidate", False)),
                          "tables": o.get("tables", []), "indicators": o.get("indicators", []), "fields": o.get("field_count", 0),
                          **_iof_node_ann(o)})
        for r in ir["relations"]:
            if r.get("source_concept") is None or r.get("target_concept") is None: continue
            if r.get("status") == "rejected": continue          # 人审否决的关系不再渲染(评审页可见、可恢复)
            verb = r.get("verb", "关联")
            edges.append({"s": r["source_concept"], "t": r["target_concept"], "verb": verb, "status": r.get("status", ""),
                          "overlap": r.get("overlap"), "human_review": r.get("human_review", ""),
                          **_iof_edge_ann(r, verb)})
    else:
        # 示例 数据 IR(DR-001)
        for o in ir.get("objects", []):
            nodes.append({"id": o["id"], "name": o.get("cn") or o.get("name"), "kind": o.get("kind", "object"),
                          "candidate": bool(o.get("candidate", False)),
                          "tables": [o.get("table")], "indicators": o.get("supported_metrics", []), "fields": o.get("attr_count", len(o.get("attrs", []))),
                          **_iof_node_ann(o)})
        for l in ir.get("links", []):
            if l.get("source") is None or l.get("target") is None: continue
            if l.get("status") == "rejected": continue           # 人审否决的关系不再渲染(评审页可见、可恢复)
            verb = l.get("verb", "关联")
            edges.append({"s": l["source"], "t": l["target"], "verb": verb, "status": l.get("status", ""),
                          "overlap": l.get("overlap_pct", l.get("overlap")), "human_review": l.get("human_review", ""),
                          **_iof_edge_ann(l, verb)})
    return {"nodes": nodes, "edges": edges}

# ── SQLite 工具(只读查询)──
def ro_connect(path):
    """统一只读连接:mode=ro 打开;缺库时响亮失败(不静默新建空库,防丢库被掩盖)。
    仅当 URI 不受支持时才退回普通连接,且仍先确认文件存在 + 强制 query_only。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据库不存在: {path}")
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        con = sqlite3.connect(path)  # 极端情况(URI 不支持)退回普通连接,但库已确认存在,不会误建
        try: con.execute("PRAGMA query_only=ON")
        except Exception: pass
        return con

def q(sql, db=None, limit=500, attach_uploads=False):
    # 以只读模式打开(mode=ro):即便 SQL 含写操作,引擎层也会拒绝,杜绝改/删库
    path = db or DB
    con = ro_connect(path)
    con.row_factory = sqlite3.Row
    # 深度问数带上传数据时:只读挂载 uploads.db,上传表可作 up.<表> 查询
    if attach_uploads and os.path.exists(UPLOAD_DB):
        try: con.execute(f"ATTACH DATABASE 'file:{UPLOAD_DB}?mode=ro' AS up")
        except Exception: pass
    # 语句级超时:防笛卡尔积/慢查询拖垮进程(约 8s 后中断)
    deadline = [time.time() + 8.0]
    con.set_progress_handler(lambda: 1 if time.time() > deadline[0] else 0, 10000)
    try:
        cur = con.execute(sql)
        rows = [dict(r) for r in cur.fetchmany(limit)]
        return {"columns": [c[0] for c in cur.description or []], "rows": rows}
    finally: con.close()
# 只放行纯查询:允许 select / with,但 with 之后若出现 DML/DDL 关键字则拒绝
SAFE_SQL = re.compile(r"^\s*(select|with)\b", re.I)
_SQL_WRITE = re.compile(r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|pragma|vacuum|reindex|truncate)\b", re.I)
def sql_is_readonly(sql):
    s = sql or ""
    if not SAFE_SQL.match(s): return False
    # select 开头天然安全;with 开头需排除内嵌写语句(WITH cte AS(...) DELETE ...)
    if re.match(r"^\s*with\b", s, re.I) and _SQL_WRITE.search(s): return False
    return True

# 复用 driver 实例:get_runtime 每次返回新实例,会重置 _started/_primed,使会话式对话(稳定 cid)
# 的多轮续接失效。按 driver 缓存一份,让 hermes/claude-code 的多轮语境/续接生效。
_RT_CACHE = {}
def runtime_cached(drv):
    from agent_runtime import get_runtime
    if drv not in _RT_CACHE:
        _RT_CACHE[drv] = get_runtime(drv)
    return _RT_CACHE[drv]

def _drv_order(cands=("openai", "hermes", "claude-code")):
    # LLM 引擎尝试顺序遵循 CLAW_DRIVER:选中的排最前(其余按原序回退),
    # 使 /api/ont/runtime 的引擎切换对所有 LLM 流程真正生效(而非只改显示标签)。
    pref = os.environ.get("CLAW_DRIVER", "hermes")
    if pref in cands:
        return (pref,) + tuple(d for d in cands if d != pref)
    return tuple(cands)

def table_list(db=None):
    con = ro_connect(db or DB)
    tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = []
    for t in tabs:
        n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        cols = con.execute(f'PRAGMA table_info("{t}")').fetchall()
        out.append({"name": t, "rows": n, "cols": len(cols)})
    con.close(); return out

# ── 后台作业(技能运行/本体构建)──
JOBS = {}
_WRITE_LOCK = threading.RLock()   # 保护 json 文件读-改-写(edits/chats),防并发丢更新/损坏
# rdflib 的 SPARQL 解析器基于 pyparsing,其 packrat 缓存/语法状态为进程级全局且非线程安全:
# 多请求并发跑 SPARQL(或 SPARQL 与 pyshacl 内部 SPARQL 相撞)会污染语法,报出
# 『Expected SelectQuery, found OPTIONAL』『postParse2() missing arg』等假语法错。故串行化所有 SPARQL 语法操作。
_RDF_LOCK = threading.Lock()
def run_job(cmd, cwd=None, env=None, tag=""):
    jid = uuid.uuid4().hex[:8]
    logf = os.path.join(WORK, f"job_{jid}.log")
    if len(JOBS) > 200:                                   # 防无界增长:超 200 淘汰最早
        for k in list(JOBS)[:len(JOBS) - 200]: JOBS.pop(k, None)
    JOBS[jid] = {"id": jid, "tag": tag, "status": "running", "log": logf, "cmd": " ".join(cmd)[:200], "ts": time.time()}
    def _run():
        with open(logf, "w") as f:
            try:
                p = subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, stdout=f, stderr=subprocess.STDOUT, timeout=1800)
                JOBS[jid]["status"] = "done" if p.returncode == 0 else f"exit_{p.returncode}"
            except Exception as e:
                f.write(f"\nJOB ERROR: {e}"); JOBS[jid]["status"] = "error"
    threading.Thread(target=_run, daemon=True).start()
    return jid

def _bounded(fn, secs, default=None):
    """守护线程内跑 fn,最多等 secs 秒;超时则放弃并返回 default,保证外层请求永不卡死。"""
    v, _err, _to = _bounded_ex(fn, secs, default=default)
    return v

def _bounded_ex(fn, secs, default=None):
    """同 _bounded,但显式区分三种结局,返回 (value, error, timed_out):
    - 正常完成 → (返回值, None, False)
    - fn 抛异常 → (default, 异常对象, False)   ← 关键:异常不再被误报成『超时』
    - 超时未完成 → (default, None, True)
    调用方据此给出准确错误(异常 vs 超时),避免『瞬时失败却提示 8s 超时』的自相矛盾。"""
    box = {"v": default, "done": False, "err": None}
    def run():
        try: box["v"] = fn()
        except Exception as e: box["err"] = e
        box["done"] = True
    t = threading.Thread(target=run, daemon=True); t.start(); t.join(secs)
    if not box["done"]:
        return default, None, True
    return box["v"], box["err"], False

# ── 深度问数编排(hermes/claude-code → SQL 计划 → 本地执行 → 洞察)──
def _join_hints(ir, tables):
    """选中表之间的本体关系 → JOIN 提示行(⋈ 前缀;沿本体关系召回的实现)。
    只给 verified/asserted(人审断言)关系;键取关系证据里的 child_key/parent_key。"""
    tl = {str(t).lower() for t in tables if t}
    o2t = {o.get("id"): o.get("table") for o in ir.get("objects", [])}
    out = []
    for l in ir.get("links", []):
        if l.get("status") not in ("verified", "asserted"): continue
        st, tt = o2t.get(l.get("source")), o2t.get(l.get("target"))
        if not st or not tt or st.lower() not in tl or tt.lower() not in tl: continue
        ev = l.get("evidence") or {}
        ck, pk = ev.get("child_key"), ev.get("parent_key")
        key = f"{st}.{ck} = {tt}.{pk}" if (ck and pk) else f"{st} 关联 {tt}(键见列名)"
        out.append(f"⋈ {key}  [{l.get('verb','关联')} · {l.get('status')}]")
        if len(out) >= 12: break
    # 两跳路径召回(DR-022):选中表间无直接关系、但经一张中间表可达 → 给出完整 JOIN 链。
    # 「累计产量最高的产线」这类跨两跳聚合,缺路径提示时引擎最易自造错误 JOIN。
    if len(out) < 12:
        adj = {}                                     # table → [(邻表, 本端键, 邻端键)]
        for l in ir.get("links", []):
            if l.get("status") not in ("verified", "asserted"): continue
            ev = l.get("evidence") or {}
            ck, pk = ev.get("child_key"), ev.get("parent_key")
            st, tt2 = o2t.get(l.get("source")), o2t.get(l.get("target"))
            if not (ck and pk and st and tt2): continue
            adj.setdefault(st.lower(), []).append((tt2.lower(), ck, pk, st, tt2))
            adj.setdefault(tt2.lower(), []).append((st.lower(), pk, ck, tt2, st))
        tl_list = sorted(tl)
        added = 0
        for i, a in enumerate(tl_list):
            for b in tl_list[i + 1:]:
                if added >= 3: break
                # 已有直接提示则跳过
                if any(a in h.lower() and b in h.lower() for h in out): continue
                hit = None
                for (m, k1, k2, at, mt1) in adj.get(a, []):
                    if m == b: hit = None; break     # 有直连(未入 out 因非选中态),不补链
                    for (b2, k3, k4, mt2, bt) in adj.get(m, []):
                        if b2 == b and m not in tl:
                            hit = (at, k1, mt1, k2, k3, bt, k4); break
                    if hit: break
                if hit:
                    at, k1, mtab, k2, k3, bt, k4 = hit
                    out.append(f"⋈⋈ {at}.{k1} = {mtab}.{k2} ∧ {mtab}.{k3} = {bt}.{k4}(经中间表 {mtab},两跳链)")
                    added += 1
            if added >= 3: break
    return out

_REG_CACHE = {"win": None, "win_ts": 0, "base": {}, "base_ts": 0}

def _data_window():
    """M4-a 数据时间窗:显式告知 LLM 业务数据截止到哪天。

    不告知的后果是确定的:LLM 会用 date('now','-3 month') 之类相对区间,而合成/历史库
    的数据往往早于今天,查询因此命中极少甚至为空(实测本库 214/4190 行),且随时间流逝
    只会更糟。窗口由确定性 SQL 探测,算不出则返回空串——不臆造。缓存 10 分钟。
    """
    if _REG_CACHE["win"] is not None and time.time() - _REG_CACHE["win_ts"] < 600:
        return _REG_CACHE["win"]
    win = ""
    for tbl, col in (("fact_sales_order", "order_date"), ("dws_production_daily", "date")):
        try:
            r = q(f'SELECT MIN("{col}") a, MAX("{col}") b FROM "{tbl}"', limit=1)
            row = (r.get("rows") or [{}])[0]
            if row.get("a") and row.get("b"):
                win = (f'数据时间窗: {row["a"]} ~ {row["b"]}(业务数据截止于此;'
                       f'问"最近N个月"请以 {row["b"]} 为基准倒推,不要用 date("now"),否则查空)')
                break
        except Exception:
            continue
    _REG_CACHE.update(win=win, win_ts=time.time())
    return win

def _metric_baselines(mets):
    """M4-b 指标统计基线:让 LLM 看到的不是一个列名,而是一个有取值范围与数据边界的业务量。

    对标 Trane M4(五法中唯一 100/100):在数据进入 AI 之前先做一次业务语义封装。此处封装
    全部由确定性 SQL 得出——与数据裁决同源,零幻觉;单个指标算不出就跳过,绝不猜数。
    口径说明:给出的是**观测区间**(实际数据的 min/max),不是工艺规范意义上的"正常范围"
    ——后者需要工艺标准输入,系统无从推导,不可用观测极值冒充(极值可能本就是异常样本)。
    负值/零值单独标注:它们通常是业务异常信号(如负毛利订单),LLM 若默认"毛利必为正"会写错过滤条件。
    """
    out = []
    for m in mets[:6]:
        tbl, col, nm = m.get("table"), m.get("col"), m.get("name")
        if not (tbl and col and nm): continue
        ck = f"{tbl}.{col}"
        if ck in _REG_CACHE["base"] and time.time() - _REG_CACHE["base_ts"] < 600:
            out.append(_REG_CACHE["base"][ck]); continue
        try:
            r = q(f'SELECT MIN("{col}") mn, MAX("{col}") mx, ROUND(AVG("{col}"),2) av, '
                  f'COUNT("{col}") n, SUM(CASE WHEN "{col}"<0 THEN 1 ELSE 0 END) neg '
                  f'FROM "{tbl}"', limit=1)
            row = (r.get("rows") or [{}])[0]
            if row.get("n") in (None, 0) or row.get("mn") is None: continue
            u = (m.get("unit") or "").strip()
            seg = (f'{nm}({ck}): 观测区间[{row["mn"]}, {row["mx"]}] 均值{row["av"]} 非空{row["n"]}行'
                   + (f' 单位{u}' if u else ""))
            if row.get("neg"): seg += f' 含{row["neg"]}行负值(业务异常,勿假设恒为正)'
            _REG_CACHE["base"][ck] = seg
            _REG_CACHE["base_ts"] = time.time()
            out.append(seg)
        except Exception:
            continue
    return out

_LAYER_SEMANTICS = {   # 数仓分层前缀的权威语义:LLM 若不知约定,会按通用语境猜表用途
    "ods": "贴源层(原始系统镜像)", "stg": "暂存层(清洗中间态)", "dim": "维度表(主数据/档案)",
    "dwd": "明细层(单据粒度)", "dws": "汇总层(按日或按维度预聚合)",
    "fact": "事实表(业务过程流水)", "agg": "聚合层(跨维汇总)", "up": "用户上传表",
}

def _glossary_block(picked):
    """M5 领域词汇表注入:把本次上下文涉及的分层前缀语义与歧义缩写的权威译法钉进 prompt。

    工业缩写在不同语境含义完全不同(OA 在暖通=新风量,在办公语境=办公自动化);不锚定时
    LLM 只能用『大概率正确』的通用解释填空——在本系统里可能是致命误解。表名与列名的中文
    由 IR 自带(已人工校订),此处只补两类 IR 里没有的语义:分层约定 + 短缩写词元。
    """
    prefixes, out = set(), []
    for o in picked:
        t = (o.get("table") or "").lower()
        p = t.split("_", 1)[0]
        if p in _LAYER_SEMANTICS: prefixes.add(p)
    if prefixes:
        out.append("分层约定: " + "; ".join(f"{p}_* = {_LAYER_SEMANTICS[p]}" for p in sorted(prefixes)))
    # 只锚定"出现在本次列名里 + 短到易歧义(≤4 字符)"的词元,避免把 434 条词表全灌进上下文
    try:
        import translate_cn as T
        tok = getattr(T, "TOKEN", {}) or {}
        blob = " ".join((a.get("col") or "").lower() for o in picked for a in o.get("attrs", []))
        hits = [(en, cn) for en, cn in tok.items()
                if en and cn and len(en) <= 4 and re.search(r"(^|_)" + re.escape(en.lower()) + r"($|_)", blob)]
        if hits:
            out.append("缩写锚定(本系统内的确定含义,勿按通用语境另作解释): "
                       + "; ".join(f"{en}={cn}" for en, cn in sorted(hits)[:20]))
    except Exception:
        pass
    return out

def build_context(question, focus_tables=None):
    """从 IR 挑相关表/列/指标,组紧凑 schema 上下文;focus_tables 非空时优先/限定这些表(对应『数据源』选择)"""
    ir = load_ir_edited("demo") or {}
    mets = []
    for k, arr in (ir.get("metric_layers") or {}).items():
        for m in arr: mets.append({"name": m.get("name"), "table": m.get("table"), "col": m.get("value_col"), "layer": k, "unit": m.get("unit") or ""})
    kws = [w for w in re.split(r"[,，。？?\s]+", question) if w]
    kws += expand_terms(question)          # A1 术语扩展:词典同义/中英互补词并入匹配
    def score(txt): return sum(1 for w in kws if w and w in txt)
    ft = set(t.lower() for t in (focus_tables or []))
    if ft:   # 用户在『数据源』里选了具体表 → 只喂这些表(仿平台按选定数据源限定)
        picked = [o for o in ir.get("objects", []) if o.get("table", "").lower() in ft]
        if picked:
            lines = []
            for o in picked:
                cols = ", ".join(f'{a["col"]}({a.get("cn","")})' for a in o.get("attrs", [])[:18])
                _al = "、".join(o.get("aliases") or [])
                lines.append(f'表 {o["table"]}({o.get("cn","")}{",业务别称:" + _al if _al else ""}): {cols}')
            jh = _join_hints(ir, [o.get("table") for o in picked])
            if jh: lines.append("表间关系(本体已验证,JOIN 优先用这些键):\n" + "\n".join(jh))
            up = _uploads_schema()
            if up: lines.append("上传数据(作 up.<表> 查询): " + up)
            dw = _data_window()                       # M4-a 数据时间窗
            if dw: lines.append(dw)
            lines += _glossary_block(picked)          # M5 词汇表注入
            return "\n".join(lines)
    tabs = []
    for o in ir.get("objects", []):
        # DR-027:别名并入评分语料——业务用语("产量")与表名中文("生产日汇总")常常不同,
        # 不认别名会让问数召回不到正确的表,进而生成查错表的 SQL
        blob = ((o.get("cn") or "") + o.get("table", "") + "".join(o.get("aliases") or [])
                + " ".join(a.get("cn", "") + a.get("col", "") for a in o.get("attrs", [])))
        tabs.append((score(blob), o))
    tabs.sort(key=lambda x: -x[0])
    picked = [o for s, o in tabs[:8] if s > 0] or [o for _, o in tabs[:5]]
    core = {"fact_sales_order", "fact_production_output", "dws_production_daily"}   # 核心事实表始终入上下文
    have = {o["table"].lower() for o in picked}
    for o in ir.get("objects", []):
        if o.get("table", "").lower() in core and o["table"].lower() not in have:
            picked.append(o)
    # 沿本体关系召回:命中表的一跳邻居(维表等)拉进上下文,JOIN 才有另一端
    have = {o["table"].lower() for o in picked if o.get("table")}
    o_by_id = {o.get("id"): o for o in ir.get("objects", [])}
    extras = []
    for l in ir.get("links", []):
        if l.get("status") not in ("verified", "asserted"): continue
        so, to = o_by_id.get(l.get("source")), o_by_id.get(l.get("target"))
        if not so or not to: continue
        st, tt = (so.get("table") or "").lower(), (to.get("table") or "").lower()
        if st in have and tt and tt not in have and len(extras) < 4:
            extras.append(to); have.add(tt)
        elif tt in have and st and st not in have and len(extras) < 4:
            extras.append(so); have.add(st)
    picked += extras
    lines = []
    for o in picked:
        cols = ", ".join(f'{a["col"]}({a.get("cn","")})' for a in o.get("attrs", [])[:18])
        lines.append(f'表 {o["table"]}({o.get("cn","")}): {cols}')
    jh = _join_hints(ir, [o.get("table") for o in picked])
    if jh: lines.append("表间关系(本体已验证,JOIN 优先用这些键):\n" + "\n".join(jh))
    hit_m = [m for m in mets if score(m["name"] or "")][:10]
    if hit_m:
        lines.append("相关指标: " + "; ".join(f'{m["name"]}←{m["table"]}.{m["col"]}' for m in hit_m))
        bl = _metric_baselines(hit_m)                 # M4-b 指标统计基线(确定性 SQL)
        if bl: lines.append("指标基线(真实数据算出,勿自行假设量级):\n" + "\n".join("  " + b for b in bl))
    up = _uploads_schema()
    if up: lines.append("上传数据(作 up.<表> 查询): " + up)
    dw = _data_window()                              # M4-a 数据时间窗
    if dw: lines.append(dw)
    lines += _glossary_block(picked)                  # M5 词汇表注入
    return "\n".join(lines)

def _uploads_schema():
    """uploads.db 各表列结构(供深度问数带上传文件时喂进上下文,SQL 用 up.<表> 引用)"""
    if not os.path.exists(UPLOAD_DB): return ""
    try:
        con = sqlite3.connect(f"file:{UPLOAD_DB}?mode=ro", uri=True)
        tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        parts = []
        for t in tabs[:8]:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')][:20]
            parts.append(f'up.{t}({", ".join(cols)})')
        con.close(); return "; ".join(parts)
    except Exception:
        return ""

def _eng_label(drv):
    """对外中性引擎名:执行记录里不暴露底层多智能体库(hermes/claude-code/openclaw)"""
    return {"hermes": "智能引擎", "claude-code": "智能引擎(备选)", "claude": "智能引擎(备选)",
            "openclaw": "经典引擎"}.get(drv, "智能引擎")

# ── 深度问数增强(DR-019):术语扩展 / 口径拦截 / 口径卡 / 指代延续 / 按任务选模 ──
def _task_model(task):
    """B4 按任务选模:engine_config.task_models[task] 非空则单次覆盖模型(claude=--model, hermes=-m)。"""
    try: return ((_load_engine_cfg().get("task_models") or {}).get(task) or "").strip() or None
    except Exception: return None

def _model_fits(drv, m):
    """任务模型须与运行时同族:claude 系模型只喂 claude-code,其余喂 hermes/openclaw。
    防止引擎切到 hermes 后,任务覆盖里的 claude 模型名被塞给 hermes -m 而全线报错。"""
    lm = (m or "").lower()
    is_claude = lm.startswith("claude") or lm in ("opus", "sonnet", "haiku")
    return is_claude if drv == "claude-code" else not is_claude

def _llm_turn(rt, sid, prompt, timeout, task=None):
    """统一 LLM 调用:带任务级模型覆盖(仅当模型与运行时同族);不支持 model 参数则自动回落。→ (ok, reply)"""
    m = _task_model(task) if task else None
    if m and not _model_fits(getattr(rt, "name", ""), m):
        m = None
    if m:
        try: return rt.run_turn(sid, prompt, timeout=timeout, model=m)
        except TypeError: pass
    return rt.run_turn(sid, prompt, timeout=timeout)

_GLOSS_CACHE = {"ts": 0.0, "pairs": []}
def _gloss_pairs():
    """术语管理词典(translate_cn 四张词表)→ (en, cn) 对;5 分钟缓存。"""
    if time.time() - _GLOSS_CACHE["ts"] < 300 and _GLOSS_CACHE["pairs"]:
        return _GLOSS_CACHE["pairs"]
    pairs = []
    try:
        import translate_cn as T
        for src in (getattr(T, "TABLE_PHRASE", {}), getattr(T, "COL_PHRASE", {}),
                    getattr(T, "PHRASE", {}), getattr(T, "TOKEN", {})):
            for en, cn in src.items():
                if en and cn: pairs.append((str(en), str(cn)))
    except Exception:
        pass
    _GLOSS_CACHE.update(ts=time.time(), pairs=pairs)
    return pairs

def expand_terms(question):
    """A1 术语扩展检索:问句命中词典中文 → 补英文同义词(反之亦然),提升表/列匹配召回。
    只扩展长度≥2 的中文 / ≥3 的英文,防单字噪声;上限 12,防上下文爆炸。"""
    ql = (question or "").lower()
    out, seen = [], set()
    for en, cn in _gloss_pairs():
        if len(out) >= 12: break
        w = None
        if len(cn) >= 2 and cn in question and en.lower() not in ql: w = en
        elif len(en) >= 3 and en.lower() in ql and cn not in question: w = cn
        if w and w not in seen:
            seen.add(w); out.append(w)
    return out

_SQL_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_SQL_KW = {"on", "where", "group", "order", "left", "right", "inner", "outer", "cross",
           "join", "select", "limit", "using", "as", "union", "having", "with"}
def _validate_sql_ontology(sql, ir):
    """A2 口径拦截(P8『口径错了直接拦截』落地):SQL 执行前对照本体校验。
    ① 表白名单:FROM/JOIN 的表必须在本体/数据目录/上传库(up.)/CTE 内 —— 拦臆造表名;
    ② JOIN 键校验:ON a.x=b.y 两侧列名不同时,该键对必须落在本体 verified/asserted 关系
      的 child/parent 键上(同名键等值 JOIN 放行,列名本身即口径)—— 拦自造 JOIN。
    → (ok, reason)"""
    s = sql or ""
    ctes = {m.group(1).lower() for m in re.finditer(r"(?:\bwith|,)\s*(%s)\s+as\s*\(" % _SQL_IDENT, s, re.I)}
    known = {(o.get("table") or "").lower() for o in ir.get("objects", []) if o.get("table")}
    try: known |= {t["name"].lower() for t in table_list()}
    except Exception: pass
    up = set()
    try: up = {x.split("(")[0][3:].lower() for x in _uploads_schema().split("; ") if x.startswith("up.")}
    except Exception: pass
    alias, used = {}, []
    for m in re.finditer(r"\b(?:from|join)\s+(up\.)?(%s)(?:\s+(?:as\s+)?(%s))?" % (_SQL_IDENT, _SQL_IDENT), s, re.I):
        pre, t, al = m.group(1), m.group(2).lower(), (m.group(3) or "").lower()
        if al in _SQL_KW: al = ""
        full = ("up." + t) if pre else t
        used.append(full)
        alias[al or t] = full
    for full in used:
        base = full[3:] if full.startswith("up.") else full
        if full.startswith("up."):
            if base not in up: return False, f"上传表 {full} 不存在"
        elif base not in known and base not in ctes:
            return False, f"表 {base} 不在本体/数据目录中(疑似臆造表名)"
    o2t = {o.get("id"): (o.get("table") or "").lower() for o in ir.get("objects", [])}
    relkeys = set()
    for l in ir.get("links", []):
        if l.get("status") not in ("verified", "asserted"): continue
        ev = l.get("evidence") or {}
        ck, pk = (ev.get("child_key") or "").lower(), (ev.get("parent_key") or "").lower()
        st, tt = o2t.get(l.get("source")), o2t.get(l.get("target"))
        if ck and pk and st and tt:
            relkeys.add((st, ck, tt, pk)); relkeys.add((tt, pk, st, ck))
    for m in re.finditer(r"\bon\s+(%s)\.(%s)\s*=\s*(%s)\.(%s)" % ((_SQL_IDENT,) * 4), s, re.I):
        a, ca, b, cb = (m.group(i).lower() for i in (1, 2, 3, 4))
        ta, tb = alias.get(a, a), alias.get(b, b)
        if ca == cb: continue
        if ta in ctes or tb in ctes or ta.startswith("up.") or tb.startswith("up."): continue
        if (ta, ca, tb, cb) not in relkeys:
            return False, f"JOIN 键 {ta}.{ca}={tb}.{cb} 不在本体已验证关系上(口径未证实,已拦截)"
    return True, ""

_METRIC_LAYER_CN = {"atomic": "原子指标", "derived": "派生指标", "composite": "复合指标"}
def _metric_cards(question, ir):
    """A3 口径卡:问句命中的指标 → 名称/分层/口径说明/数据出处(表.列)/单位,随答案展示。"""
    cards = []
    for k, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            n = (m.get("name") or "").strip()
            if len(n) >= 2 and n in question:
                cards.append({"name": n, "layer": _METRIC_LAYER_CN.get(k, k),
                              "table": m.get("table") or "", "col": m.get("value_col") or "",
                              "unit": m.get("unit") or "", "desc": m.get("desc") or ""})
    return cards[:4]

_PRONOUN = re.compile(r"它|这个|该|上述|同样|这些|其中|再看|还有|呢[??]?$")
def _carryover(question, history, ir):
    """B5 多轮指代:问句含指代词(或极短追问)时,把上文命中的本体对象显式延续进本问。
    本体即实体注册表 —— 不做通用 NLP,只在图谱对象里找。→ (增强问句, 延续对象串)"""
    if not history: return question, ""
    if not (_PRONOUN.search(question) or len(question) <= 12): return question, ""
    prev = " ".join(h.get("q", "") for h in history[-2:])
    hits = []
    for o in ir.get("objects", []):
        cn = (o.get("cn") or "").strip()
        cands = {cn, o.get("table") or ""}
        for suf in ("事实表", "维度表", "汇总表", "明细表", "表"):   # 「销售订单事实表」也按「销售订单」匹配
            if cn.endswith(suf) and len(cn) > len(suf) + 1: cands.add(cn[:-len(suf)])
        for c in cands:
            if c and len(c) >= 2 and c in prev:
                nm = cn or o.get("table")
                if nm and nm not in hits: hits.append(nm)
                break
        if len(hits) >= 3: break
    if not hits: return question, ""
    return question + "(指代延续:上文对象 " + "、".join(hits) + ")", "、".join(hits)

def agent_sql_plan(question, context, steps):
    try:
        from agent_runtime import get_runtime, available
    except Exception as e:
        steps.append({"step": "agent_runtime", "ok": False, "info": str(e)[:100]}); return None
    prompt = f"""你是数据分析引擎。基于 SQLite 库(方言:SQLite,日期是 TEXT 'YYYY-MM-DD')回答业务问题。
问题: {question}
可用表结构:
{context}
只输出一个 JSON(不要其它文字): {{"analyses":[{{"title":"...","sql":"SELECT ...","chart":{{"type":"line|bar|pie","x":"列名","y":["列名"]}}}}], "note":"一句话分析思路"}}
要求: 最多3个分析;SQL 只用上面列出的表列;若上下文给出「表间关系(⋈)」,跨表 JOIN 必须优先使用这些键,不要自造 JOIN 条件;聚合趋势用 substr(日期列,1,7) 按月;LIMIT 500 以内;「⋈⋈」为两跳链,须完整沿链 JOIN 中间表;均值口径:『月均/日均』分母用 count(DISTINCT 期间),不得除以固定常数;『最…的』单值问题:JOIN 后聚合再 ORDER BY … LIMIT 1。"""
    for drv in _drv_order():
        try:
            if drv not in available(): continue
            t0 = time.time()
            rt = get_runtime(drv)
            ok, reply = _llm_turn(rt, f"dm_{uuid.uuid4().hex[:6]}", prompt, 60, task="plan")
            steps.append({"step": f"llm_plan({_eng_label(drv)})", "ok": bool(ok), "info": f"{time.time()-t0:.1f}s {len(reply or '')}字符"})
            if ok and reply:
                m = re.search(r"\{[\s\S]*\}", reply)
                if m:
                    try: return json.loads(m.group(0))
                    except Exception: pass
        except Exception as e:
            steps.append({"step": f"llm_plan({_eng_label(drv)})", "ok": False, "info": str(e)[:120]})
    return None

def fallback_plan(question):
    """无引擎时的内置模板,保证可用"""
    p = []
    if re.search(r"毛利|利润|margin", question):
        p.append({"title": "月度毛利与毛利率", "sql": "SELECT substr(order_date,1,7) 月, round(sum(gross_profit_actual)/10000,1) 毛利_万, round(sum(gross_profit_actual)*100.0/sum(amount),1) 毛利率_pct FROM fact_sales_order GROUP BY 1 ORDER BY 1", "chart": {"type": "line", "x": "月", "y": ["毛利_万", "毛利率_pct"]}})
    if re.search(r"收入|销售|营收", question):
        p.append({"title": "月度收入", "sql": "SELECT substr(order_date,1,7) 月, round(sum(amount)/10000,1) 收入_万 FROM fact_sales_order GROUP BY 1 ORDER BY 1", "chart": {"type": "bar", "x": "月", "y": ["收入_万"]}})
    if re.search(r"产量|生产", question):
        p.append({"title": "月度产量", "sql": "SELECT substr(output_date,1,7) 月, round(sum(quantity),0) 产量 FROM fact_production_output GROUP BY 1 ORDER BY 1", "chart": {"type": "line", "x": "月", "y": ["产量"]}})
    if not p:
        p.append({"title": "销售概览", "sql": "SELECT substr(order_date,1,7) 月, round(sum(amount)/10000,1) 收入_万, round(sum(gross_profit_actual)/10000,1) 毛利_万 FROM fact_sales_order GROUP BY 1 ORDER BY 1", "chart": {"type": "line", "x": "月", "y": ["收入_万", "毛利_万"]}})
    return {"analyses": p, "note": "内置模板(问数引擎离线兜底)"}

def _rule_summary(results):
    """规则化数据摘要(引擎离线/超时的兜底,始终基于真实数据,不编造)"""
    outs = []
    for r in results:
        rows = r["data"]["rows"]
        if rows and len(rows) >= 2:
            outs.append(f"「{r['title']}」共 {len(rows)} 期,末期 {json.dumps(rows[-1], ensure_ascii=False)}")
        elif rows:
            outs.append(f"「{r['title']}」{json.dumps(rows[0], ensure_ascii=False)}")
    return ("数据摘要:" + ";".join(outs)) if outs else "已取到数据,请展开各分析查看明细。"

_ERR_REPLY = re.compile(r"API call failed|HTTP (?:4\d\d|5\d\d)|usage limit|rate ?limit|quota|Traceback|exceeded|无法.*(连接|执行)|Error:", re.I)
def _looks_like_error(reply):
    """引擎有时把错误文案当正文返回(ok=True 但内容是 429/超限等);识别后视为失败,交由规则兜底。"""
    r = (reply or "").strip()
    return (not r) or (len(r) < 400 and bool(_ERR_REPLY.search(r)))

def narrative_llm(question, results, steps, emit=None):
    """引擎生成业务洞察;成功返回文本,失败/离线/引擎报错返回 None(由调用方兜底为 _rule_summary)。
    emit(片段) 非空且引擎支持流式(claude-code stream-json)时逐段回调 —— B6 叙述流式;
    其它引擎自动回落一次性(emit 收到整段)。"""
    from agent_runtime import get_runtime, available
    # 长序列取「首3 + 末7 行」而非只取前6——否则时序按月升序时 LLM 只看到最早几期、看不到最新期(如毛利率末期骤降),
    # 会写出与图表矛盾、且把最早几期误当"最近"的洞察。末尾即最新期,是趋势/异常的关键。
    def _samp(rows): return rows if len(rows) <= 10 else (rows[:3] + rows[-7:])
    data_brief = json.dumps([{"title": r["title"], "总行数": len(r["data"]["rows"]), "样本(首3+末7,末尾为最新期)": _samp(r["data"]["rows"])} for r in results], ensure_ascii=False)[:3200]
    prompt = f"问题: {question}\n各分析结果样本(长序列取首尾,末尾为最新期): {data_brief}\n用中文给出 3-5 句结论式业务洞察,**重点关注最新期(数据末尾)的变化与异常**(基于数据,不要编造),直接输出文本。"
    for drv in _drv_order():
        if drv not in available(): continue
        rt = get_runtime(drv)
        sid = f"dmn_{uuid.uuid4().hex[:6]}"
        if emit is not None and drv == "claude-code" and hasattr(rt, "stream_turn"):
            m = _task_model("narrative")
            def _push(ev):
                if ev.get("type") == "text" and ev.get("text"): emit(ev["text"])
            try:
                try: ok, reply = rt.stream_turn(_push, sid, prompt, timeout=45, model=m)
                except TypeError: ok, reply = rt.stream_turn(_push, sid, prompt, timeout=45)
            except Exception as e:
                ok, reply = False, str(e)
        else:
            ok, reply = _llm_turn(rt, sid, prompt, 45, task="narrative")
            if ok and reply and emit is not None and not _looks_like_error(reply): emit(reply.strip()[:1500])
        if ok and reply and not _looks_like_error(reply):
            steps.append({"step": f"llm_insight({_eng_label(drv)})", "ok": True, "info": f"{len(reply)}字符"})
            return reply.strip()[:1500]
        if reply and _looks_like_error(reply):
            steps.append({"step": f"llm_insight({_eng_label(drv)})", "ok": False, "info": f"引擎报错,跳过: {reply.strip()[:80]}"})
    return None

# ══════════ 路由 ══════════
@app.get("/")
def index(): return send_from_directory(os.path.join(HERE, "ui"), "index.html")

@app.get("/doc/<name>")
def doc(name):
    """服务 ui/ 下的文档页(根因分析等),仅限 .html,防穿越"""
    if not re.match(r"^[A-Za-z0-9_-]+$", name): return "bad", 400
    p = os.path.join(HERE, "ui", name + ".html")
    if not os.path.exists(p): return "not found", 404
    return send_file(p)

@app.get("/vendor/<path:f>")
def vendor(f):
    """本地静态库(echarts 等),不依赖外网 CDN"""
    base = os.path.join(HERE, "ui", "vendor")
    rp = os.path.realpath(os.path.join(base, f))
    if not rp.startswith(os.path.realpath(base) + os.sep) or not os.path.exists(rp): return "not found", 404
    return send_file(rp)

@app.get("/api/overview")
def overview():
    ir = load_ir_edited("demo") or {}
    ml = ir.get("metric_layers") or {}
    try:
        tabs = table_list()
    except Exception as e:
        # 优雅降级:库不可用时仍返回零值 KPI 让看板可渲染。用 warning 而非 error——
        # error 在全站约定中表示"请求失败",占用它会让前端守卫误判这次成功的降级响应。
        return jsonify({"warning": f"数据库不可用: {str(e)[:120]}", "kpi": {"tables": 0, "rows": 0, "metrics": 0, "objects": len(ir.get("objects", [])), "links": len(ir.get("links", []))}, "trend": [], "prod": []}), 200
    kpi = {"tables": len(tabs), "rows": sum(t["rows"] for t in tabs),
           "metrics": sum(len(v) for v in ml.values()),
           "objects": len(ir.get("objects", [])),
           "links": sum(1 for l in ir.get("links", []) if l.get("status") != "rejected")}
    def _safe(sql):
        try: return q(sql)["rows"]
        except Exception: return []
    trend = _safe("SELECT substr(order_date,1,7) m, round(sum(amount)/10000,1) rev, round(sum(gross_profit_actual)/10000,1) gp FROM fact_sales_order GROUP BY 1 ORDER BY 1")
    prod = _safe("SELECT substr(output_date,1,7) m, round(sum(quantity),0) qty FROM fact_production_output GROUP BY 1 ORDER BY 1")
    return jsonify({"kpi": kpi, "trend": trend, "prod": prod})

@app.get("/api/tables")
def tables(): return jsonify(table_list())

@app.get("/api/table/<name>")
def table_detail(name):
    if not re.match(r"^[A-Za-z0-9_]+$", name): return jsonify({"error": "bad name"}), 400
    ir = load_ir_edited("demo") or {}
    cn_map = {}
    for o in ir.get("objects", []):
        if o.get("table", "").lower() == name.lower():
            cn_map = {a["col"]: a.get("cn", "") for a in o.get("attrs", [])}
    con = ro_connect(DB)
    cols = [{"col": r[1], "type": r[2], "pk": bool(r[5]), "cn": cn_map.get(r[1], "")} for r in con.execute(f'PRAGMA table_info("{name}")')]
    con.close()
    if not cols: return jsonify({"error": "表不存在"}), 404
    prev = q(f'SELECT * FROM "{name}" LIMIT 20')
    return jsonify({"columns": cols, "preview": prev})

@app.get("/api/graphs")
def graphs():
    out = []
    for k, v in IR_SOURCES.items():
        ir = load_ir_edited(k)
        if ir:
            g = ir_to_graph(k, ir)
            out.append({"id": k, "name": v["name"], "nodes": len(g["nodes"]), "edges": len(g["edges"]), "cat": "curated"})
    for p2 in sorted(glob.glob(os.path.join(PLATFORM, "data", "forged", "*.json"))):
        k = "forged_" + os.path.basename(p2)[:-5]
        ir = load_ir_edited(k)
        if not isinstance(ir, dict): continue
        g = ir_to_graph(k, ir)
        out.append({"id": k, "name": ((ir.get("scenario") or {}).get("name") or k), "nodes": len(g["nodes"]), "edges": len(g["edges"]), "cat": "scenario"})
    for p in sorted(glob.glob(os.path.join(WORK, "built_*.json")), key=os.path.getmtime, reverse=True):
        k = os.path.basename(p)[:-5]
        ir = load_ir_edited(k); g = ir_to_graph(k, ir)
        out.append({"id": k, "name": (ir.get("scenario") or {}).get("name") or k, "nodes": len(g["nodes"]), "edges": len(g["edges"]), "cat": "built"})
    return jsonify(out)

@app.get("/api/graph/<key>")
def graph(key): return jsonify(ir_to_graph(key, load_ir_edited(key)))

@app.get("/api/ont/accuracy")
def ont_accuracy():
    """⑥ 可解释性第三问「历史上类似情况准确率?」:人审同意率(按系统原判 / 按动词)。

    工业AI 要取得工程师信任,每条输出须能答三问:基于什么数据(已有 provenance)、
    依据什么规则(已有关系接地)、**历史上类似情况准确率**(此端点)。
    样本不足时如实返回 insufficient,不给百分比。
    """
    return jsonify(_review_history(force=bool(request.args.get("force"))))

@app.get("/api/ont/rejected-patterns")
def ont_rejected_patterns():
    """人审否决过的关系模式(即注入构建提示的『已知误判模式』),供页面展示与审计。"""
    return jsonify({"patterns": _rejected_patterns(limit=int(request.args.get("limit") or 8))})

@app.get("/api/ont/review")
def ont_review():
    """人机协同人审队列:全部关系 + 状态/语义评审/取值重合/人审结论;待审 = 未人审的 candidate 或语义存疑"""
    key = request.args.get("graph", "demo")
    if _bad_gkey(key): return jsonify({"error": "bad key"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    rels, ks, kt = _rels(ir)
    disp = {}
    for o in ir.get("objects", []):
        oid = str(o.get("id") or o.get("name"))
        disp[oid] = o.get("cn") or o.get("name") or oid
    rows = []
    for l in rels:
        s, t = str(l.get(ks)), str(l.get(kt))
        st, sem, hr = l.get("status", ""), l.get("semantic", ""), l.get("human_review", "")
        rows.append({"s": s, "t": t, "sn": disp.get(s, s), "tn": disp.get(t, t),
                     "verb": l.get("verb", "关联"), "card": l.get("card", ""), "status": st,
                     "semantic": sem, "overlap": l.get("overlap", l.get("overlap_pct")),
                     "human_review": hr, "reason": l.get("review_reason", ""),
                     "by": l.get("review_by", ""), "time": l.get("review_time", ""),
                     "pending": (hr == "") and (st == "candidate" or sem == "fail")})
    rows.sort(key=lambda r: (not r["pending"], r["status"] == "rejected", r["s"]))
    objs = []
    for o in ir.get("objects", []):
        oid = str(o.get("id") or o.get("name"))
        cand = bool(o.get("candidate", False)) and not o.get("confirmed", False)
        objs.append({"id": oid, "name": o.get("cn") or o.get("name") or oid, "kind": o.get("kind", "object"),
                     "candidate": cand, "tables": [x for x in (o.get("tables") or [o.get("table")]) if x],
                     "by": o.get("review_by", ""), "reason": o.get("review_reason", ""), "time": o.get("review_time", "")})
    objs.sort(key=lambda x: (not x["candidate"], x["id"]))
    counts = {"total": len(rows), "pending": sum(1 for r in rows if r["pending"]),
              "approved": sum(1 for r in rows if r["human_review"] == "approved"),
              "rejected": sum(1 for r in rows if r["human_review"] == "rejected"),
              "disputed": sum(1 for r in rows if r["semantic"] == "fail"),
              "objects": len(objs), "obj_candidate": sum(1 for x in objs if x["candidate"])}
    return jsonify({"graph": key, "counts": counts, "rows": rows, "objects": objs})

@app.get("/api/metrics")
def metrics():
    ir = load_ir_edited("demo") or {}
    return jsonify(ir.get("metric_layers") or {})

@app.get("/api/metric/lineage")
def metric_lineage():
    name = request.args.get("name", "").strip()
    ir = load_ir_edited("demo") or {}
    hit = None
    for k, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            if m.get("name") == name:
                hit = {**m, "layer": k}; break
        if hit: break
    if not hit: return jsonify({"error": "指标不存在"}), 404
    refs = []
    app_ir = load_ir_edited("app") or {}
    for o in app_ir.get("objects", []):
        if name in (o.get("indicators") or []):
            refs.append({"object": o["name"], "kind": o.get("kind"), "tables": o.get("tables", [])})
    # 派生/复合的输入指标
    inputs = hit.get("inputs") or []
    return jsonify({"metric": hit, "source_table": hit.get("table"), "source_col": hit.get("value_col"),
                    "referenced_by": refs, "inputs": inputs, "formula": hit.get("formula", "")})

@app.get("/api/metric/quick")
def metric_quick():
    """即时问数:指标 → 自动生成月度聚合 SQL → 秒出数据(不走 LLM)"""
    name = request.args.get("name", "").strip()
    ir = load_ir_edited("demo") or {}
    hit = None
    for k, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            if m.get("name") == name: hit = {**m, "layer": k}; break
        if hit: break
    if not hit or not hit.get("table"): return jsonify({"error": "指标不存在或未绑表"}), 404
    tbl, vcol = hit["table"], hit.get("value_col")
    # 找该表日期列(IR attrs 中 DATE 类型优先,退而求 *date* 命名)
    dcol = None
    for o in ir.get("objects", []):
        if o.get("table", "").lower() == tbl.lower():
            dates = [a["col"] for a in o.get("attrs", []) if "DATE" in (a.get("type", "").upper())]
            named = [a["col"] for a in o.get("attrs", []) if "date" in a["col"].lower()]
            dcol = (dates or named or [None])[0]
    if not (vcol and dcol):
        return jsonify({"error": f"缺日期列或取值列(date={dcol}, value={vcol})"}), 400
    # 聚合口径:比率/百分比 → 平均(不可求和);存量/快照(余额/库存/在册/期末/人数)→ 月均(按日求和会 ~30x 高估);流量 → 求和
    mtype, unit = (hit.get("type") or ""), (hit.get("unit") or "")
    is_ratio = ("比率" in mtype) or ("占比" in mtype) or unit.strip() == "%" or bool(re.search(r"率|占比|比率|均", name))
    is_stock = bool(re.search(r"余额|库存|在册|期末|存量|头寸|人数|结存|在制", name))
    if is_ratio: agg, agg_note = "avg", "比率→月均(该列为逐行率值,取月度均值近似)"
    elif is_stock: agg, agg_note = "avg", "存量/快照→月均(按日求和会高估,故取均值)"
    else: agg, agg_note = "sum", "流量→月度求和"
    sql = f'SELECT substr("{dcol}",1,7) 月, round({agg}("{vcol}"),2) "{name}" FROM "{tbl}" GROUP BY 1 ORDER BY 1'
    try:
        data = q(sql)
        return jsonify({"metric": name, "unit": unit, "layer": hit["layer"],
                        "sql": sql, "agg": agg, "agg_note": agg_note, "source": f"{tbl}.{vcol}", "data": data})
    except Exception as e:
        return jsonify({"error": str(e), "sql": sql}), 400

@app.get("/api/table/<name>/info")
def table_info(name):
    """表基本信息:行/列 + 所属本体对象 + 支撑指标(iip 元数据详情范式)"""
    if not re.match(r"^[A-Za-z0-9_]+$", name): return jsonify({"error": "bad name"}), 400
    con = ro_connect(DB)
    try:
        rows = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        ncols = len(con.execute(f'PRAGMA table_info("{name}")').fetchall())
    except Exception:
        return jsonify({"error": "表不存在"}), 404
    finally:
        con.close()
    app_ir = load_ir_edited("app") or {}
    objs = [{"name": o["name"], "kind": o.get("kind")} for o in app_ir.get("objects", [])
            if name.lower() in [t.lower() for t in o.get("tables", [])]]
    ir = load_ir_edited("demo") or {}
    mets = [{"name": m.get("name"), "layer": k} for k, arr in (ir.get("metric_layers") or {}).items()
            for m in arr if (m.get("table") or "").lower() == name.lower()]
    return jsonify({"table": name, "rows": rows, "cols": ncols, "objects": objs, "metrics": mets})

# ══ M1: serve_claw 能力原生吸收(不依赖 8091) ══
def _edits_path(key):
    if _bad_gkey(key): key = "__invalid__"      # 非法键落到固定安全名,绝不逃逸 WORK 目录(防写穿越)
    return os.path.join(WORK, f"edits_{key}.json")
def _load_edits(key):
    return json.load(open(_edits_path(key))) if os.path.exists(_edits_path(key)) else {"version": 1, "ops": []}
REVIEW_OPS = ("confirm_relation", "reject_relation")   # 人机协同人审:通过(→asserted)/否决(→剔除)
LOCAL_OPS = ("set_alias",)     # DataMind 本地算子:业务别名(引擎白名单未含,不依赖引擎在线)

def _rels(ir):
    """关系列表 + 端点键名:兼容两种 IR 形状(示例 links[source/target] / 构建产物 relations[source_concept/target_concept])"""
    if "links" in ir: return ir["links"], "source", "target"
    return ir.setdefault("relations", []), "source_concept", "target_concept"

def _find_rel_any(ir, rid):
    m = re.match(r"^(.+?)->(.+)$", (rid or "").replace("rel:", "", 1))
    if not m: return None
    rels, ks, kt = _rels(ir)
    for l in rels:
        if str(l.get(ks)) == m.group(1) and str(l.get(kt)) == m.group(2): return l
    return None

def _find_obj_any(ir, oid):
    for o in ir.get("objects", []):
        if str(o.get("id") or o.get("name")) == str(oid): return o
    return None

def _stamp_review(x, op):
    """把评审人/意见/时间盖到元素上(op 内字段随编辑日志持久化,回放确定)"""
    reason = (op.get("reason") or "").strip()
    if reason: x["review_reason"] = reason
    if op.get("reviewer"): x["review_by"] = op["reviewer"]
    if op.get("ts"): x["review_time"] = op["ts"]

def apply_any(ir, op):
    """白名单编辑统一入口:关系类算子(人审通过/否决 + 动词/基数/增删)与对象确认/删除本地实现、
    两种 IR 形状通吃;其余算子(属性类等)沿用平台 apply_op。人审纪律:人只产生 asserted,永不冒充 verified(反造假)。"""
    kind = op.get("op"); t = op.get("target", "")
    params = op.get("params") or {}; reason = (op.get("reason") or "").strip()
    if kind == "confirm":                      # 确认候选对象(两种形状)
        o = _find_obj_any(ir, t.replace("obj:", "", 1))
        if not o: raise ValueError(f"对象不存在: {t}")
        o["confirmed"] = True
        if "candidate" in o: o["candidate"] = False
        _stamp_review(o, op)
        return
    if kind == "set_alias":                    # DR-027 业务别名:让业务用语可锚定到本体对象
        o = _find_obj_any(ir, t.replace("obj:", "", 1))
        if not o: raise ValueError(f"对象不存在: {t}")
        raw = params.get("aliases")
        if isinstance(raw, str): raw = [x for x in re.split(r"[,,、;;\s]+", raw) if x]
        if not isinstance(raw, list): raise ValueError("params.aliases 需为列表或分隔字符串")
        # 上限在去重前校验:否则「21 个相同别名」去重后剩 1 个而绕过限制,
        # 大批量输入即可绕开防线(去重是清洗,不是防线)
        if len(raw) > 20: raise ValueError("别名过多(上限 20)")
        seen, out = set(), []
        for a in raw:
            a = str(a).strip()[:40]
            # 与对象自身名称重复的别名无意义(锚定本就能命中),去重后丢弃
            if not a or a in seen or a in (o.get("cn"), o.get("name"), o.get("id"), o.get("table")):
                continue
            seen.add(a); out.append(a)
        o["aliases"] = out
        _stamp_review(o, op)
        return
    if kind == "remove_object":                # 删对象(两种形状),级联删其关系
        oid = t.replace("obj:", "", 1)
        o = _find_obj_any(ir, oid)
        if not o: raise ValueError(f"对象不存在: {t}")
        ir["objects"].remove(o)
        rels, ks, kt = _rels(ir)
        rels[:] = [l for l in rels if str(l.get(ks)) != str(oid) and str(l.get(kt)) != str(oid)]
        return
    if kind in ("confirm_relation", "reject_relation", "verb", "set_card", "remove_relation", "add_relation"):
        rels, ks, kt = _rels(ir)
        if kind == "add_relation":
            m = re.match(r"^(.+?)->(.+)$", t.replace("rel:", "", 1))
            if not m: raise ValueError("add_relation 目标格式: rel:<source>-><target>")
            src, dst = m.group(1), m.group(2)
            ids = {str(o.get("id") or o.get("name")) for o in ir.get("objects", [])}
            if src not in ids or dst not in ids: raise ValueError("源/目标对象不存在")
            if src == dst: raise ValueError("不允许自环关系")
            if _find_rel_any(ir, t): raise ValueError("该关系已存在")
            verb = (params.get("verb") or "关联").strip()
            if not verb or len(verb) > 12 or re.search(r"[<>\"'&]", verb): raise ValueError("verb 1~12字, 不含 < > \" ' &")
            card = params.get("card") if params.get("card") in ("1:N", "N:1", "1:1", "N:N") else "N:1"
            nl = {ks: src, kt: dst, "verb": verb, "card": card, "status": "asserted",
                  "human_review": "approved", "review_reason": reason or "人工新增"}
            _stamp_review(nl, op)
            rels.append(nl)
            return
        l = _find_rel_any(ir, t)
        if not l: raise ValueError(f"关系不存在: {t}(格式 rel:<source>-><target>)")
        if kind == "confirm_relation":     # 人审通过:candidate/gap/rejected → asserted;verified 只记通过、不动状态
            l.setdefault("review_prior_status", l.get("status"))   # 原判在此刻定格:改写 status 后就再也反推不出来
            if l.get("status") != "verified": l["status"] = "asserted"
            l["human_review"] = "approved"
            _stamp_review(l, op)
            return
        if kind == "reject_relation":      # 人审否决:标 rejected,渲染剔除;记录留痕,可撤销
            l.setdefault("review_prior_status", l.get("status"))   # 同上:先存原判,再改写
            l["status"] = "rejected"; l["human_review"] = "rejected"
            _stamp_review(l, op)
            return
        if kind == "verb":
            verb = (params.get("verb") or "").strip()
            if not verb or len(verb) > 12 or re.search(r"[<>\"'&]", verb): raise ValueError("verb 需要 1~12 字中文动词, 不含 < > \" ' &")
            l["verb"] = verb
            _stamp_review(l, op)
            return
        if kind == "set_card":
            card = params.get("card")
            if card not in ("1:N", "N:1", "1:1", "N:N"): raise ValueError("card 须为 1:N/N:1/1:1/N:N")
            l["card"] = card
            return
        if kind == "remove_relation":
            rels.remove(l)
            return
    import serve_claw as SC
    return SC.apply_op(ir, op)

def load_ir_edited(key):
    """IR + 编辑日志回放(草案层,不动构建产物;与 serve_claw 同机制)"""
    ir = load_ir(key)
    if not ir: return None
    ir = json.loads(json.dumps(ir))
    try:
        for op in _load_edits(key)["ops"]:
            try: apply_any(ir, op)
            except Exception: pass
    except Exception: pass
    return ir

@app.post("/api/ont/apply")
def ont_apply():
    """白名单编辑(confirm/rename/verb/add_*/remove_*/set_*),按图谱记操作日志,可撤销"""
    body = request.json or {}
    key, op = body.get("graph", "demo"), body.get("op") or {}
    _kind = op.get("op")
    try:
        import serve_claw as SC
        _allowed = tuple(SC.ALLOWED_OPS) + REVIEW_OPS + LOCAL_OPS
    except Exception as e:
        # 引擎缺失时仍放行本地算子:别名/人审是 DataMind 自有能力,不该被上游离线卡住
        if _kind not in (REVIEW_OPS + LOCAL_OPS):
            return jsonify({"error": f"编辑引擎未就绪: {str(e)[:120]}"}), 503
        _allowed = REVIEW_OPS + LOCAL_OPS
    if _kind not in _allowed:
        return jsonify({"error": f"非白名单操作: {_kind}"}), 400
    reviewer = (body.get("reviewer") or op.get("reviewer") or "").strip()[:40]
    if reviewer: op["reviewer"] = reviewer
    op.setdefault("ts", time.strftime("%Y-%m-%d %H:%M"))
    # DR-027 审计来源:chat=对话建议被人采纳 / review=评审台人工发起 / api=外部直调。
    # 必须可区分——「AI 提的被采纳」与「人自己决定的」责任归属不同,审计要分得开。
    src = (body.get("source") or op.get("source") or "api").strip()[:20]
    op["source"] = src if src in ("chat", "review", "graph", "api") else "api"
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try: apply_any(ir, op)
    except Exception as e: return jsonify({"error": f"应用失败: {e}"}), 400
    with _WRITE_LOCK:
        ed = _load_edits(key); ed["ops"].append(op)
        _atomic_json(_edits_path(key), ed)
        n = len(ed["ops"])
    return jsonify({"ok": True, "ops": n})

@app.post("/api/ont/undo")
def ont_undo():
    key = (request.json or {}).get("graph", "demo")
    with _WRITE_LOCK:
        ed = _load_edits(key)
        if not ed["ops"]: return jsonify({"error": "无可撤销操作"}), 400
        last = ed["ops"].pop()
        _atomic_json(_edits_path(key), ed)
        n = len(ed["ops"])
    return jsonify({"ok": True, "undone": last, "ops": n})

@app.get("/api/ont/edits")
def ont_edits():
    return jsonify(_load_edits(request.args.get("graph", "demo")))

FORGED_DIR = os.path.join(PLATFORM, "data", "forged")
@app.get("/api/ont/forged")
def ont_forged():
    out = []
    for p2 in sorted(glob.glob(os.path.join(FORGED_DIR, "*.json"))):
        try:
            d = json.load(open(p2)); sc = d.get("scenario") or {}
            out.append({"id": os.path.basename(p2)[:-5], "name": sc.get("name") or d.get("name"),
                        "objects": len(d.get("objects", [])), "links": len(d.get("links", []))})
        except Exception: pass
    return jsonify({"ontologies": out})

@app.get("/api/ont/forged/<fid>")
def ont_forged_one(fid):
    if not re.match(r"^[\w\-\u4e00-\u9fff·]+$", fid): return jsonify({"error": "bad id"}), 400
    p2 = os.path.join(FORGED_DIR, fid + ".json")
    if not os.path.exists(p2): return jsonify({"error": "不存在"}), 404
    return jsonify(json.load(open(p2)))

@app.post("/api/ont/save")
def ont_save():
    """保存当前(含编辑)IR 为版本 → 平台本体库 data/forged/(与 serve_claw 同存储)"""
    body = request.json or {}
    key, name = body.get("graph", "demo"), (body.get("name") or "").strip()
    if not name or len(name) > 40: return jsonify({"error": "需要 name(≤40字)"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    fid = "onto_" + uuid.uuid4().hex[:8]
    ir.setdefault("scenario", {})["name"] = name
    os.makedirs(FORGED_DIR, exist_ok=True)
    with _WRITE_LOCK:                                    # 原子写 + 串行化,避免中途崩溃截断已存本体版本(对齐 DR-006 写入原子性)
        _atomic_json(os.path.join(FORGED_DIR, fid + ".json"), ir)
    return jsonify({"ok": True, "id": fid, "name": name})

@app.post("/api/ont/forged/delete")
def ont_forged_delete():
    fid = (request.json or {}).get("id", "")
    if not re.match(r"^[\w\-]+$", fid): return jsonify({"error": "bad id"}), 400
    p2 = os.path.join(FORGED_DIR, fid + ".json")
    if not os.path.exists(p2): return jsonify({"error": "不存在"}), 404
    os.remove(p2)
    ttl = os.path.join(FORGED_DIR, fid + ".ttl")         # 同删 forge 写出的 .ttl 伴生文件,避免孤儿
    if os.path.exists(ttl): os.remove(ttl)
    return jsonify({"ok": True})

@app.get("/api/ont/runtimes")
def ont_runtimes():
    try:
        from agent_runtime import available
        return jsonify({"runtimes": available(), "current": os.environ.get("CLAW_DRIVER", "hermes")})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.get("/api/ont/skill/<name>")
def ont_skill_detail(name):
    if not re.match(r"^[\w\-]+$", name): return jsonify({"error": "bad"}), 400
    p2 = os.path.join(PLATFORM, "web", "skills_seed", name, "SKILL.md")
    if not os.path.exists(p2): return jsonify({"error": "不存在"}), 404
    return jsonify({"name": name, "content": open(p2).read()})

def _atomic_json(path, data):
    """原子写:先写 .tmp 再 os.replace,避免中途崩溃截断已存文件(会话/编辑/技能状态不丢)"""
    _atomic_text(path, json.dumps(data, ensure_ascii=False))

def _atomic_text(path, text):
    """文本文件的原子写(SKILL.md / OWL Turtle 等)。与 _atomic_json 同一纪律:
    先写 .tmp 再 os.replace,避免写到一半失败留下截断文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w") as fp: fp.write(text)
    os.replace(tmp, path)

CHATS_F = os.path.join(WORK, "ont_chats.json")
def _chats():
    if not os.path.exists(CHATS_F): return {}
    try:
        with open(CHATS_F) as fp: return json.load(fp)
    except Exception:
        return {}
def _chats_w(d):
    _atomic_json(CHATS_F, d)

@app.get("/api/ont/chats")
def ont_chats():
    return jsonify([{"id": k, "title": v.get("title", ""), "n": len(v.get("messages", []))} for k, v in _chats().items()])

@app.post("/api/ont/chats/new")
def ont_chats_new():
    cid = "c" + uuid.uuid4().hex[:10]
    with _WRITE_LOCK:
        d = _chats(); d[cid] = {"title": "", "messages": []}; _chats_w(d)
    return jsonify({"id": cid})

@app.post("/api/ont/chat")
def ont_chat():
    """原生对话式本体完善:agent_runtime(hermes/claude-code)+ 本体上下文;编辑建议以 op JSON 返回由前端确认执行"""
    body = request.json or {}
    cid, msg, key = body.get("id", ""), (body.get("message") or "").strip(), body.get("graph", "demo")
    if not msg: return jsonify({"error": "empty"}), 400
    d = _chats(); sess = d.setdefault(cid or "c_default", {"title": "", "messages": []})
    ir = load_ir_edited(key) or {}
    def _od(o):
        al = "、".join(o.get("aliases") or [])
        return f'{o.get("cn") or o.get("name")}({o.get("id")}{"|别称:" + al if al else ""})'
    objs = "; ".join(_od(o) for o in ir.get("objects", [])[:60])
    prompt = f"""你是本体治理助手。当前图谱[{key}]对象: {objs}
历史: {json.dumps(sess["messages"][-4:], ensure_ascii=False)[:800]}
用户: {msg}

可用编辑算子(仅这些,不得杜撰):
  rename        改中文名          params: {{"cn": "新名"}}
  set_alias     设业务别名(重要)   params: {{"aliases": "别名1,别名2"}}
  confirm       确认候选对象       params: {{}}
  verb          改关系动词         target: "rel:<源>-><目标>", params: {{"verb": "动词"}}
  add_relation  新增关系          target: "rel:<源>-><目标>", params: {{"verb": "动词"}}
  remove_object / remove_relation  删除(不可逆,需谨慎)

纪律:人工确认只产生 asserted,**永不指定 verified**(verified 只能由数据裁决产生)。
若业务用语与对象中文名不同(如业务说「产量」而对象叫「生产日汇总」),优先建议 set_alias。

若用户要求修改本体,回答末尾附一行 EDIT_OP:{{"op":"...","target":"obj:<id>","params":{{...}},"reason":"改动依据"}} 供人确认后执行;
否则直接中文回答(基于给出的对象,不编造)。"""
    reply = None
    try:
        from agent_runtime import available
        for drv in _drv_order():
            if drv not in available(): continue
            ok, r = runtime_cached(drv).run_turn(cid or "c_default", prompt, timeout=120)   # 缓存实例→多轮续接
            if ok and r: reply = r.strip(); break
    except Exception as e:
        reply = f"(引擎异常: {e})"
    reply = reply or "(引擎离线)"
    op = None
    m = re.search(r"EDIT_OP:(\{.*\})", reply, re.S)
    if m:
        try: op = json.loads(m.group(1))
        except Exception: pass
    sess["messages"] += [{"role": "user", "text": msg}, {"role": "ai", "text": reply}]
    sess["title"] = sess["title"] or msg[:24]
    with _WRITE_LOCK:   # 重读→只更新本会话→写回,避免并发覆盖其它会话
        cur = _chats(); cur[cid or "c_default"] = sess; _chats_w(cur)
    return jsonify({"reply": reply, "suggested_op": op})

@app.post("/api/ont/skills/install")
def ont_skill_install():
    """装技能到 agent 工作区(openclaw workspace),对齐平台 skills/install"""
    slug = (request.json or {}).get("slug", "")
    if not re.match(r"^[\w\-]+$", slug): return jsonify({"error": "bad slug"}), 400
    src = os.path.join(PLATFORM, "web", "skills_seed", slug)
    if not os.path.isdir(src): return jsonify({"error": "技能不存在"}), 404
    dst = os.path.expanduser(os.path.join("~/.openclaw/workspace/skills", slug))
    import shutil
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return jsonify({"ok": True, "installed": dst})

@app.post("/api/ont/skills/write")
def ont_skill_write():
    """写/建 SKILL.md(技能库编辑,对齐平台 skills/write)"""
    body = request.json or {}
    name, content = body.get("name", ""), body.get("content", "")
    if not re.match(r"^[\w\-]+$", name) or not content.strip(): return jsonify({"error": "需要合法 name+content"}), 400
    d = os.path.join(PLATFORM, "web", "skills_seed", name)
    os.makedirs(d, exist_ok=True)
    _atomic_text(os.path.join(d, "SKILL.md"), content)
    return jsonify({"ok": True, "path": os.path.join(d, "SKILL.md")})

@app.post("/api/ont/chat/stream")
def ont_chat_stream():
    """SSE 流式对话:阶段事件 + 最终回复(引擎仍阻塞,流式透出状态)"""
    body = request.json or {}
    cid, msg, key = body.get("id", ""), (body.get("message") or "").strip(), body.get("graph", "demo")
    atts = body.get("attachments") or []
    from flask import Response, stream_with_context
    import queue as _q
    def gen():
        yield 'data: {"type":"status","text":"加载本体上下文…"}\n\n'
        att_note = ""
        updir = os.path.join(WORK, "chat_uploads"); os.makedirs(updir, exist_ok=True)
        for a in atts[:5]:
            try:
                import base64
                fn = re.sub(r"[^\w.\-一-鿿]", "_", a.get("name", "f"))[:60]
                raw = base64.b64decode(a.get("b64", ""))
                open(os.path.join(updir, fn), "wb").write(raw)
                txt = ""
                if fn.lower().endswith((".txt", ".md", ".csv", ".json", ".sql")):
                    txt = raw.decode("utf-8", "replace")[:2000]
                att_note += f"\n[附件 {fn}]{(':' + txt) if txt else '(二进制已存档)'}"
            except Exception: pass
        if att_note: yield 'data: {"type":"status","text":"附件已解析入上下文"}\n\n'
        yield 'data: {"type":"status","text":"智能引擎分析中…"}\n\n'
        out = _q.Queue()
        def run():
            with app.test_request_context(json={"id": cid, "message": msg + att_note, "graph": key}):
                try: out.put(ont_chat().get_json())
                except Exception as e: out.put({"reply": f"(异常:{e})"})
        threading.Thread(target=run, daemon=True).start()
        t0 = time.time()
        while True:
            try:
                d = out.get(timeout=8)
                yield "data: " + json.dumps({"type": "done", **d}, ensure_ascii=False) + "\n\n"; break
            except Exception:
                yield f'data: {{"type":"status","text":"引擎运行中 {int(time.time()-t0)}s…"}}\n\n'
    return Response(stream_with_context(gen()), mimetype="text/event-stream")

@app.post("/api/ont/forge")
def ont_forge():
    """原生锻造:当前(含编辑)IR → OWL Turtle + 结构校验(+pyshacl 若装) → 存入本体库(IR+TTL)"""
    body = request.json or {}
    key, name = body.get("graph", "demo"), (body.get("name") or "").strip()
    if not name: return jsonify({"error": "需要 name"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        ttl = _ir_to_turtle(key, ir)                     # 统一走 IOF 注释化 Turtle(与导出/SPARQL 同底)
        import rdflib
        g = rdflib.Graph(); g.parse(data=ttl, format="turtle")
    except ImportError:
        return jsonify({"error": "锻造需要 rdflib(pip3 install rdflib)"}), 503
    except Exception as e:
        return jsonify({"error": f"OWL 生成失败: {e}"}), 400
    shacl = "未装 pyshacl(跳过)"
    try:
        import pyshacl, rdflib as _rl
        sg = _rl.Graph(); sg.parse(data=_IOF_SHACL, format="turtle")   # IOF 形状约束门禁
        with _RDF_LOCK:   # pyshacl 内部跑 SPARQL,同受 pyparsing 非线程安全影响,串行化
            conforms, _, txt = pyshacl.validate(g, shacl_graph=sg, inference="none")
        nviol = (txt or "").count("Constraint Violation")
        shacl = "conforms" if conforms else f"{nviol} violations"
    except ImportError: pass
    except Exception as e: shacl = f"shacl error: {str(e)[:60]}"
    fid = "onto_" + uuid.uuid4().hex[:8]
    ir.setdefault("scenario", {})["name"] = name
    os.makedirs(FORGED_DIR, exist_ok=True)
    with _WRITE_LOCK:                                     # 原子写(对齐 DR-006 写入原子性)
        _atomic_json(os.path.join(FORGED_DIR, fid + ".json"), ir)
        _atomic_text(os.path.join(FORGED_DIR, fid + ".ttl"), ttl)
    return jsonify({"ok": True, "id": fid, "triples": len(g), "shacl": shacl})

@app.get("/api/ont/rules")
def ont_rules():
    """构成规则:方法论映射 + 从当前IR派生的真实计数(同平台rules.js口径,不臆造)"""
    key = request.args.get("graph", "demo")
    ir = load_ir_edited(key) or {}
    objs, links = ir.get("objects", []), ir.get("links", []) or ir.get("relations", [])
    kinds = {}
    for o in objs: kinds[o.get("kind", "object")] = kinds.get(o.get("kind", "object"), 0) + 1
    ml = ir.get("metric_layers") or {}
    verified = sum(1 for l in links if (l.get("status") == "verified"))
    cand = sum(1 for o in objs if o.get("candidate"))   # 仅显式 candidate=True 计为候选;DR-001 建成对象默认为已确认
    return jsonify({"graph": key, "counts": {
        "objects": len(objs), "links": len(links), "verified": verified,
        "candidate_objs": cand, "confirmed_objs": len(objs) - cand, "kinds": kinds,
        "metrics": {k: len(v) for k, v in ml.items()},
        "hierarchy": len((ir.get("hierarchy") or {}).get("families", [])), "gaps": len(ir.get("gaps", []))},
        "methodology": [
            {"name": "斯坦福七步法", "map": "确定范围→复用→列举术语→定义类→类层次→定义属性→创建实例;对应 抽取列结构/枚举表→对象定义→hierarchy families→attrs→绑定实数据"},
            {"name": "Palantir 操作型本体四层", "map": "对象↔表 / 属性↔列 / 链接↔FK+取值重叠(≥60%∧列名有据=verified) / 指标↔DWS列(原子/派生/复合)"},
            {"name": "W3C OWL2+SHACL+HermiT", "map": "导出 owl:Class/DatatypeProperty/ObjectProperty+skos指标;锻造时 SHACL 校验;推理检查工具箱可跑"},
            {"name": "反造假纪律", "map": "verified 仅由数据裁决;人工/LLM 断言记 asserted/candidate;弱证据送审;编辑走白名单op+可撤销"}],
        "pipeline": [
            {"stage": "领域与源界定", "io": "数据源探活 → 表清单/连接", "rule": "真实查询探活(非端口探测)"},
            {"stage": "复用领域知识包", "io": "指标Excel/术语 → glossary", "rule": "知识包驱动命名与指标分层"},
            {"stage": "列举术语·对象与属性", "io": "表结构 → 对象+属性(中文)", "rule": "SchemaDump 单连接;过滤分区伪列"},
            {"stage": "关系发现·取值重叠", "io": "候选键对 → verified/candidate", "rule": "重叠≥60% ∧ 列名有据(name_score≥1);父键唯一度≥0.95;子键 distinct≥3"},
            {"stage": "类层次发现", "io": "对象 → hierarchy families", "rule": "证据化类层次(IR-007)"},
            {"stage": "动作·事件·指标分层", "io": "日志/API/DWS → 事件/动作/指标", "rule": "原子/派生/复合三层(DR-001)"},
            {"stage": "W3C 标准导出+校验", "io": "IR → OWL/JSON-LD + SHACL", "rule": "锻造时 SHACL conforms;HermiT 可推理"}],
        "owl_projection": {"owlClass": len(objs), "owlObjProp": len(links), "owlDataProp": sum(len(o.get("attrs", [])) for o in objs),
            "owlFunctional": sum(1 for l in links if str(l.get("card", "")).startswith("N:1")),
            "skos": (ml.get("atomic") and len(ml["atomic"]) or 0) + (ml.get("derived") and len(ml["derived"]) or 0)}})

@app.post("/api/ont/runtime")
def ont_runtime_set():
    name = (request.json or {}).get("name", "")
    try:
        from agent_runtime import available
        if name not in available(): return jsonify({"error": "无此运行时", "available": available()}), 400
    except Exception as e: return jsonify({"error": str(e)}), 500
    os.environ["CLAW_DRIVER"] = name
    return jsonify({"ok": True, "current": name})

@app.get("/api/health")
def health(): return jsonify({"ok": True, "ts": int(time.time()), "graphs": len(IR_SOURCES) + 1})

@app.get("/api/db/check")
def db_check():
    try:
        n = sqlite3.connect(DB).execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        return jsonify({"ok": True, "db": os.path.basename(DB), "tables": n})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.get("/api/ont/chats/<cid>")
def ont_chat_get(cid):
    d = _chats().get(cid)
    return jsonify(d) if d else (jsonify({"error": "无此会话"}), 404)

@app.post("/api/ont/chats/delete")
def ont_chat_del():
    cid = (request.json or {}).get("id", "")
    with _WRITE_LOCK:
        d = _chats()
        if cid in d: d.pop(cid); _chats_w(d); return jsonify({"ok": True})
    return jsonify({"error": "无此会话"}), 404

@app.get("/api/ont/object/<key>/<oid>")
def ont_object(key, oid):
    """单对象详情:属性(含中文/证据)/关系/指标 —— 对齐平台 /api/object/<id>"""
    ir = load_ir_edited(key) or {}
    o = next((x for x in ir.get("objects", []) if x.get("id") == oid or x.get("name") == oid), None)
    if not o: return jsonify({"error": "对象不存在"}), 404
    rels = [{"dir": "out" if l.get("source") == o.get("id") else "in", "verb": l.get("verb"),
             "other": l.get("target") if l.get("source") == o.get("id") else l.get("source"), "status": l.get("status")}
            for l in ir.get("links", []) if o.get("id") in (l.get("source"), l.get("target"))]
    # 对象描述/说明(对齐平台『对象描述』):由 kind/表/中文/证据来源构造
    KMAP = {"object": "对象", "event": "事件", "action": "动作", "asset": "资产", "role": "角色"}
    tb = o.get("table") or ""
    ev = o.get("evidence", {}) or {}
    src = "、".join(ev.get("sources", []) or []) or "IR"
    bfo = o.get("bfo") or _KIND_BFO.get(o.get("kind"), "MaterialEntity")
    defn = (o.get("definition") or "").strip()
    cex = (o.get("counterExample") or "").strip()
    desc = f'{KMAP.get(o.get("kind"),"对象")}「{o.get("cn") or o.get("name")}」(BFO:{bfo})' + (f',绑定表 {tb}' if tb else '') + f';字段 {len(o.get("attrs",[]))} 个;证据来源 {src}。'
    if defn: desc += f' 定义:{defn}'                      # IOF 属+种差定义
    if cex: desc += f'(反例:{cex})'                       # IOF counterExample,辅助辨伪
    # 业务指标:按表匹配指标目录,带编码/描述/类型(对齐平台『业务指标管理』)
    LNAME = {"atomic": "原子指标", "derived": "派生指标", "composite": "复合指标"}
    metrics = []
    for lk, arr in (ir.get("metric_layers") or {}).items():
        for m in arr:
            if (m.get("table") or "").lower() == tb.lower() and tb:
                metrics.append({"name": m.get("name"), "code": m.get("id"), "desc": m.get("desc"),
                                "type": LNAME.get(lk, lk), "unit": m.get("unit")})
    return jsonify({"id": o.get("id"), "cn": o.get("cn"), "name": o.get("name"), "kind": o.get("kind"),
                    "table": o.get("table"), "pk": o.get("pk"), "candidate": o.get("candidate"),
                    "description": desc, "scene": o.get("scene") or o.get("scenario") or "",
                    "attrs": o.get("attrs", []), "supported_metrics": o.get("supported_metrics", []),
                    "metrics": metrics, "evidence": ev, "relations": rels,
                    # ── IOF-AV 机读注释(供审计/导出/前端展示)──
                    "bfo": bfo, "definition": defn, "isPrimitive": o.get("isPrimitive"),
                    "example": o.get("example", ""), "counterExample": cex,
                    "maturity": o.get("maturity", ""), "provenance": o.get("provenance")})

@app.get("/api/ont/relation/<key>")
def ont_relation(key):
    """关系详情(证据卡)—— src/tgt 由 query 传;对齐 render.js 连线详情面板"""
    src, tgt = request.args.get("s", ""), request.args.get("t", "")
    ir = load_ir_edited(key) or {}
    links = ir.get("links") or []
    l = next((x for x in links if x.get("source") == src and x.get("target") == tgt), None)
    if not l:
        rels = ir.get("relations") or []
        l = next((x for x in rels if x.get("source_concept") == src and x.get("target_concept") == tgt), None)
        if l: l = {"source": src, "target": tgt, "verb": l.get("verb"), "status": l.get("status"),
                   "founded_relation": l.get("founded_relation"), "temporal": l.get("temporal"),
                   "semantic": l.get("semantic"), "note": l.get("note", ""),
                   "evidence": {"overlap_pct": l.get("overlap")}}
    if not l: return jsonify({"error": "关系不存在"}), 404
    ev = l.get("evidence", {})
    fr, tq = l.get("founded_relation"), l.get("temporal")
    if not fr: fr, tq = _ground_verb(l.get("verb"))       # 老 IR 无接地字段则按动词回溯(IOF 有根据关系)
    return jsonify({"source": src, "target": tgt, "verb": l.get("verb"), "status": l.get("status"),
                    "candidate": l.get("candidate"), "note": l.get("note", ""), "card": l.get("card", ""),
                    "child_key": ev.get("child_key"), "parent_key": ev.get("parent_key"),
                    "overlap_pct": ev.get("overlap_pct"), "child_distinct": ev.get("child_distinct"),
                    "sources": ev.get("sources", []),
                    "founded_relation": fr, "temporal": tq or "atSomeTime",
                    "semantic": l.get("semantic", "")})   # IOF/BFO 接地 + 语义评审标注

# IOF 风格 SHACL 形状:非原始类须有定义、每个类须有标签(本体质量门禁,借鉴 IOF『非原始类须有定义』)
_IOF_SHACL = """@prefix sh:     <http://www.w3.org/ns/shacl#> .
@prefix owl:    <http://www.w3.org/2002/07/owl#> .
@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .
@prefix iof-av: <https://spec.industrialontologies.org/ontology/core/meta/AnnotationVocabulary/> .
[] a sh:NodeShape ; sh:targetClass owl:Class ;
   sh:property [ sh:path rdfs:label ; sh:minCount 1 ; sh:message "IOF: 类必须有 rdfs:label" ] ;
   sh:or ( [ sh:path iof-av:isPrimitive ; sh:hasValue true ]
           [ sh:path iof-av:naturalLanguageDefinition ; sh:minCount 1 ] ) ;
   sh:message "IOF: 非原始类必须有 naturalLanguageDefinition 定义" .
"""

# BFO 2020 / IOF Core 上层类 IRI(供 rdfs:subClassOf 归类;借鉴 Industrial Ontology Foundry)
_BFO_UP = {"MaterialEntity": "obo:BFO_0000040", "Object": "obo:BFO_0000030", "Process": "obo:BFO_0000015",
           "Continuant": "obo:BFO_0000002", "Role": "obo:BFO_0000023", "Disposition": "obo:BFO_0000016",
           "Function": "obo:BFO_0000034", "Quality": "obo:BFO_0000019",
           "InformationContentEntity": "iof:InformationContentEntity", "MaterialArtifact": "iof:MaterialArtifact"}
_TTL_XSD = {"int": "integer", "integer": "integer", "bigint": "integer", "smallint": "integer", "tinyint": "integer",
            "decimal": "decimal", "numeric": "decimal", "double": "decimal", "float": "decimal", "real": "decimal",
            "date": "date", "datetime": "dateTime", "timestamp": "dateTime", "bool": "boolean", "boolean": "boolean"}

def _ir_to_turtle(key, ir):
    """IR → OWL2 Turtle,带 BFO 上层归类 + IOF-AV 机读注释 + 关系接地(借鉴 Industrial Ontology Foundry)。
    自包含、确定性纯函数;所有图谱(示例/应用/quick_build/forged)统一走此路径,ttl/jsonld/owl 三格式一致且带注释。"""
    g = ir_to_graph(key, ir)
    raw = {}                                              # 原始对象索引:取 attrs 补字段级数据类型属性,不丢信息
    for o in (ir.get("objects") or []):
        for kk in (o.get("id"), o.get("name")):
            if kk: raw[str(kk)] = o
    def loc(x):
        s = re.sub(r"[^0-9A-Za-z_]", "_", str(x))
        return ("_" + s) if (s and s[0].isdigit()) else (s or "_")
    def esc(x):                                           # 转义 + C0 控制字符→\uXXXX,兼容严格 TTL 解析器(Jena 等)
        s = str(x).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", lambda m: "\\u%04X" % ord(m.group(0)), s)
    xsd = lambda t: "xsd:" + _TTL_XSD.get(str(t or "").split("(")[0].strip().lower(), "string")
    scen = (ir.get("scenario") or {}).get("name") or key
    L = ["@prefix :       <http://datamind.local/ont#> .",
         "@prefix owl:    <http://www.w3.org/2002/07/owl#> .",
         "@prefix rdfs:   <http://www.w3.org/2000/01/rdf-schema#> .",
         "@prefix xsd:    <http://www.w3.org/2001/XMLSchema#> .",
         "@prefix obo:    <http://purl.obolibrary.org/obo/> .",
         "@prefix iof:    <https://spec.industrialontologies.org/ontology/core/Core/> .",
         "@prefix iof-av: <https://spec.industrialontologies.org/ontology/core/meta/AnnotationVocabulary/> .",
         "", ":temporal a owl:AnnotationProperty ; rdfs:label \"BFO 关系时间指标\"@zh .",
         f':  a owl:Ontology ; rdfs:label "{esc(scen)}"@zh ; iof-av:maturity iof-av:Provisional .', ""]
    used_fr = set()
    for n in g["nodes"]:
        cid = loc(n["id"]); up = _BFO_UP.get(n.get("bfo") or "", "")
        parts = [f':{cid} a owl:Class']
        if up: parts.append(f'rdfs:subClassOf {up}')     # BFO/IOF 上层归类
        parts.append(f'rdfs:label "{esc(n["name"])}"@zh')
        defn = (n.get("definition") or "").strip()
        if defn: parts.append(f'iof-av:naturalLanguageDefinition "{esc(defn)}"@zh')
        if n.get("isPrimitive") is not None:
            parts.append(f'iof-av:isPrimitive "{str(bool(n["isPrimitive"])).lower()}"^^xsd:boolean')
        if (n.get("example") or "").strip(): parts.append(f'iof-av:example "{esc(n["example"])}"@zh')
        if (n.get("counterExample") or "").strip(): parts.append(f'iof-av:counterExample "{esc(n["counterExample"])}"@zh')
        mat = (n.get("maturity") or "").strip() or "Provisional"
        if mat in ("Provisional", "Released", "Deprecated"): parts.append(f'iof-av:maturity iof-av:{mat}')
        prov = n.get("provenance") or {}
        if prov.get("directSource"): parts.append(f'iof-av:directSource "{esc(prov["directSource"])}"')
        for ad in (prov.get("adaptedFrom") or []):
            if ad: parts.append(f'iof-av:adaptedFrom "{esc(ad)}"')
        L.append(" ;\n    ".join(parts) + " .")
        for a in ((raw.get(str(n["id"])) or {}).get("attrs") or [])[:60]:   # 字段级数据类型属性
            col = a.get("col")
            if not col: continue
            L.append(f':{loc(cid + "_" + col)} a owl:DatatypeProperty ; rdfs:domain :{cid} ; '
                     f'rdfs:range {xsd(a.get("type"))} ; rdfs:label "{esc(a.get("cn") or col)}"@zh .')
    for i2, e2 in enumerate(g["edges"]):
        fr = e2.get("founded_relation") or "relatedToAtSomeTime"; used_fr.add(fr)
        # 关系 IRI 带 verb+序号,避免同一对节点的多条不同关系塌缩成一个属性(丢边)
        rid = f'rel_{loc(e2["s"])}_{loc(e2.get("verb",""))}_{loc(e2["t"])}_{i2}'
        seg = [f':{rid} a owl:ObjectProperty', f'rdfs:subPropertyOf iof:{loc(fr)}',   # 接地到 BFO 有根据关系
               f'rdfs:domain :{loc(e2["s"])}', f'rdfs:range :{loc(e2["t"])}',
               f'rdfs:label "{esc(e2.get("verb",""))}"@zh', f':temporal "{esc(e2.get("temporal") or "atSomeTime")}"']
        if e2.get("status"): seg.append(f'iof-av:usageNote "{esc("status=" + str(e2["status"]))}"')
        L.append(" ;\n    ".join(seg) + " .")
    for fr in sorted(used_fr):                            # 声明用到的有根据关系,保证自包含可解析
        L.append(f'iof:{loc(fr)} a owl:ObjectProperty ; rdfs:label "{esc(fr)}" .')
    return "\n".join(L)

@app.get("/api/graph/<key>/export.<fmt>")
def graph_export_fmt(key, fmt):
    """OWL 多格式导出:ttl / jsonld / owl(rdfxml) —— 三格式对所有图谱一致可用"""
    if fmt not in ("ttl", "jsonld", "owl", "rdf", "xml"):
        return jsonify({"error": "格式仅支持 ttl/jsonld/owl/rdf/xml"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    from flask import Response
    ttl = _ir_to_turtle(key, ir)
    if fmt == "ttl":
        return Response(ttl, mimetype="text/turtle", headers={"Content-Disposition": f"attachment;filename={key}.ttl"})
    try:
        import rdflib                                    # 置于 try 内:缺依赖时给可操作提示,而非异常直穿成 500
        g = rdflib.Graph(); g.parse(data=ttl, format="turtle")
    except ImportError:
        return jsonify({"error": "该格式需要 rdflib(pip3 install rdflib);Turtle 格式无此依赖,可直接导出"}), 503
    except Exception as e:
        return jsonify({"error": f"图谱序列化失败: {str(e)[:120]}"}), 400
    if fmt == "jsonld":
        return Response(g.serialize(format="json-ld"), mimetype="application/ld+json", headers={"Content-Disposition": f"attachment;filename={key}.jsonld"})
    return Response(g.serialize(format="xml"), mimetype="application/rdf+xml", headers={"Content-Disposition": f"attachment;filename={key}.owl"})

@app.post("/api/ont/cq")
def ont_cq():
    """能力问题(CQ)核验(DR-024):在已建成的本体上判定「这些业务问题答不答得了」。

    与完备度记分卡正交——记分卡答「本体规不规范」,CQ 答「本体够不够用」:
    一个定义 100%、接地 100% 的本体,完全可能缺了业务真正要问的那条关系。

    判定为确定性图计算(对象锚定 + 路径可达 + 边状态),不调 LLM:
    让模型自评「能不能答」会把「看起来能答」当成「能答」,与反造假纪律相悖。

    请求: {"graph": "<键>", "cqs": ["问题…", {"q": "问题…", "expect": ["对象名"]}]}
    """
    body = request.get_json(silent=True) or {}
    key = (body.get("graph") or "").strip()
    if not key: return jsonify({"error": "缺 graph(不默认任何图谱)"}), 400
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    cqs = body.get("cqs") or []
    if not isinstance(cqs, list) or not cqs: return jsonify({"error": "缺 cqs(能力问题列表)"}), 400
    if len(cqs) > 100: return jsonify({"error": "cqs 过多(上限 100)"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import cq_check
        rep = cq_check.check_all(cqs, ir)
        rep["graph"] = key
        rep["gaps"] = cq_check.gaps_from(rep)      # 供人审队列/构建下一轮消费
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"CQ 核验失败: {e}"}), 500

@app.post("/api/ont/chain")
def ont_chain():
    """穿透链路核验(DR-025):逐段判定一条业务追溯链路通不通。

    《本体智能研究报告》四个行业案例方法同构——定义 5-6 类核心实体后,
    关键在建立一条纵向穿透链路(停电事件—设备—线路—用户 / 订单—资源—工单—用户 /
    飞机—子系统—零部件—供应商 / 客户—账户—交易—关联方)。本体的价值不在对象多,
    而在能否从一端穿到另一端;首尾通但中段断的链路在业务上是断的,故逐段判定。

    请求: {"graph": "<键>", "chain": ["停电事件", "配电设备", "线路", "用户"]}
    """
    body = request.get_json(silent=True) or {}
    key = (body.get("graph") or "").strip()
    if not key: return jsonify({"error": "缺 graph(不默认任何图谱)"}), 400
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    chain = body.get("chain") or []
    if not isinstance(chain, list) or len(chain) < 2:
        return jsonify({"error": "缺 chain(至少 2 个节点的对象名列表)"}), 400
    if len(chain) > 20: return jsonify({"error": "chain 过长(上限 20 节点)"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import cq_check
        rep = cq_check.check_chain(chain, ir)
        rep["graph"] = key
        rep["gaps"] = cq_check.chain_gaps(rep)
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"链路核验失败: {e}"}), 500


@app.get("/api/ont/drift/<key>")
def ont_drift(key):
    """概念漂移与关系断裂检测(DR-025):本体还对不对得上数据源。

    《本体智能研究报告》阶段六点名「引入自动化检测工具监控本体与数据源的一致性,
    及时发现概念漂移与关系断裂」。本体建成之日与库一致,但库会继续演进——
    表改名、列删除、主键换名,此时本体不报错,只在问数时静默产出错误 SQL。

    确定性 schema 比对,不调 LLM;只报事实不自动修复(漂移的正解可能是改本体、
    也可能是数据源回滚,须人判断)。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    if not os.path.exists(DB):
        return jsonify({"error": "数据源不可用,无法比对", "checked": False}), 503
    try:
        import drift_check
        rep = drift_check.check(ir, DB)
        rep["graph"] = key
        rep["gaps"] = drift_check.gaps_from(rep)
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"漂移检测失败: {e}"}), 500

@app.get("/api/ont/usage/<key>")
def ont_usage(key):
    """本体使用度与建模优先级(DR-026):以使用数据驱动建模迭代。

    报告阶段六:「定期评估本体的业务调用频次与决策支撑效果,以使用数据驱动优化迭代」。
    统计本身不是目的——把调用频次与证据状态交叉,直接产出优先级:
    高频却仍有候选关系 → 优先补裁决;零调用 → 疑似建模过度(须先确认统计窗口够长)。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import usage_stat
        return jsonify(usage_stat.report(WORK, ir, key))
    except Exception as e:
        return jsonify({"error": f"使用度统计失败: {e}"}), 500

@app.get("/api/ont/audit/<key>")
def ont_audit(key):
    """本体变更审计(DR-027):谁在何时改了什么,以及哪些改动值得复核。

    与 /api/ont/edits 的区别:后者是原始日志(给回放用),这里是**审计视图**——
    按人/类型/来源聚合,并主动标出风险项。报告阶段六要求「明确本体治理的责任主体」,
    责任要能追溯到人,就必须能回答「这条改动是谁做的、依据什么、AI 建议还是人自己定的」。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ed = _load_edits(key)
    ops = ed.get("ops", [])
    by_person, by_op, by_src = {}, {}, {}
    no_reviewer, no_reason, risky = [], [], []
    for i, o in enumerate(ops):
        who = (o.get("reviewer") or "").strip() or "(未署名)"
        kind = o.get("op", "?")
        src = o.get("source", "api")
        by_person[who] = by_person.get(who, 0) + 1
        by_op[kind] = by_op.get(kind, 0) + 1
        by_src[src] = by_src.get(src, 0) + 1
        if not (o.get("reviewer") or "").strip():
            no_reviewer.append(i)
        if not (o.get("reason") or "").strip():
            no_reason.append(i)
        # 风险项:删除类不可逆影响面大;人审试图直接指定 verified 违反反造假纪律
        if kind in ("remove_object", "remove_relation", "reject_relation"):
            risky.append({"idx": i, "op": kind, "target": o.get("target"),
                          "by": who, "ts": o.get("ts"), "level": "destructive",
                          "why": "删除/否决类操作影响面大且需级联,建议复核"})
        if str((o.get("params") or {}).get("status", "")).lower() == "verified":
            risky.append({"idx": i, "op": kind, "target": o.get("target"),
                          "by": who, "ts": o.get("ts"), "level": "discipline",
                          "why": "人审试图直接指定 verified —— 违反反造假纪律"
                                 "(verified 只能由数据裁决产生,人只产生 asserted)"})
    return jsonify({
        "graph": key, "total": len(ops),
        "by_person": by_person, "by_op": by_op, "by_source": by_src,
        "unsigned": len(no_reviewer), "no_reason": len(no_reason),
        "risky": risky,
        "recent": [{"idx": i, "op": o.get("op"), "target": o.get("target"),
                    "by": o.get("reviewer") or "(未署名)", "ts": o.get("ts"),
                    "source": o.get("source", "api"), "reason": o.get("reason", "")}
                   for i, o in list(enumerate(ops))[-20:]][::-1],
        "note": "审计视图基于编辑日志;日志是回放的单一真相,撤销会同步移除条目——"
                "故本视图反映的是当前生效的变更集,不是历史全量操作流水",
    })

_RULES_F = os.path.join(WORK, "ont_rules.json")

def _load_rules(key):
    try:
        d = json.load(open(_RULES_F, encoding="utf-8"))
        return d.get(key, []) if isinstance(d, dict) else []
    except Exception:
        return []

def _save_rules(key, rules):
    with _WRITE_LOCK:
        try:
            d = json.load(open(_RULES_F, encoding="utf-8"))
            if not isinstance(d, dict): d = {}
        except Exception:
            d = {}
        d[key] = rules
        _atomic_json(_RULES_F, d)

@app.get("/api/ont/rulebook/<key>")
def ont_rulebook(key):
    """业务规则与约束清单(DR-028 · 报告语义层第四要素)+ 静态一致性校验。"""
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    import rule_engine
    rules = _load_rules(key)
    return jsonify({"graph": key, "rules": rules,
                    "consistency": rule_engine.consistency_check(rules)})

@app.post("/api/ont/rulebook/<key>")
def ont_rulebook_save(key):
    """新增/更新一条业务规则。结构非法一律拒收——规则是逻辑边界,带病入库会污染全部下游判定。"""
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    import rule_engine
    r = (request.get_json(silent=True) or {}).get("rule") or {}
    err = rule_engine.validate_rule(r)
    if err: return jsonify({"error": f"规则非法: {err}"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    keys = {o.get("id") or o.get("name") for o in ir.get("objects", [])}
    if r["on"] not in keys:
        return jsonify({"error": f"作用对象 {r['on']} 不在本体中 —— 规则须锚定到已建模的对象"}), 400
    r["ts"] = time.strftime("%Y-%m-%d %H:%M")
    who = ((request.get_json(silent=True) or {}).get("author") or "").strip()[:40]
    if who: r["author"] = who
    rules = [x for x in _load_rules(key) if x.get("id") != r["id"]] + [r]
    if len(rules) > 500: return jsonify({"error": "规则过多(上限 500)"}), 400
    _save_rules(key, rules)
    return jsonify({"ok": True, "total": len(rules),
                    "consistency": rule_engine.consistency_check(rules)})

@app.post("/api/ont/rulebook/<key>/delete")
def ont_rulebook_del(key):
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    rid = (request.get_json(silent=True) or {}).get("id", "")
    rules = _load_rules(key)
    left = [x for x in rules if x.get("id") != rid]
    if len(left) == len(rules): return jsonify({"error": "规则不存在"}), 404
    _save_rules(key, left)
    return jsonify({"ok": True, "total": len(left)})

@app.post("/api/ont/decide/<key>")
def ont_decide(key):
    """决策层求值(DR-028):由规则推导隐含结论,每条结论可回溯至具体规则依据。

    报告决策层要求「逻辑推理基于语义层的概念关系与业务规则,推导出未显式记录的
    隐含结论」「形成完整可追溯的决策路径——每一条结论均可回溯至具体规则依据」。
    确定性求值,不调 LLM:规则是业务写死的逻辑边界,用模型推理会把概率当逻辑。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    body = request.get_json(silent=True) or {}
    obj = (body.get("object") or "").strip()
    facts = body.get("facts")
    if not obj: return jsonify({"error": "缺 object(作用对象)"}), 400
    if not isinstance(facts, dict) or not facts:
        return jsonify({"error": "缺 facts(该实例的字段字典)"}), 400
    import rule_engine
    res = rule_engine.evaluate(_load_rules(key), obj, facts)
    res["graph"] = key
    return jsonify(res)

@app.get("/api/ont/health/<key>")
def ont_health(key):
    """本体健康度体检(DR-030 · 报告阶段六「异常关系检测」与「定期评审健康度」)。

    与既有三项检测互补——它们都不看图结构本身:
      CQ 答「够不够用」· 漂移答「还对不对得上数据」· 完备度答「定义填没填全」
    而一个三项全过的本体,结构上仍可能是病的:一半对象是孤岛、存在自反关系、
    同一对语义重复连了多条边。这些不会让任何现有检查报错,却会让问数召回选错表。

    分级:dangling/self_loop/status_conflict 是硬错误(IR 不自洽);
    isolated/hub/duplicate/bidirectional 是待核查信号。健康分只由硬错误扣分——
    否则一个业务枢纽对象就能把分数拉垮,分数失去意义。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import health_check
        rep = health_check.check(ir)
        rep["graph"] = key
        rep["gaps"] = health_check.gaps_from(rep)
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"健康度体检失败: {e}"}), 500

@app.get("/api/ont/compat/<key>")
def ont_compat(key):
    """向后兼容性检查(DR-031 · 报告阶段五/六「版本管理与向后兼容性保障」)。

    比较**基线 IR** 与**当前编辑后 IR**:发布这批草案编辑会破坏什么。

    本体是语义契约——问数靠它召回表与口径、规则靠它锚定对象、动作靠它绑表、
    穿透链路靠它连通。删掉一个对象可能让几条规则失效、几个动作绑不到表,
    而这些往往直到线上报错才被发现。

    重点在**下游影响**而非结构 diff:只报「删了 3 个对象」没有决策价值,
    报「删掉的对象上挂着 2 条规则和 1 个动作」才让人知道该不该删。
    不阻断变更——兼容性是决策依据不是权限,判断权在人。

    可选 ?against=<另一图谱键> 改为与另一图谱比较。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    against = (request.args.get("against") or "").strip()
    if against and _bad_gkey(against): return jsonify({"error": "非法对比图谱键"}), 400
    new_ir = load_ir_edited(key)
    if not new_ir: return jsonify({"error": "图谱不存在"}), 404
    base_ir = load_ir_edited(against) if against else load_ir(key)
    if not base_ir: return jsonify({"error": "基线图谱不存在"}), 404
    try:
        import compat_check
        rules = _load_rules(key)
        try:
            actions = json.load(open(os.path.join(WORK, "action_types.json"), encoding="utf-8"))
            actions = actions if isinstance(actions, list) else []
        except Exception:
            actions = []
        try:
            qas = json.load(open(os.path.join(WORK, "qa_skills.json"), encoding="utf-8"))
            qas = qas if isinstance(qas, list) else []
        except Exception:
            qas = []
        rep = compat_check.check(base_ir, new_ir, rules, actions, qas)
        rep["graph"] = key
        rep["baseline"] = against or f"{key}(未编辑基线)"
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"兼容性检查失败: {e}"}), 500

@app.get("/api/ont/modules/<key>")
def ont_modules(key):
    """本体模块化划分建议(DR-031 · 报告阶段二「模块化策略:按领域或按层次拆分」)。

    我们的本体是一张平图,108 个对象平铺没有模块边界。后果很具体:
    改一处不知影响范围、想按域交给不同团队维护无从下手、新人打开图谱建立不了认知。

    ?strategy=by_domain(默认,连通分量+词根聚类)或 by_layer(数仓分层)。
    只建议不落盘:模块边界最终是业务决策,算法只给结构上的自然分界——
    自动切分会把一个错误的边界固化进本体。
    """
    if _bad_gkey(key): return jsonify({"error": "非法图谱键"}), 400
    st = (request.args.get("strategy") or "by_domain").strip()
    if st not in ("by_domain", "by_layer"):
        return jsonify({"error": "strategy 需为 by_domain 或 by_layer"}), 400
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    try:
        import module_split
        rep = module_split.suggest(ir, st)
        rep["graph"] = key
        return jsonify(rep)
    except Exception as e:
        return jsonify({"error": f"模块划分失败: {e}"}), 500

@app.get("/api/ont/completeness/<key>")
def ont_completeness(key):
    """本体完备度 / IOF 一致性记分卡:定义·示例·反例覆盖率、BFO 归类率、成熟度分布、关系接地率。
    借鉴 IOF『非原始类须有定义、每个术语须有成熟度』的工程纪律,量化图谱的可审计程度(供人审与专利佐证)。"""
    from collections import Counter
    ir = load_ir_edited(key)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    g = ir_to_graph(key, ir)
    nodes, edges = g["nodes"], g["edges"]
    cnt = lambda pred: sum(1 for n in nodes if pred(n))
    has = lambda f: cnt(lambda n: bool(str(n.get(f) or "").strip()))
    obj = {"total": len(nodes),
           "withDefinition": has("definition"), "withExample": has("example"),
           "withCounterExample": has("counterExample"), "primitive": cnt(lambda n: n.get("isPrimitive") is True),
           "bound": cnt(lambda n: any(n.get("tables") or [])), "withBFO": has("bfo"), "withMaturity": has("maturity"),
           "byBFO": dict(Counter((n.get("bfo") or "—") for n in nodes)),
           "byMaturity": dict(Counter((n.get("maturity") or "—") for n in nodes))}
    grounded = sum(1 for e in edges if e.get("founded_relation") and e["founded_relation"] != "relatedToAtSomeTime")
    rel = {"total": len(edges), "grounded": grounded,
           "verified": sum(1 for e in edges if e.get("status") == "verified"),
           "candidate": sum(1 for e in edges if e.get("status") == "candidate"),
           "semanticReviewed": sum(1 for e in edges if e.get("semantic") in ("pass", "fail")),
           "semanticDisputed": sum(1 for e in edges if e.get("semantic") == "fail"),
           "byTemporal": dict(Counter((e.get("temporal") or "—") for e in edges))}
    frac = lambda x, d: (x / d) if d else 0
    nn = len(nodes)
    # 加权完备度:定义 40% + 反例 15% + 成熟度已定 10% + BFO 归类 15% + 关系接地 20%
    score = round(100 * (0.40 * frac(obj["withDefinition"], nn) + 0.15 * frac(obj["withCounterExample"], nn) +
                         0.10 * frac(obj["withMaturity"], nn) + 0.15 * frac(obj["withBFO"], nn) +
                         0.20 * frac(grounded, len(edges))), 1) if nn else 0.0
    gaps = [n["name"] for n in nodes if not (n.get("definition") or "").strip()][:20]   # 待补定义清单
    return jsonify({"key": key, "score": score, "objects": obj, "relations": rel, "gaps": gaps})

def _ir_write_path(key):
    """图谱键 → 可回写的 IR JSON 路径;只读平台源(cq 的 .js)或非法键返 None,绝不逃逸。"""
    if _bad_gkey(key): return None
    if key.startswith("built_"): return os.path.join(WORK, key + ".json")
    if key.startswith("forged_"):
        p = os.path.join(FORGED_DIR, key[7:] + ".json"); return p if os.path.exists(p) else None
    src = IR_SOURCES.get(key)
    if src:
        for p in src["paths"]:
            if p.endswith(".json"): return p              # demo→WORK/demo_ir.json、app→WORK/app_ontology_ir.json
    return None

def _open_writable(key, need_objects=False):
    """IR 写端点(enrich/reground/maturity)统一前奏:图谱必填校验 + 可回写路径守卫 + 载图 + 存在性校验。
    成功返回 (ir, write_path, None);失败返回 (None, None, (响应, 状态码))——缺图谱参数/只读源/穿越 400,缺图/缺对象 404。"""
    if not key:   # 写端点必须显式指定图谱,不静默默认到 示例 主图(防漏传/拼错时误改生产本体)
        return None, None, (jsonify({"error": "需要指定图谱(graph)"}), 400)
    wp = _ir_write_path(key)
    if not wp:
        return None, None, (jsonify({"error": "该图谱为只读平台源,不支持回写(请选 示例/应用/构建/锻造图谱)"}), 400)
    ir = load_ir(key)
    if not ir:
        return None, None, (jsonify({"error": "图谱不存在"}), 404)
    if need_objects and not isinstance(ir.get("objects"), list):
        return None, None, (jsonify({"error": "图谱无对象"}), 404)
    return ir, wp, None

def _llm_define(objs):
    """批量为对象生成 IOF 风格『属+种差』定义 + 正例 + 反例;返回 {name: {definition, example, counterExample}}。
    严格基于给定语义,引擎离线/超时返回空(绝不编造)。"""
    from agent_runtime import get_runtime, available
    items = []
    for o in objs:
        cols = ", ".join(a.get("col", "") for a in (o.get("attrs") or [])[:12])
        tb = o.get("table") or (o.get("tables") or [""])[0]
        items.append(f'- name={o.get("name") or o.get("id")}; cn={o.get("cn") or ""}; kind={o.get("kind")}; table={tb}; 列[{cols}]')
    prompt = ("你是企业本体定义专家。为下列对象各写一条 IOF 风格『属+种差』定义、一个正例、一个易混淆反例。\n"
              "对象清单:\n" + "\n".join(items) +
              '\n\n只输出一个 JSON(无其它文字):{"<name>":{"definition":"X 是一种 Y,且…(属+种差,简洁准确,基于给定语义,不编造)",'
              '"example":"一个正例","counterExample":"一个会被误认成它、实则不是的反例(如 销售订单↔报价单)"}, ...}\n'
              "要求:①定义用中文属+种差句式;②不虚构表/列中没有的语义;③反例要有辨析价值;④JSON 的 key 必须是上面给出的 name;"
              "⑤**非循环**:定义体中不得复用被定义术语名本身及其中文名(如定义『销售订单』不得出现『销售订单』字样),用上位类(属)+区别特征(种差)描述,避免自指。")
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"def_{uuid.uuid4().hex[:6]}", prompt, timeout=110)
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try:
                    d = json.loads(m.group(0))
                    if isinstance(d, dict): return d
                except Exception: pass
    return {}

@app.post("/api/ont/enrich")
def ont_enrich():
    """一键补定义/反例:对缺定义的对象调智能引擎生成『属+种差』定义+正例+反例,回写 IR,升级到 IOF 标准。
    默认只补缺失项(非破坏,只增字段);force=1 全量重生;引擎离线则不动(绝不用模板编造定义)。"""
    body = request.json or {}
    key = body.get("graph"); force = bool(body.get("force")); limit = min(int(body.get("limit") or 60), 120)
    ir, wp, err = _open_writable(key, need_objects=True)
    if err: return err
    objs = ir["objects"]
    targets = [o for o in objs if force or not (o.get("definition") or "").strip()][:limit]
    if not targets: return jsonify({"ok": True, "enriched": 0, "note": "所有对象已有定义,无需补全"})
    try:
        from agent_runtime import available
        if not available(): return jsonify({"error": "智能引擎离线,无法生成定义(本功能不编造定义)"}), 503
    except Exception:
        return jsonify({"error": "智能引擎不可用"}), 503
    idx = {(o.get("name") or o.get("id")): o for o in objs}
    enriched = 0
    for i in range(0, len(targets), 20):                  # 分批≤20,控 prompt 体量防超时
        gen = _bounded(lambda b=targets[i:i + 20]: _llm_define(b), 120) or {}
        for nm, ann in (gen.items() if isinstance(gen, dict) else []):
            o = idx.get(nm)
            if not o or not isinstance(ann, dict): continue
            d = (ann.get("definition") or "").strip()
            if not d: continue
            o["definition"] = d; o["isPrimitive"] = False
            if (ann.get("example") or "").strip(): o["example"] = ann["example"].strip()
            if (ann.get("counterExample") or "").strip(): o["counterExample"] = ann["counterExample"].strip()
            o.setdefault("maturity", "Provisional")
            o["bfo"] = o.get("bfo") or _KIND_BFO.get(o.get("kind"), "MaterialEntity")
            enriched += 1
    if enriched:
        with _WRITE_LOCK: _atomic_json(wp, ir)            # 原子回写(只增注释字段,非破坏)
    return jsonify({"ok": True, "enriched": enriched, "targets": len(targets),
                    "note": f"已为 {enriched}/{len(targets)} 个对象补全定义/反例" + ("" if enriched else ",引擎未产出有效定义")})

_VERB_VOCAB = ["归属", "包含", "组成", "产生", "输出", "触发", "输入", "服务", "参与", "承载", "实现", "描述"]
def _rel_ends(r):
    """兼容 links(source/target)与 relations(source_concept/target_concept)两种关系结构。"""
    if "source" in r or "target" in r: return r.get("source"), r.get("target")
    return r.get("source_concept"), r.get("target_concept")

def _llm_verbs(pairs):
    """pairs: [(s,s_cn,t,t_cn)]。让 LLM 从受控词表为每条关系选具体动词。返回 {(s,t): verb}。"""
    from agent_runtime import get_runtime, available
    lines = [f'{i}. {sn}({scn}) → {tn}({tcn})' for i, (sn, scn, tn, tcn) in enumerate(pairs)]
    prompt = ("你是本体关系标注专家。为每条『源→目标』关系,从受控词表选一个最贴切的关系动词。\n"
              f"受控词表(只能选其一):{'|'.join(_VERB_VOCAB)}\n关系清单:\n" + "\n".join(lines) +
              '\n\n只输出 JSON(无其它文字):{"<序号>":"动词", ...};动词必须来自受控词表,序号对应上面每条。')
    allowed = set(_VERB_VOCAB)
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"vb_{uuid.uuid4().hex[:6]}", prompt, timeout=110)
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try:
                    d = json.loads(m.group(0)); out = {}
                    for k, v in d.items():
                        try: i = int(k)
                        except Exception: continue
                        if 0 <= i < len(pairs) and v in allowed: out[(pairs[i][0], pairs[i][2])] = v
                    return out
                except Exception: pass
    return {}

@app.post("/api/ont/reground")
def ont_reground():
    """关系接地:把通用『关联』关系用 LLM 标注具体动词(受控词表)→ 映射到 BFO 有根据关系,回写 IR。
    这把完备度里『关系接地率』那 20% 拉起来;引擎离线则不动(不臆造动词)。"""
    body = request.json or {}; key = body.get("graph")
    ir, wp, err = _open_writable(key)
    if err: return err
    rels = ir.get("links") if isinstance(ir.get("links"), list) and ir.get("links") else ir.get("relations")
    if not isinstance(rels, list) or not rels: return jsonify({"ok": True, "grounded": 0, "note": "无关系"})
    cn = {(o.get("name") or o.get("id")): (o.get("cn") or o.get("name") or o.get("id")) for o in ir.get("objects", [])}
    todo = [r for r in rels if (r.get("verb") or "关联") in ("", "关联")]
    if todo:
        try:
            from agent_runtime import available
            if not available(): return jsonify({"error": "智能引擎离线,无法标注关系动词(不臆造)"}), 503
        except Exception:
            return jsonify({"error": "智能引擎不可用"}), 503
    grounded = 0
    for i in range(0, len(todo), 30):
        chunk = todo[i:i + 30]
        pairs = [(s, cn.get(s, s), t, cn.get(t, t)) for r in chunk for (s, t) in [_rel_ends(r)]]
        vmap = _bounded(lambda p=pairs: _llm_verbs(p), 120) or {}
        for r in chunk:
            s, t = _rel_ends(r); v = vmap.get((s, t))
            if v:
                r["verb"] = v; fr, tq = _ground_verb(v); r["founded_relation"] = fr; r["temporal"] = tq
                grounded += 1
    for r in rels:                                        # 所有关系补齐 founded_relation 字段(含已具体动词的)
        if not r.get("founded_relation"):
            fr, tq = _ground_verb(r.get("verb", "关联")); r["founded_relation"] = fr; r["temporal"] = tq
    with _WRITE_LOCK: _atomic_json(wp, ir)
    return jsonify({"ok": True, "grounded": grounded, "targets": len(todo),
                    "note": f"已为 {grounded}/{len(todo)} 条关系标注具体动词并接地到 BFO"})

@app.post("/api/ont/maturity")
def ont_maturity():
    """成熟度人审:把对象成熟度置为 Provisional/Released/Deprecated(IOF 治理),回写 IR。"""
    body = request.json or {}; key = body.get("graph")
    oid = (body.get("object") or "").strip(); mat = (body.get("maturity") or "").strip()
    if mat not in ("Provisional", "Released", "Deprecated"):
        return jsonify({"error": "maturity 仅 Provisional/Released/Deprecated"}), 400
    ir, wp, err = _open_writable(key)
    if err: return err
    o = next((x for x in ir.get("objects", []) if x.get("name") == oid or x.get("id") == oid), None)
    if not o: return jsonify({"error": "对象不存在"}), 404
    o["maturity"] = mat
    with _WRITE_LOCK: _atomic_json(wp, ir)
    return jsonify({"ok": True, "object": oid, "maturity": mat})

@app.get("/api/ont/metadata")
def ont_metadata():
    """拼音↔中文字典(对齐平台 /api/metadata / export_metadata)"""
    ir = load_ir_edited(request.args.get("graph", "demo")) or {}
    dic = {}
    for o in ir.get("objects", []):
        dic[o.get("name") or o.get("id")] = o.get("cn")
        for a in o.get("attrs", []):
            if a.get("cn"): dic[f'{o.get("id")}.{a["col"]}'] = a["cn"]
    return jsonify({"count": len(dic), "dict": dic})

@app.post("/api/ont/rebuild")
def ont_rebuild():
    """重建:清空该图谱的草案编辑层,回到构建产物基线(对齐平台 /api/rebuild)"""
    key = (request.json or {}).get("graph", "demo")
    ep = _edits_path(key)
    if os.path.exists(ep): os.remove(ep)
    ir = load_ir(key)
    return jsonify({"ok": True, "objects": len(ir.get("objects", [])) if ir else 0})

@app.post("/api/sparql")
def sparql():
    """原生 SPARQL:优先代理经典引擎(rdflib);未就绪时对当前 IR 做轻量三元组匹配兜底"""
    body = request.json or {}
    query = body.get("query", ""); key = body.get("graph", "demo")
    # 拒绝 SERVICE 联邦子句:rdflib 会向任意 URL 发外连,构成 SSRF
    if re.search(r"\bSERVICE\b", query, re.I):
        return jsonify({"error": "出于安全,SPARQL SERVICE 联邦查询已禁用"}), 400
    # 拒绝 FROM/FROM NAMED 引外部 IRI(file:///、http(s)://…):防经数据集子句读本地文件或外连(SSRF/LFI)
    if re.search(r"\bFROM\b[\s\w]*<\s*(?:file|https?|ftp)\s*:", query, re.I):
        return jsonify({"error": "出于安全,SPARQL FROM 外部数据集(file/http)已禁用"}), 400
    try:
        import rdflib
        ir = load_ir_edited(key)
        if not ir: return jsonify({"error": f"图谱不存在: {key}"}), 404
        # 用本系统的 IOF 注释化 Turtle 作 substrate:让 iof-av 定义/反例/BFO 归类/关系接地都可被 SPARQL 查询
        g = rdflib.Graph(); g.parse(data=_ir_to_turtle(key, ir), format="turtle")
        def _run():
            with _RDF_LOCK:   # 串行化 SPARQL 解析+执行,规避 pyparsing 语法状态并发污染
                qres = g.query(query)
                vs = [str(v) for v in (qres.vars or [])]
                return vs, [{v: str(r[i]) for i, v in enumerate(vs)} for r in qres][:500]
        out, err, timed_out = _bounded_ex(_run, 8, default=None)   # 限时执行,防止病态查询占满 CPU
        if timed_out:
            return jsonify({"error": "SPARQL 执行超时(>8s):查询过重或图谱过大,请增加过滤或 LIMIT"}), 400
        if err is not None:   # 语法/语义错误——如实回报,不再伪装成超时
            return jsonify({"error": f"SPARQL 查询错误: {str(err)[:200]}"}), 400
        vars_, rows = out
        return jsonify({"type": "SELECT", "vars": vars_, "rows": rows, "total": len(rows), "graph": key})
    except Exception as e:
        return jsonify({"error": f"SPARQL 失败: {str(e)[:200]}"}), 400

@app.post("/api/query")
def query():
    body = request.json or {}
    sql = body.get("sql", "")
    src = body.get("src") or "demo"
    if not sql_is_readonly(sql): return jsonify({"error": "仅允许只读 SELECT/WITH 查询"}), 400
    try:
        if src in ("demo", ""): return jsonify(q(sql, attach_uploads=True))
        db, nm = _resolve_src(src)
        if db: return jsonify(q(sql, db=db))
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 SQL 工作台直查外部库
            return jsonify({"live": True, **_ext_query(conn, sql)})
        return jsonify({"error": f"数据源「{nm}」不可查询"}), 400
    except Exception as e: return jsonify({"error": str(e)}), 400

_QA_CACHE = {}   # 深度问数结果缓存:相同问题+上下文秒回(引擎慢,缓存显著提速)
def _qa_key(question, history, focus=None):
    import hashlib
    up_sig = ""                                          # 上传库指纹:上传数据变更后作废旧缓存,避免同名表复用陈旧结果
    try:
        if os.path.exists(UPLOAD_DB): up_sig = str(int(os.path.getmtime(UPLOAD_DB)))
    except Exception: pass
    # 本体编辑指纹:本体是问数的语义锚点(召回/口径/双盲全靠它),改了本体却复用旧答案,
    # 用户会持续拿到旧语义下的结果——别名新增后仍答不上就是这么来的(实测发现)
    ont_sig = ""
    try:
        _ep = _edits_path("demo")
        if os.path.exists(_ep): ont_sig = str(int(os.path.getmtime(_ep)))
    except Exception: pass
    sig = json.dumps([question, [h.get("q", "") for h in (history or [])[-2:]], sorted(focus or []), up_sig, ont_sig], ensure_ascii=False)
    return hashlib.md5(sig.encode("utf-8")).hexdigest()

@app.post("/api/chat")
def chat():
    body = request.json or {}
    question = body.get("q", "").strip()
    history = body.get("history") or []          # [{q, summary}] 最近几轮
    if not question: return jsonify({"error": "empty"}), 400
    if not body.get("nocache"):
        hit = _QA_CACHE.get(_qa_key(question, history))
        if hit: return jsonify({**hit, "cached": True})
    steps = [{"step": "load_ontology", "ok": True, "info": "示例本体上下文"}]
    ir_gate = load_ir_edited("demo") or {}
    q_eff, co = _carryover(question, history, ir_gate)          # B5 指代延续
    if co: steps.append({"step": "coreference", "ok": True, "info": f"多轮指代 · 延续上文对象:{co}"})
    ctx = build_context(q_eff)
    if history:
        hist_txt = "\n".join(f"上轮问: {h.get('q','')}\n上轮结果摘要: {h.get('summary','')[:800]}" for h in history[-2:])
        ctx = f"[对话历史,供追问理解指代]\n{hist_txt}\n\n[库结构]\n{ctx}"
        steps.append({"step": "load_history", "ok": True, "info": f"带入 {len(history[-2:])} 轮上下文"})
    steps.append({"step": "build_context", "ok": True, "info": f"{len(ctx)}字符 schema"})
    plan = None
    _sk = _match_qa_skill(question)                   # #5 沉淀技能复用(与流式链同语义)
    if _sk:
        plan = {"analyses": (_sk.get("analyses") or [])[:4], "note": "沉淀技能复用"}
        steps.append({"step": "skill_reuse", "ok": True, "info": f"命中沉淀技能,复用 {len(plan['analyses'])} 条已验证 SQL"})
    # 硬性时间预算:引擎 90s 内不出计划就走内置模板,保证请求永不卡死
    if plan is None:
        plan = _bounded(lambda: agent_sql_plan(q_eff, ctx, steps), 90)
    if not plan:
        steps.append({"step": "plan_timeout", "ok": False, "info": "引擎超时/离线 → 内置模板兜底"})
        plan = fallback_plan(question)
    results = []
    for a in (plan.get("analyses") or [])[:4]:
        sql = a.get("sql", "")
        if not sql_is_readonly(sql):
            steps.append({"step": "exec_sql", "ok": False, "info": "非只读SQL被拒: " + sql[:60]}); continue
        okv, why = _validate_sql_ontology(sql, ir_gate)          # A2 口径拦截
        if not okv:
            steps.append({"step": "ontology_gate", "ok": False, "info": "口径拦截:" + why}); continue
        # DR-026 双盲意图检测:口径闸管「SQL 合不合规」,这里管「答的是不是问的那件事」。
        # 只观测不阻断——确定性反解也会有漏判(如口径卡走视图名),
        # 因误判挡住正确答案的代价远高于标注一句存疑。
        try:
            import intent_check, usage_stat
            _ic = intent_check.cross_check(question, sql, ir_gate)
            steps.append(intent_check.step_of(_ic))
            # DR-026 使用度埋点:复用双盲已反解出的对象,零额外解析开销;失败静默(旁路)
            usage_stat.record(WORK, "demo", [o["key"] for o in _ic["actual"]["objects"]], "query")
        except Exception:
            pass
        try:
            data = q(sql)
            steps.append({"step": "exec_sql", "ok": True, "info": f'{a.get("title","")} → {len(data["rows"])}行'})
            results.append({"title": a.get("title", "分析"), "sql": sql, "chart": a.get("chart") or {}, "data": data})
        except Exception as e:
            steps.append({"step": "exec_sql", "ok": False, "info": f'{a.get("title","")}: {str(e)[:100]}'})
    if results:
        text = _bounded(lambda: narrative_llm(question, results, steps), 50) or _rule_summary(results)
    else:
        text = "查询均失败,请换个问法或检查指标是否绑表。"
    summary = "; ".join(f"{r['title']}[{r['sql'][:120]}]→{len(r['data']['rows'])}行,末行{json.dumps(r['data']['rows'][-1] if r['data']['rows'] else {}, ensure_ascii=False)[:150]}" for r in results)[:1200]
    resp = {"steps": steps, "results": results, "narrative": text, "note": plan.get("note", ""),
            "summary": summary, "metric_cards": _metric_cards(question, ir_gate)}
    if results:                                   # 仅缓存成功结果;上限 200 条,超出清最早
        if len(_QA_CACHE) > 200: _QA_CACHE.pop(next(iter(_QA_CACHE)))
        _QA_CACHE[_qa_key(question, history)] = resp
    return jsonify(resp)

@app.post("/api/chat/stream")
def chat_stream():
    """深度问数流式:SSE 逐条推送执行步骤(对齐平台 chat-bi 的实时执行记录),末尾 done 事件带完整结果。"""
    body = request.json or {}
    question = (body.get("q") or "").strip()
    history = body.get("history") or []
    nocache = body.get("nocache")
    # default=str:即便某列是 BLOB/bytes 等不可 JSON 序列化的值,也不会让整条 SSE 流因异常静默中断(前端卡在"运行中")
    def sse(obj): return "data: " + json.dumps(obj, ensure_ascii=False, default=str) + "\n\n"
    def gen():
        import time as _t
        t0 = _t.time(); session = uuid.uuid4().hex[:8]
        if not question:
            yield sse({"type": "error", "error": "empty"}); return
        focus_tables = list(body.get("tables") or [])
        # 选中的图谱→其绑定表并入范围(否则"按图谱选数据源"是静默空操作)
        for gk in (body.get("graphs") or []):
            gir = load_ir(gk) or {}
            for o in gir.get("objects", []):
                for t in (o.get("tables") or ([o.get("table")] if o.get("table") else [])):
                    if t and t not in focus_tables: focus_tables.append(t)
        if not nocache:
            hit = _QA_CACHE.get(_qa_key(question, history, focus_tables))
            if hit:
                yield sse({"type": "done", **hit, "cached": True, "elapsed": 0.0, "session": session}); return
        def stp(step, ok, info=""):
            return {"step": step, "ok": ok, "info": info, "ts": _t.strftime("%H:%M:%S")}
        steps = []
        def push(step, ok, info=""):
            s = stp(step, ok, info); steps.append(s); return sse({"type": "step", **s})
        if focus_tables:
            yield push("scope_source", True, f"数据源限定 · {len(focus_tables)} 张表")
        yield push("load_ontology", True, "加载 示例 本体上下文")
        ir_gate = load_ir_edited("demo") or {}
        q_eff, co = _carryover(question, history, ir_gate)      # B5 多轮指代:上文本体对象延续
        if co:
            yield push("coreference", True, f"多轮指代 · 延续上文对象:{co}")
        _exp = expand_terms(q_eff)                               # A1 术语扩展检索(术语管理词典)
        if _exp:
            yield push("term_expand", True, f"术语扩展 · 词典命中 {len(_exp)} 个同义/中英对照词:{'、'.join(_exp[:6])}{'…' if len(_exp) > 6 else ''}")
        ctx = build_context(q_eff, focus_tables=focus_tables)
        if history:
            hist_txt = "\n".join(f"上轮问: {h.get('q','')}\n上轮结果摘要: {h.get('summary','')[:800]}" for h in history[-2:])
            ctx = f"[对话历史,供追问理解指代]\n{hist_txt}\n\n[库结构]\n{ctx}"
            yield push("load_history", True, f"带入 {len(history[-2:])} 轮上下文")
        ntab = ctx.count("\n表 ") + (1 if ctx.startswith("表 ") else 0)
        yield push("match_schema", True, f"匹配相关表/指标 · 命中 {ntab} 张表")
        _nrel = ctx.count("⋈")
        yield push("ontology_relations", True,
                   f"沿本体关系召回 · {_nrel} 条已验证关系作 JOIN 依据" if _nrel else "选中表间暂无已验证关系,JOIN 由引擎按列名推断")
        yield push("build_context", True, f"组装库结构上下文 · {len(ctx)} 字符")
        plan = None
        _sk = _match_qa_skill(question)               # #5 沉淀技能复用:命中即免引擎规划(秒级)
        if _sk:
            plan = {"analyses": (_sk.get("analyses") or [])[:4], "note": "沉淀技能复用"}
            yield push("skill_reuse", True, f"命中沉淀技能「{(_sk.get('question') or '')[:24]}」· 复用 {len(plan['analyses'])} 条已验证 SQL,跳过引擎规划")
        else:
            yield sse({"type": "status", "text": "智能引擎生成 SQL 分析计划(约 40-90s)…"})
        # 引擎计划:后台线程跑,主循环轮询 plan_steps 实时外推 + 心跳
        plan_steps, box = [], {"v": None, "done": False}
        def _run():
            try: box["v"] = agent_sql_plan(q_eff, ctx, plan_steps)
            except Exception: pass
            box["done"] = True
        if plan is not None:
            box["done"] = True                        # 技能复用命中:不启动引擎
        else:
            th = threading.Thread(target=_run, daemon=True); th.start()
        emitted = 0; waited = 0.0
        while not box["done"] and waited < 95:
            th.join(1.0); waited += 1.0
            while emitted < len(plan_steps):
                s = plan_steps[emitted]; emitted += 1; steps.append(s)
                yield sse({"type": "step", **({"ts": _t.strftime("%H:%M:%S")}), **s})
            if not box["done"] and int(waited) % 4 == 0:
                yield sse({"type": "status", "text": f"引擎推理中… {int(waited)}s"})
        while emitted < len(plan_steps):
            s = plan_steps[emitted]; emitted += 1; steps.append(s)
            yield sse({"type": "step", **s})
        plan = plan or box["v"]
        if not plan:
            yield push("plan_timeout", False, "引擎超时/离线 → 内置模板兜底")
            plan = fallback_plan(question)
        analyses = (plan.get("analyses") or [])[:4]
        yield push("plan_ready", True, f"分析计划就绪 · 拆解为 {len(analyses)} 个子分析")
        results = []
        for idx, a in enumerate(analyses, 1):
            title = a.get("title", "分析")
            sql = a.get("sql", "")
            yield push("gen_sql", True, f"[{idx}/{len(analyses)}] 生成 SQL 规范:{title}")
            if not sql_is_readonly(sql):
                yield push("exec_sql", False, f"[{idx}] 非只读SQL被拒: " + sql[:50]); continue
            okv, why = _validate_sql_ontology(sql, ir_gate)      # A2 口径拦截:表白名单 + JOIN 键须落本体关系
            if not okv:
                yield push("ontology_gate", False, f"[{idx}] 口径拦截:{why}"); continue
            yield push("ontology_gate", True, f"[{idx}] 本体校验通过 · 表与 JOIN 键均在本体边界内")
            try:                                     # DR-026 双盲意图检测(只观测不阻断)+ 使用度埋点
                import intent_check, usage_stat
                _ic = intent_check.cross_check(question, sql, ir_gate)
                yield push("intent_crosscheck", _ic["verdict"] in ("aligned", "unknown"),
                           f"[{idx}] " + intent_check.step_of(_ic)["info"])
                usage_stat.record(WORK, "demo", [o["key"] for o in _ic["actual"]["objects"]], "query")
            except Exception:
                pass
            try:
                data = q(sql, attach_uploads=True)
                nrow = len(data["rows"])
                yield push("exec_sql", True, f"[{idx}] 执行查询 → {nrow} 行 × {len(data['columns'])} 列")
                conf = 95 if nrow >= 6 else (70 if nrow >= 2 else 30)
                yield push("data_check", nrow > 0, f"[{idx}] 数据质量校验:{'通过' if nrow>=2 else '数据偏少'} (confidence={conf}%)")
                ct = (a.get("chart") or {}).get("type", "line")
                yield push("gen_chart", True, f"[{idx}] 生成图表规范:{ct} 图")
                one = {"title": title, "sql": sql, "chart": a.get("chart") or {}, "data": data}
                results.append(one)
                yield sse({"type": "result", "index": idx - 1, "result": one})   # 渐进推送:每完成一图即出
            except Exception as e:
                yield push("exec_sql", False, f"[{idx}] {title}: {str(e)[:90]}")
        if results:
            yield push("review", True, f"审视分析结果 · 检查数据完整性与口径 · {len(results)}/{len(analyses)} 项有效")
            yield sse({"type": "status", "text": "汇总数据,生成业务洞察报告…"})
            # B6 叙述流式:后台线程生成,片段进 chunks;主循环轮询逐段外推 narrative_delta
            chunks, nbox = [], {"v": None, "done": False}
            def _run_n():
                try: nbox["v"] = narrative_llm(question, results, steps, emit=chunks.append)
                except Exception: pass
                nbox["done"] = True
            nth = threading.Thread(target=_run_n, daemon=True); nth.start()
            n_emit, n_wait = 0, 0.0
            while not nbox["done"] and n_wait < 55:
                nth.join(0.5); n_wait += 0.5
                while n_emit < len(chunks):
                    yield sse({"type": "narrative_delta", "text": chunks[n_emit]}); n_emit += 1
            while n_emit < len(chunks):
                yield sse({"type": "narrative_delta", "text": chunks[n_emit]}); n_emit += 1
            text = nbox["v"] or _rule_summary(results)
            yield push("narrative", True, f"生成分析报告 · {len(text)} 字")
        else:
            yield push("review", False, "无有效数据,无法生成报告")
            text = "查询均失败,请换个问法或检查指标是否绑表。"
        summary = "; ".join(f"{r['title']}[{r['sql'][:120]}]→{len(r['data']['rows'])}行,末行{json.dumps(r['data']['rows'][-1] if r['data']['rows'] else {}, ensure_ascii=False)[:150]}" for r in results)[:1200]
        resp = {"steps": steps, "results": results, "narrative": text, "note": plan.get("note", ""),
                "summary": summary, "metric_cards": _metric_cards(question, ir_gate)}
        if results:
            if len(_QA_CACHE) > 200: _QA_CACHE.pop(next(iter(_QA_CACHE)))
            _QA_CACHE[_qa_key(question, history, focus_tables)] = resp
        yield sse({"type": "done", **resp, "cached": False, "elapsed": round(_t.time() - t0, 1), "session": session})
    from flask import Response, stream_with_context
    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

_QA_SKILLS_F = os.path.join(WORK, "qa_skills.json")

def _match_qa_skill(question):
    """#5 沉淀技能复用(修「存而不用」):问题归一化后与沉淀技能匹配(等值或互为包含),
    命中即复用其已验证 SQL、跳过引擎规划 —— 沉淀自此参与回答,不再只是展示品。"""
    try: store = json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else []
    except Exception: return None
    norm = lambda s: re.sub(r"[\s??。.,,、;;::!!()()\-—]+", "", str(s or "")).lower()
    qn = norm(question)
    if len(qn) < 4: return None
    for sk in store:
        sn = norm(sk.get("question"))
        if sn and (sn == qn or (len(sn) >= 6 and (sn in qn or qn in sn))) and sk.get("analyses"):
            return sk
    return None
@app.post("/api/diagnose/stream")
def diagnose_stream():
    """最小根因诊断(P21 四步的前三步):本体识别意图 → 沿本体关系召回 → schema 约束生成(≤3 根因+检查清单)。
    输出必须落在本体边界内:根因路径只允许引用召回的对象/关系,越界降 candidate;引擎离线则如实拒答(不臆造)。"""
    q = ((request.json or {}).get("q") or "").strip()
    pipeline = bool((request.json or {}).get("pipeline"))   # M3 opt-in:拆成 诊断→(确定性评估)→方案,每节点单一职责
    def sse(o): return "data: " + json.dumps(o, ensure_ascii=False, default=str) + "\n\n"
    def gen():
        import time as _t
        def push(step, ok, info=""):
            return sse({"type": "step", "step": step, "ok": ok, "info": info, "ts": _t.strftime("%H:%M:%S")})
        if not q:
            yield sse({"type": "error", "error": "请描述异常现象"}); return
        ir = load_ir_edited("demo") or {}
        objs, links = ir.get("objects", []), ir.get("links", [])
        # ① 本体识别意图:关键词对齐 对象(cn/表名) 与 指标
        def _score(o):
            cands = set()
            cn = (o.get("cn") or "").strip()
            if cn:
                cands.add(cn)
                for suf in ("事实表", "维度表", "汇总表", "明细表", "表"):   # 「退货事实表」也按「退货」匹配(与指代延续同套剥离)
                    if cn.endswith(suf) and len(cn) > len(suf) + 1: cands.add(cn[:-len(suf)])
            for v in (o.get("table") or "", o.get("name") or ""):
                if v: cands.add(str(v))
            best = 0
            for c in cands:
                if len(c) >= 2 and (c in q or c.lower() in q.lower()): best = max(best, len(c))
            return best
        ents = sorted(((_score(o), o) for o in objs), key=lambda x: -x[0])
        ents = [o for sc, o in ents[:2] if sc > 0]
        mets = []
        for k, arr in (ir.get("metric_layers") or {}).items():
            for m in arr:
                if m.get("name") and m["name"] in q: mets.append(m)
        if not ents:
            yield push("intent", False, "未在本体中识别到相关对象——请在问题里带上业务对象名(如 生产工单/设备/产线)")
            yield sse({"type": "error", "error": "本体中未命中实体,诊断需先锚定对象(不作无锚定的自由生成)"}); return
        yield push("intent", True, "本体识别意图 · 实体:" + "、".join(o.get("cn") or o["id"] for o in ents)
                   + (" · 指标:" + "、".join(m["name"] for m in mets[:3]) if mets else "") + " · 类型:异常根因诊断")
        # ② 沿本体关系召回:从命中实体出发 BFS 2 跳
        adj = {}
        for l in links:
            if l.get("status") in ("rejected",): continue
            adj.setdefault(l.get("source"), []).append(l); adj.setdefault(l.get("target"), []).append(l)
        seen = {o["id"] for o in ents}; used_links = []; frontier = list(seen)
        for _hop in range(2):
            nxt = []
            for oid in frontier:
                for l in adj.get(oid, []):
                    other = l["target"] if l["source"] == oid else l["source"]
                    if l not in used_links and len(used_links) < 14: used_links.append(l)
                    if other not in seen and len(seen) < 8:
                        seen.add(other); nxt.append(other)
            frontier = nxt
        o_by_id = {o["id"]: o for o in objs}
        rel_objs = [o_by_id[i] for i in seen if i in o_by_id]
        rel_lines = []
        for l in used_links:
            sc, tc = (o_by_id.get(l["source"], {}).get("cn") or l["source"]), (o_by_id.get(l["target"], {}).get("cn") or l["target"])
            rel_lines.append(f"{sc} --{l.get('verb','关联')}({l.get('status')})--> {tc}")
        yield push("recall", True, f"沿本体关系召回 · 关联对象 {len(rel_objs)} 个 · 关系 {len(used_links)} 条(2 跳)")
        # ③ schema 约束生成(LLM 只能在召回边界内作答)
        allowed = sorted({(o.get("cn") or o["id"]) for o in rel_objs})
        schema = "\n".join(f"表 {o.get('table')}({o.get('cn')}): " + ", ".join(a_.get("cn") or a_["col"] for a_ in (o.get("attrs") or [])[:10]) for o in rel_objs if o.get("table"))
        yield sse({"type": "status", "text": "受本体边界约束生成候选根因(约 30-90s)…"})
        prompt = f"""你是制造业根因诊断专家。仅依据下面给定的本体对象、关系与表结构,诊断异常。
异常现象:{q}
[允许引用的对象(白名单,路径只能用这些)]:{"、".join(allowed)}
[对象间关系(带验证状态)]:
{chr(10).join(rel_lines)}
[表结构]:
{schema[:4000]}
只输出一个 JSON(无其它文字):
{{"causes":[{{"cause":"候选根因(一句话)","path":"对象A→对象B(只用白名单对象名)","evidence":"依据哪张表/字段/关系","confidence":"verified|candidate"}}],
  "checklist":["现场检查项(短句)"]}}
要求:causes 至多 3 条;凡依据的关系状态非 verified、或仅单表佐证,confidence 必须为 candidate;checklist 3-5 条;不得引用白名单之外的对象或编造字段。"""
        ans = None
        try:
            from agent_runtime import get_runtime, available
            for drv in _drv_order():
                if drv not in available(): continue
                ok, reply = _bounded(lambda d=drv: _llm_turn(get_runtime(d), f"dg_{uuid.uuid4().hex[:6]}", prompt, 150, task="diagnose"), 170, (False, "")) or (False, "")
                if ok and reply:
                    m = re.search(r"\{[\s\S]*\}", reply)
                    if m:
                        try: ans = json.loads(m.group(0)); break
                        except Exception: pass
        except Exception: pass
        if not isinstance(ans, dict) or not ans.get("causes"):
            yield push("llm_causes", False, "引擎超时/离线 —— 根因诊断需引擎在线,不作无证据的臆造")
            yield sse({"type": "error", "error": "引擎不可用,本次不产出根因(反造假纪律:宁可不答,不编结论)"}); return
        # ④ 边界校验:路径越界 → 降 candidate
        allow_set = set(allowed)
        causes = _dg_bounds(ans.get("causes"), allow_set)[:3]          # G1 边界校验(确定性)
        checklist = [str(x)[:60] for x in (ans.get("checklist") or [])[:5]]
        cl_flagged = []
        if pipeline:
            # M3 流水线:诊断已出 → 确定性评估排序 → 方案节点只据已排序根因出清单 → G2 校验
            causes = _dg_rank(causes)
            yield push("assess", True, "评估(确定性打分):" + " | ".join(f'#{c["rank"]} {c["cause"][:18]}' for c in causes[:3]))
            _top = "\n".join(f'#{c["rank"]} {c["cause"]}(路径 {c["path"]};依据 {c["evidence"]})' for c in causes[:3])
            p2 = ("你只负责一件事:为下列已确认的候选根因给出现场检查清单。不要重新诊断、不要改动根因。\n"
                  "[已排序候选根因]\n" + _top + "\n[允许引用的对象]:" + "、".join(allowed) + "\n"
                  '只输出 JSON:{"checklist":["现场检查项(短句,只引用上面对象或真实表名)"]}  3-5 条。')
            yield sse({"type": "status", "text": "方案节点:仅据已排序根因生成检查清单…"})
            _cl = None
            try:
                for drv in _drv_order():          # get_runtime/available 已在上方诊断节点导入,复用
                    if drv not in available(): continue
                    ok2, rp2 = _bounded(lambda d=drv: _llm_turn(get_runtime(d), f"dg2_{uuid.uuid4().hex[:6]}", p2, 90, task="diagnose"), 110, (False, "")) or (False, "")
                    if ok2 and rp2:
                        mm = re.search(r"\{[\s\S]*\}", rp2)
                        if mm:
                            try: _cl = json.loads(mm.group(0)); break
                            except Exception: pass
            except Exception: pass
            if isinstance(_cl, dict) and _cl.get("checklist"):
                _tbls = [o.get("table") for o in rel_objs if o.get("table")]
                kept, cl_flagged = _dg_checklist_gate(_cl.get("checklist"), allow_set, _tbls)   # G2 清单校验
                checklist = kept
                yield push("plan_gate", True, f"方案节点 · 清单 {len(kept)} 条通过" + (f" · {len(cl_flagged)} 条引用未知对象已标记" if cl_flagged else ""))
            else:
                yield push("plan_gate", False, "方案节点失败/超时 → 沿用诊断节点的清单(不臆造)")
        n_out = sum(1 for c in causes if not c["in_bounds"])
        yield push("bounds_check", True, f"本体边界校验 · {len(causes)} 条根因" + (f" · {n_out} 条路径越界已降 candidate" if n_out else " · 全部在界内"))
        yield sse({"type": "done", "entity": "、".join(o.get("cn") or o["id"] for o in ents),
                   "metrics": [m["name"] for m in mets[:3]], "causes": causes, "checklist": checklist,
                   "relations_used": len(used_links), "pipeline": pipeline,
                   "checklist_flagged": cl_flagged})
    from flask import Response
    return Response(gen(), mimetype="text/event-stream")

# ── 引擎设置(DR-017):运行时/模型/API Key 实时切换;Key 只写不回读(掩码),文件 0600 ──
ENGINE_CFG_F = os.path.join(WORK, "engine_config.json")
ENGINE_KEY_VARS = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY",
                   "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY"]
ENGINE_MODEL_OPTS = {
    # 全部经 claude CLI 实测可用(2026-07-25);别名 opus/sonnet/haiku 自动跟踪最新版(opus 现解析到 claude-opus-5)
    "claude-code": ["claude-opus-5", "claude-opus-4-8", "claude-sonnet-5", "claude-fable-5",
                    "claude-haiku-4-5-20251001", "opus", "sonnet", "haiku"],
    "hermes": ["gpt-5.5", "gpt-5.3-codex", "glm-4.6", "deepseek-v3", "kimi-k2"],
    "openclaw": ["openai/gpt-5.5", "openai/gpt-5.3-codex"],
}
ENGINE_HERMES_PROVIDERS = ["", "openai-codex", "zai", "deepseek", "moonshot", "qwen-oauth"]
# B4 按任务选模:每个环节可配独立模型(空=跟随当前引擎缺省)。SQL 计划/叙述可用轻量模型提速,诊断可用强模型保质。
ENGINE_TASKS = ["plan", "narrative", "diagnose"]
ENGINE_TASK_CN = {"plan": "SQL 计划生成", "narrative": "洞察叙述", "diagnose": "根因诊断"}

def _load_engine_cfg():
    try: return json.load(open(ENGINE_CFG_F))
    except Exception: return {}

def _save_engine_cfg(cfg):
    _atomic_json(ENGINE_CFG_F, cfg)
    try: os.chmod(ENGINE_CFG_F, 0o600)      # 含密钥,仅本用户可读
    except Exception: pass

def _apply_engine_cfg(cfg):
    """配置 → 进程环境,立即生效(_cmd 调用时读 env);清运行时实例缓存使模型切换即时。"""
    for k, var in (("driver", "CLAW_DRIVER"), ("claude_model", "CLAUDE_MODEL"),
                   ("hermes_model", "HERMES_MODEL"), ("hermes_provider", "HERMES_PROVIDER")):
        v = cfg.get(k)
        if v: os.environ[var] = v
        elif k != "driver" and v == "": os.environ.pop(var, None)
    for var, val in (cfg.get("keys") or {}).items():
        if var not in ENGINE_KEY_VARS: continue
        # 环境变量优先:部署时由运维注入的 env 不被本地配置文件覆盖
        if os.environ.get(var): continue
        if val: os.environ[var] = val
        else: os.environ.pop(var, None)
    _RT_CACHE.clear()

_apply_engine_cfg(_load_engine_cfg())        # 启动即应用持久化配置(覆盖 start.sh 缺省)

def _mask_key(v):
    return "" if not v else ("*" * 6 + v[-4:] if len(v) > 8 else "*" * len(v))

@app.get("/api/engine/config")
def engine_config_get():
    # 副作用导入:serve_claw 在 import 时把 openclaw 注册进运行时表。用 import_module 表达
    # "只为副作用",既不留未使用绑定(静态检查干净),也让意图对读者显式。
    try: importlib.import_module("serve_claw")
    except Exception: pass
    from agent_runtime import available
    cfg = _load_engine_cfg()
    keys = cfg.get("keys") or {}
    return jsonify({
        "driver": os.environ.get("CLAW_DRIVER", "hermes"),
        "runtimes": available(),
        "models": {"claude-code": os.environ.get("CLAUDE_MODEL", "claude-opus-4-8"),
                   "hermes": os.environ.get("HERMES_MODEL", ""),
                   "openclaw": os.environ.get("OPENCLAW_MODEL", "")},
        "hermes_provider": os.environ.get("HERMES_PROVIDER", ""),
        "model_options": ENGINE_MODEL_OPTS, "hermes_providers": ENGINE_HERMES_PROVIDERS,
        "task_models": {t: (cfg.get("task_models") or {}).get(t, "") for t in ENGINE_TASKS},
        "keys": {v: _mask_key(keys.get(v) or os.environ.get(v, "")) for v in ENGINE_KEY_VARS}})

@app.post("/api/engine/config")
def engine_config_set():
    """部分更新:driver / 各运行时模型 / hermes provider / API keys(空串=清除)。持久化+即时生效。"""
    from agent_runtime import available
    body = request.json or {}
    cfg = _load_engine_cfg()
    if "driver" in body:
        if body["driver"] not in available():
            return jsonify({"error": f"无此运行时: {body['driver']}", "available": available()}), 400
        cfg["driver"] = body["driver"]
    for k in ("claude_model", "hermes_model", "hermes_provider"):
        if k in body: cfg[k] = str(body[k]).strip()[:80]
    if isinstance(body.get("task_models"), dict):        # B4 按任务选模(空串=清除该任务覆盖)
        tm = cfg.setdefault("task_models", {})
        for t, v in body["task_models"].items():
            if t not in ENGINE_TASKS: return jsonify({"error": f"未知任务: {t}", "tasks": ENGINE_TASKS}), 400
            v = str(v or "").strip()[:80]
            if v: tm[t] = v
            else: tm.pop(t, None)
    if isinstance(body.get("keys"), dict):
        ks = cfg.setdefault("keys", {})
        for var, val in body["keys"].items():
            if var not in ENGINE_KEY_VARS: return jsonify({"error": f"不支持的 Key 变量: {var}"}), 400
            val = str(val or "").strip()
            if val: ks[var] = val[:200]
            else:
                ks.pop(var, None); os.environ.pop(var, None)   # 清除须同步弹出进程 env
    _save_engine_cfg(cfg)
    _apply_engine_cfg(cfg)
    return engine_config_get()

@app.post("/api/engine/test")
def engine_test():
    """连通性测试:对指定运行时跑一条最小指令,回真实延迟或真实报错(切换前先测,best practice)。"""
    drv = (request.json or {}).get("driver", "")
    from agent_runtime import get_runtime, available
    if drv not in available(): return jsonify({"error": "无此运行时"}), 400
    t0 = time.time()
    try:
        ok, reply = get_runtime(drv).run_turn(f"tst_{uuid.uuid4().hex[:6]}", "只回复两个字:在线", timeout=60)
    except Exception as e:
        ok, reply = False, str(e)
    if ok and _looks_like_error(str(reply or "")):
        ok = False
    return jsonify({"ok": bool(ok), "seconds": round(time.time() - t0, 1),
                    "reply": str(reply or "")[:200]})

# ── C9 问数评测(P20 落地):金标题集 × 三臂对照(A朴素 / B图谱 / C本体全量),自动判分 ──
_EVAL_SET_F = os.path.join(HERE, "benchmark", "qa_set.json")
_EVAL_RES_F = os.path.join(WORK, "eval_results.json")
EVAL_JOB = {"running": False, "progress": "", "done": 0, "total": 0, "started": ""}

def _eval_items():
    try: return (json.load(open(_EVAL_SET_F)) or {}).get("items") or []
    except Exception: return []

def _ctx_naive():
    """A 臂:朴素 Text2SQL 基线 —— 只有英文表名+列名(截断),无中文语义/无关系/无指标。"""
    lines = []
    try:
        con = ro_connect(DB)
        for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')][:10]
            lines.append(f"{t}({', '.join(cols)})")
        con.close()
    except Exception:
        pass
    out = "\n".join(lines)
    return out[:4000]

def _ctx_graph(question):
    """B 臂:GraphRAG 式 —— 相关表+中文列注+本体关系 JOIN 提示,无指标层/无术语扩展。"""
    ir = load_ir_edited("demo") or {}
    kws = [w for w in re.split(r"[,，。？?\s]+", question) if w]
    def score(txt): return sum(1 for w in kws if w and w in txt)
    tabs = []
    for o in ir.get("objects", []):
        blob = (o.get("cn") or "") + o.get("table", "") + " ".join(a.get("cn", "") + a.get("col", "") for a in o.get("attrs", []))
        tabs.append((score(blob), o))
    tabs.sort(key=lambda x: -x[0])
    picked = [o for s, o in tabs[:8] if s > 0] or [o for _, o in tabs[:5]]
    lines = [f'表 {o["table"]}: ' + ", ".join(f'{a["col"]}({a.get("cn","")})' for a in o.get("attrs", [])[:18]) for o in picked]
    jh = _join_hints(ir, [o.get("table") for o in picked])
    if jh: lines.append("表间关系(JOIN 用这些键):\n" + "\n".join(jh))
    return "\n".join(lines)

def _eval_sql_gen(question, ctx, model=None):
    """单题出 SQL(单分析,只要一个 JSON {\"sql\":...});→ (sql or None, err)"""
    try:
        from agent_runtime import get_runtime, available
    except Exception as e:
        return None, str(e)[:80]
    prompt = (f"你是数据分析引擎。基于 SQLite 库(日期是 TEXT 'YYYY-MM-DD')写一条 SQL 回答问题。\n"
              f"问题: {question}\n可用表结构:\n{ctx}\n"
              f'只输出一个 JSON(不要其它文字): {{"sql": "SELECT ..."}}\n'
              f"要求: 只用上面列出的表列;单条 SELECT;若问「万元」记得除以 10000;「⋈/⋈⋈」是可信 JOIN 键(⋈⋈ 沿链经中间表),不要自造 JOIN;『月均』分母用 count(DISTINCT 月),不得除以固定常数;『最…的』问题:聚合排序 LIMIT 1。")
    for drv in _drv_order():
        if drv not in available(): continue
        rt = get_runtime(drv)
        sid = f"ev_{uuid.uuid4().hex[:6]}"
        try:
            if model and _model_fits(drv, model):
                try: ok, reply = rt.run_turn(sid, prompt, timeout=75, model=model)
                except TypeError: ok, reply = rt.run_turn(sid, prompt, timeout=75)
            else:
                ok, reply = _llm_turn(rt, sid, prompt, 75, task="plan")
        except Exception as e:
            ok, reply = False, str(e)
        if ok and reply:
            m = re.search(r"\{[\s\S]*?\}", reply)
            if m:
                try: return (json.loads(m.group(0)).get("sql") or "").strip(), ""
                except Exception: pass
    return None, "引擎未产出 SQL"

def _eval_value(data):
    """结果 → 单值:首行第一个数值列;无数值取第一格字符串。"""
    rows = data.get("rows") or []
    if not rows: return None
    row = rows[0]
    for v in row.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool): return v
    for v in row.values():
        if v is not None: return str(v)
    return None

def _eval_judge(item, val):
    """判分:数值容差(万元题同时接受 ×10000 的元口径);字符串等值/包含。"""
    if val is None: return False
    if "gold_str" in item:
        return str(val).strip() == item["gold_str"] or item["gold_str"] in str(val)
    try: v = float(val)
    except Exception: return False
    gold, tol = float(item["gold"]), float(item.get("tol") or 0.0)
    def near(g): return abs(v - g) <= max(tol * abs(g), 1e-9) + (0.5 if tol == 0 else 0)
    return near(gold) or (item.get("unit") == "万元" and near(gold * 10000))

def _run_eval_thread(model=None, limit=None):
    items = _eval_items()[: (limit or 99)]
    ir = load_ir_edited("demo") or {}
    arms = [("A", "朴素 Text2SQL(仅英文表列)", lambda it: _ctx_naive(), False),
            ("B", "图谱增强(中文语义+关系)", lambda it: _ctx_graph(it["q"]), False),
            ("C", "本体全量(语义+关系+指标+术语+口径门禁)", lambda it: build_context(it["q"]), True)]
    EVAL_JOB.update(running=True, done=0, total=len(items) * len(arms),
                    started=time.strftime("%Y-%m-%d %H:%M:%S"), progress="启动")
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model or (_task_model("plan") or "(引擎缺省)"),
           "items": [], "arms": {k: {"name": nm, "exec": 0, "correct": 0, "cite": 0, "gate_block": 0} for k, nm, _, _ in arms}}
    for it in items:
        row = {"id": it["id"], "q": it["q"], "gold": it.get("gold", it.get("gold_str")), "arms": {}}
        for key, _nm, ctx_fn, gated in arms:
            EVAL_JOB["progress"] = f'{it["id"]} · {key} 组'
            sql, err = _eval_sql_gen(it["q"], ctx_fn(it), model=model)
            rec = {"sql": sql or "", "err": err, "exec": False, "value": None, "correct": False, "cite": False, "gate_block": False}
            if sql and sql_is_readonly(sql):
                if gated:
                    okv, why = _validate_sql_ontology(sql, ir)
                    if not okv:
                        rec["gate_block"] = True; rec["err"] = "口径拦截:" + why
                        out["arms"][key]["gate_block"] += 1
                if not rec["gate_block"]:
                    try:
                        data = q(sql, attach_uploads=True)
                        rec["exec"] = True; rec["value"] = _eval_value(data)
                        rec["correct"] = _eval_judge(it, rec["value"])
                    except Exception as e:
                        rec["err"] = str(e)[:120]
                sql_l = (sql or "").lower()
                rec["cite"] = all(t.lower() in sql_l for t in it.get("gold_tables") or [])
            a = out["arms"][key]
            a["exec"] += 1 if rec["exec"] else 0
            a["correct"] += 1 if rec["correct"] else 0
            a["cite"] += 1 if rec["cite"] else 0
            row["arms"][key] = rec
            EVAL_JOB["done"] += 1
        out["items"].append(row)
        _atomic_json(_EVAL_RES_F, out)          # 每题落盘:中断也保留已跑部分
    out["n"] = len(items)
    _atomic_json(_EVAL_RES_F, out)
    EVAL_JOB.update(running=False, progress="完成")

@app.post("/api/eval/run")
def eval_run():
    """启动一轮三臂评测(后台线程,每题落盘);可传 model 覆盖本轮出 SQL 的模型、limit 限题数。"""
    if EVAL_JOB["running"]: return jsonify({"error": "评测已在运行", "job": EVAL_JOB}), 409
    if not _eval_items(): return jsonify({"error": "题集缺失(benchmark/qa_set.json)"}), 500
    body = request.json or {}
    model = (body.get("model") or "").strip()[:80] or None
    limit = body.get("limit")
    threading.Thread(target=_run_eval_thread, kwargs={"model": model, "limit": limit}, daemon=True).start()
    return jsonify({"ok": True, "total": len(_eval_items()[: (limit or 99)]) * 3}), 202

@app.get("/api/eval/status")
def eval_status(): return jsonify(EVAL_JOB)

@app.get("/api/eval/results")
def eval_results():
    try: return jsonify(json.load(open(_EVAL_RES_F)))
    except Exception: return jsonify({"empty": True, "note": "尚未跑过评测"})

@app.get("/api/eval/set")
def eval_set(): return jsonify({"items": _eval_items(), "n": len(_eval_items())})

# ── 动作层(借鉴 Palantir ActionType):类型化·带参数·风险分级·全审计;PoC=决策捕获,不写只读源库 ──
ACTION_TYPES_F = os.path.join(WORK, "action_types.json")
ACTION_LOG_F = os.path.join(WORK, "action_log.json")

def _load_ats():
    try: return json.load(open(ACTION_TYPES_F))
    except Exception: return []

def _load_alog():
    try: return json.load(open(ACTION_LOG_F))
    except Exception: return []

@app.get("/api/actions")
def actions_list():
    log = _load_alog()
    return jsonify({"types": _load_ats(),
                    "pending": sum(1 for x in log if x["status"] == "pending"),
                    "executed": sum(1 for x in log if x["status"] == "executed"),
                    "denied": sum(1 for x in log if x["status"] == "denied")})

# ── 动作类型管理(DR-020):自建/编辑/停用/删除;内置种子受保护(只可停用与改说明)──
_AT_PARAM_TYPES = ("text", "textarea", "select")

def _valid_at_params(params):
    """参数 schema 校验 → 规整后的列表;非法返回 None。上限 12 个,name 须 snake_case。"""
    if not isinstance(params, list) or len(params) > 12: return None
    out, seen = [], set()
    for p in params:
        name = str(p.get("name") or "").strip()[:30]
        if not re.match(r"^[a-z][a-z0-9_]*$", name) or name in seen: return None
        seen.add(name)
        typ = p.get("type") if p.get("type") in _AT_PARAM_TYPES else "text"
        row = {"name": name, "cn": (str(p.get("cn") or "").strip()[:30] or name),
               "type": typ, "required": bool(p.get("required"))}
        if typ == "select":
            opts = [str(o).strip()[:40] for o in (p.get("options") or []) if str(o).strip()][:12]
            if not opts: return None
            row["options"] = opts
        out.append(row)
    return out

@app.post("/api/action/type")
def action_type_create():
    body = request.json or {}
    cn = str(body.get("cn") or "").strip()[:40]
    creator = str(body.get("creator") or "").strip()[:40]
    if not cn: return jsonify({"error": "动作名称必填"}), 400
    if not creator: return jsonify({"error": "创建人必填(姓名或工号,入登记)"}), 400
    risk = body.get("risk") if body.get("risk") in ("low", "high") else "high"   # 缺省从严
    params = _valid_at_params(body.get("params") or [])
    if params is None: return jsonify({"error": "参数 schema 非法(name 须小写下划线,select 须给 options)"}), 400
    ot = str(body.get("object_table") or "").strip()[:60]
    if ot:
        try:
            if ot.lower() not in {t["name"].lower() for t in table_list()}:
                return jsonify({"error": f"绑定表 {ot} 不在数据目录中"}), 400
        except Exception: pass
    tid = "act_" + uuid.uuid4().hex[:6]
    at = {"id": tid, "cn": cn, "object": str(body.get("object") or "").strip()[:40] or (ot or "—"),
          "object_table": ot, "desc": str(body.get("desc") or "").strip()[:300],
          "risk": risk, "source": "自建动作 · " + creator, "params": params,
          "effects": [{"type": "append_event",
                       "event": (str(body.get("effects_note") or "").strip()[:120] or "动作已登记")}],
          "builtin": False, "enabled": True,
          "created_by": creator, "created_ts": time.strftime("%Y-%m-%d %H:%M")}
    with _WRITE_LOCK:
        ats = _load_ats()
        ats.append(at)
        _atomic_json(ACTION_TYPES_F, ats)
    return jsonify({"ok": True, "type": at})

@app.post("/api/action/type/update")
def action_type_update():
    body = request.json or {}
    tid = body.get("id")
    with _WRITE_LOCK:
        ats = _load_ats()
        at = next((x for x in ats if x["id"] == tid), None)
        if not at: return jsonify({"error": "动作类型不存在"}), 404
        if "enabled" in body: at["enabled"] = bool(body["enabled"])
        if "desc" in body: at["desc"] = str(body["desc"]).strip()[:300]
        if not at.get("builtin"):                    # 内置种子语义受保护;自建可全改
            if "cn" in body and str(body["cn"]).strip(): at["cn"] = str(body["cn"]).strip()[:40]
            if "risk" in body and body["risk"] in ("low", "high"): at["risk"] = body["risk"]
            if "object" in body: at["object"] = str(body["object"]).strip()[:40]
            if "object_table" in body: at["object_table"] = str(body["object_table"]).strip()[:60]
            if "params" in body:
                params = _valid_at_params(body["params"])
                if params is None: return jsonify({"error": "参数 schema 非法"}), 400
                at["params"] = params
            if "effects_note" in body:
                at["effects"] = [{"type": "append_event",
                                  "event": str(body["effects_note"]).strip()[:120] or "动作已登记"}]
        elif set(body) - {"id", "enabled", "desc"}:
            return jsonify({"error": "内置动作只允许 停用/启用 与修改说明"}), 400
        at["updated_ts"] = time.strftime("%Y-%m-%d %H:%M")
        _atomic_json(ACTION_TYPES_F, ats)
    return jsonify({"ok": True, "type": at})

@app.post("/api/action/type/delete")
def action_type_delete():
    tid = (request.json or {}).get("id")
    with _WRITE_LOCK:
        ats = _load_ats()
        at = next((x for x in ats if x["id"] == tid), None)
        if not at: return jsonify({"error": "动作类型不存在"}), 404
        if at.get("builtin"): return jsonify({"error": "内置动作不可删除(可停用)"}), 400
        _atomic_json(ACTION_TYPES_F, [x for x in ats if x["id"] != tid])
    return jsonify({"ok": True})

@app.post("/api/action/invoke")
def action_invoke():
    """发起动作:参数按类型 schema 校验;低风险直执行(记日志),高风险进审批队列。
    执行=决策捕获(追加事件式日志 + webhook 仅登记),绝不写只读源库。"""
    body = request.json or {}
    at = next((x for x in _load_ats() if x["id"] == body.get("action_id")), None)
    if not at: return jsonify({"error": "动作类型不存在"}), 404
    if at.get("enabled") is False: return jsonify({"error": "该动作类型已停用,不可发起"}), 400
    operator = str(body.get("operator") or "").strip()[:40]
    if not operator: return jsonify({"error": "操作人必填(姓名或工号)"}), 400
    params = body.get("params") or {}
    clean = {}
    for p in at["params"]:
        v = str(params.get(p["name"]) or "").strip()
        if p.get("required") and not v: return jsonify({"error": f"参数「{p['cn']}」必填"}), 400
        if p.get("type") == "select" and v and v not in (p.get("options") or []):
            return jsonify({"error": f"参数「{p['cn']}」须为 {'/'.join(p.get('options') or [])}"}), 400
        if p.get("type") == "number" and v:
            try: float(v)
            except Exception: return jsonify({"error": f"参数「{p['cn']}」须为数字"}), 400
        clean[p["name"]] = v[:500]
    item = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%d %H:%M"),
            "action_id": at["id"], "action_cn": at["cn"], "object": at.get("object", ""),
            "risk": at.get("risk", "low"), "operator": operator, "params": clean,
            "status": "pending" if at.get("risk") == "high" else "executed",
            "effects": [], "approver": "", "approve_comment": ""}
    if item["status"] == "executed":
        item["effects"] = [(((e.get("event") or "") + "(已记录)") if e.get("type") == "append_event" else (e.get("target") or e.get("type") or ""))
                           for e in (at.get("effects") or [])]
    with _WRITE_LOCK:
        log = _load_alog(); log.insert(0, item); _atomic_json(ACTION_LOG_F, log[:1000])
    return jsonify({"ok": True, "id": item["id"], "status": item["status"]})

@app.get("/api/action/log")
def action_log():
    return jsonify({"items": _load_alog()[:200]})

@app.post("/api/action/approve")
def action_approve():
    """高风险动作审批:approve 执行效果并记日志;deny 必须给意见。审计三要素:操作人/审批人/时间全留痕。"""
    body = request.json or {}
    fid, decision = body.get("id", ""), body.get("decision", "")
    approver = str(body.get("approver") or "").strip()[:40]
    comment = str(body.get("comment") or "").strip()[:300]
    if decision not in ("approve", "deny"): return jsonify({"error": "decision 须为 approve/deny"}), 400
    if not approver: return jsonify({"error": "审批人必填"}), 400
    if decision == "deny" and not comment: return jsonify({"error": "驳回必须填写意见"}), 400
    with _WRITE_LOCK:
        log = _load_alog()
        it = next((x for x in log if x["id"] == fid), None)
        if not it: return jsonify({"error": "记录不存在"}), 404
        if it["status"] != "pending": return jsonify({"error": "该动作不在待审批状态"}), 400
        at = next((x for x in _load_ats() if x["id"] == it["action_id"]), {}) or {}
        it["approver"] = approver; it["approve_comment"] = comment
        it["approve_ts"] = time.strftime("%Y-%m-%d %H:%M")
        if decision == "approve":
            it["status"] = "executed"
            it["effects"] = [(((e.get("event") or "") + "(已记录)") if e.get("type") == "append_event" else (e.get("target") or e.get("type") or ""))
                             for e in (at.get("effects") or [])]
        else:
            it["status"] = "denied"
        _atomic_json(ACTION_LOG_F, log)
    return jsonify({"ok": True, "status": it["status"]})

QA_FB = os.path.join(WORK, "qa_feedback.json")

def _load_fb():
    try: return json.load(open(QA_FB))
    except Exception: return []

@app.post("/api/chat/feedback")
def chat_feedback():
    """问数/诊断结果反馈落库:needs_work 进入本体迭代候选队列(评审页处理)——P24 证据回流环"""
    body = request.json or {}
    verdict = body.get("verdict")
    if verdict not in ("useful", "needs_work"): return jsonify({"error": "verdict 须为 useful/needs_work"}), 400
    item = {"id": uuid.uuid4().hex[:8], "ts": time.strftime("%Y-%m-%d %H:%M"),
            "question": str(body.get("question") or "")[:300], "verdict": verdict,
            "comment": str(body.get("comment") or "")[:500], "mode": str(body.get("mode") or "chat")[:16],
            "resolved": False}
    with _WRITE_LOCK:
        fb = _load_fb(); fb.insert(0, item); _atomic_json(QA_FB, fb[:500])
    return jsonify({"ok": True, "id": item["id"]})

@app.get("/api/chat/feedback")
def chat_feedback_list():
    fb = _load_fb()
    return jsonify({"items": fb, "open": sum(1 for x in fb if x["verdict"] == "needs_work" and not x.get("resolved")),
                    "useful": sum(1 for x in fb if x["verdict"] == "useful")})

@app.post("/api/chat/feedback/resolve")
def chat_feedback_resolve():
    fid = (request.json or {}).get("id", "")
    with _WRITE_LOCK:
        fb = _load_fb()
        it = next((x for x in fb if x["id"] == fid), None)
        if not it: return jsonify({"error": "反馈不存在"}), 404
        it["resolved"] = True; it["resolved_ts"] = time.strftime("%Y-%m-%d %H:%M")
        _atomic_json(QA_FB, fb)
    return jsonify({"ok": True})

@app.post("/api/chat/save_skill")
def chat_save_skill():
    """把一次深度问数(问题 + 生成的 SQL/图表规格)沉淀为可复用技能(对齐平台『沉淀为 Skill』)。"""
    body = request.json or {}
    question = (body.get("question") or "").strip()
    results = body.get("results") or []
    if not question or not results: return jsonify({"error": "缺少问题或结果"}), 400
    sk = {"id": "qas_" + uuid.uuid4().hex[:8], "question": question,
          "analyses": [{"title": r.get("title"), "sql": r.get("sql"), "chart": r.get("chart")} for r in results],
          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _WRITE_LOCK:
        try: store = json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else []
        except Exception: store = []
        store.insert(0, sk); store = store[:100]
        _atomic_json(_QA_SKILLS_F, store)
    return jsonify({"ok": True, "id": sk["id"], "count": len(sk["analyses"])})

@app.get("/api/chat/skills")
def chat_skills():
    try: return jsonify(json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else [])
    except Exception: return jsonify([])

@app.get("/api/glossary")
def glossary():
    """术语管理:英文↔中文业务词典(来自 translate_cn 词表,对齐平台『术语管理』)"""
    try:
        import translate_cn as T
    except Exception as e:
        return jsonify({"terms": [], "count": 0, "error": str(e)[:100]})
    terms = []
    for src, typ in ((getattr(T, "TABLE_PHRASE", {}), "表名"), (getattr(T, "COL_PHRASE", {}), "列名"),
                     (getattr(T, "PHRASE", {}), "短语"), (getattr(T, "TOKEN", {}), "词元")):
        for en, cn in src.items(): terms.append({"en": en, "cn": cn, "type": typ})
    return jsonify({"terms": terms, "count": len(terms)})

@app.get("/api/routes")
def api_routes():
    """API 服务目录(对齐平台『数据服务』):列出本系统全部 HTTP 接口"""
    routes = []
    for r in app.url_map.iter_rules():
        if r.endpoint == "static": continue
        methods = sorted(m for m in r.methods if m in ("GET", "POST", "PUT", "DELETE"))
        grp = "页面/静态"
        if r.rule.startswith("/api/ont"): grp = "本体治理"
        elif r.rule.startswith("/api/chat") or r.rule.startswith("/api/metric"): grp = "智能问数/指标"
        elif r.rule.startswith("/api/graph") or r.rule.startswith("/api/build"): grp = "建模/图谱"
        elif r.rule.startswith("/api/table") or r.rule.startswith("/api/overview") or r.rule.startswith("/api/query"): grp = "数据资产"
        elif r.rule.startswith("/api/skill") or r.rule.startswith("/api/tool") or r.rule.startswith("/api/job") or r.rule.startswith("/api/agent"): grp = "技能/作业"
        elif r.rule.startswith("/api/"): grp = "系统/其它"
        routes.append({"path": r.rule, "methods": methods, "group": grp})
    routes.sort(key=lambda x: (x["group"], x["path"]))
    return jsonify({"routes": routes, "count": len(routes)})

@app.get("/api/quality")
def data_quality():
    """数据质量检查(对齐平台『数据治理/告警中心』):空表/行数/关键口径异常,基于真实数据"""
    checks = []; scanned = 0; flagged = set()
    try:
        tl = table_list(); scanned = len(tl)
        for t in tl:
            if t["rows"] == 0:
                checks.append({"target": t["name"], "rule": "空表检测", "level": "warn", "detail": f'{t["name"]}: 0 行(源头无数据)'}); flagged.add(t["name"])
            elif t["cols"] == 0:
                checks.append({"target": t["name"], "rule": "无列检测", "level": "error", "detail": f'{t["name"]}: 无字段'}); flagged.add(t["name"])
    except Exception as e:
        checks.append({"target": "table_list", "rule": "库连通", "level": "error", "detail": str(e)[:80]})
    # 业务口径异常:毛利率偏低(<12%)的月份
    try:
        rows = q("SELECT substr(order_date,1,7) m, round(100.0*sum(gross_profit_actual)/nullif(sum(amount),0),1) mr FROM fact_sales_order GROUP BY 1 HAVING mr < 12 ORDER BY 1")["rows"]
        for r in rows:
            checks.append({"target": "fact_sales_order", "rule": "毛利率预警(<12%)", "level": "warn", "detail": f'{r["m"]} 毛利率 {r["mr"]}%'}); flagged.add("fact_sales_order")
    except Exception: pass
    # 分层对账(DR-022 评测揪出):汇总层与事实层同口径合计差异 >5% 报警——层间不一致会让问数
    # 因选表不同得出不同答案(评测 Q7/Q8 败题即由此)。
    for tag, fact_sql, agg_sql, agg_t in [
        ("产量对账", "SELECT sum(o.quantity) FROM fact_production_output o JOIN fact_production_order po ON o.order_id=po.order_id",
         "SELECT sum(actual_quantity) FROM DWS_PRODUCTION_DAILY", "DWS_PRODUCTION_DAILY"),
        ("销售额对账", "SELECT sum(amount) FROM fact_sales_order WHERE substr(order_date,1,4)='2025'",
         "SELECT sum(sales_amount) FROM agg_metric_monthly WHERE year=2025", "agg_metric_monthly"),
    ]:
        try:
            fv = (q(fact_sql)["rows"][0] or {}).popitem()[1] or 0
            av = (q(agg_sql)["rows"][0] or {}).popitem()[1] or 0
            if fv and av:
                diff = abs(fv - av) * 100.0 / max(abs(fv), 1e-9)
                if diff > 5:
                    checks.append({"target": agg_t, "rule": f"分层对账({tag})", "level": "warn",
                                   "detail": f"汇总层 {round(av):,} vs 事实层 {round(fv):,},差异 {diff:.1f}%(>5%,问数按层选表将得出不同答案)"})
                    flagged.add(agg_t)
        except Exception:
            pass
    lv = {"error": 0, "warn": 0, "ok": 0}
    for c in checks: lv[c["level"]] = lv.get(c["level"], 0) + 1
    # 通过表数 = 已检表数 − 命中异常的表数(给出扫描范围,避免"只见告警不见基数")
    return jsonify({"checks": checks, "count": len(checks), "levels": lv,
                    "scanned": scanned, "passed": max(0, scanned - len(flagged))})

@app.get("/api/sysinfo")
def sysinfo():
    """系统管理(对齐平台『系统管理』):健康/引擎/库/作业概况"""
    import platform as _pf
    info = {"health": "ok", "port": 8092, "python": _pf.python_version(), "platform": _pf.platform()[:60]}
    try: info["db_ok"] = os.path.exists(DB)
    except Exception: info["db_ok"] = False
    try:
        from agent_runtime import available
        info["runtimes"] = available()
        info["current_driver"] = os.environ.get("CLAW_DRIVER", "hermes")
    except Exception: info["runtimes"] = []
    info["jobs"] = len(JOBS)
    info["qa_cache"] = len(_QA_CACHE)
    try: info["tables"] = len(table_list())
    except Exception: info["tables"] = 0
    return jsonify(info)

@app.get("/api/agents")
def agents():
    """智能体列表:深度问数内置体 + 沉淀技能 + skills_seed(对齐平台『智能体列表』)"""
    out = [{"name": "demo-deep-qa", "desc": "基于 示例 本体图谱的深度问数编排智能体", "type": "内置", "author": "系统", "ts": ""}]
    try:
        qs = json.load(open(_QA_SKILLS_F)) if os.path.exists(_QA_SKILLS_F) else []
        for s in qs:
            out.append({"name": (s.get("question") or s.get("id"))[:40], "desc": f'沉淀问数 · {len(s.get("analyses",[]))} 条 SQL',
                        "type": "沉淀", "author": "用户", "ts": s.get("ts", "")})
    except Exception: pass
    try:
        for p in sorted(glob.glob(os.path.join(HERE, "..", "上游本体引擎", "web", "skills_seed", "*"))):
            if os.path.isdir(p):
                out.append({"name": os.path.basename(p), "desc": "本体构建技能包", "type": "技能包", "author": "平台", "ts": ""})
    except Exception: pass
    return jsonify({"agents": out, "count": len(out)})

# ── 本体构建(上传多模态数据 / 指向数据库 → 调技能)──
@app.post("/api/build/upload")
def build_upload():
    """上传 CSV/TSV → 入 uploads.db 成表(多模态里结构化部分;其余文件存档供技能读取)"""
    os.makedirs(os.path.dirname(UPLOAD_DB), exist_ok=True)
    saved, tables = [], []
    import csv as _csv, io
    with _WRITE_LOCK:                                     # 串行化上传写,避免 uploads.db "database is locked"
        con = sqlite3.connect(UPLOAD_DB)                  # 置于 with 内:connect 抛错也由上下文管理器释放锁
        try:
            for f in request.files.getlist("files"):
                fn = os.path.basename(f.filename or "file"); raw = f.read()
                path = os.path.join(WORK, "uploads_" + fn)
                with open(path, "wb") as fp: fp.write(raw)
                saved.append(fn)
                if fn.lower().endswith((".csv", ".tsv")):
                    try:
                        txt = raw.decode("utf-8-sig", "replace")
                        rows = list(_csv.reader(io.StringIO(txt), delimiter="\t" if fn.lower().endswith(".tsv") else ","))
                        if len(rows) >= 2:
                            t = re.sub(r"[^A-Za-z0-9_]", "_", fn.rsplit(".", 1)[0])[:40]
                            hdr = [re.sub(r"[^A-Za-z0-9_一-鿿]", "_", h) or f"c{i}" for i, h in enumerate(rows[0])]
                            con.execute(f'DROP TABLE IF EXISTS "{t}"')
                            con.execute(f'CREATE TABLE "{t}" ({", ".join(chr(34)+h+chr(34)+" TEXT" for h in hdr)})')
                            con.executemany(f'INSERT INTO "{t}" VALUES ({",".join("?"*len(hdr))})',
                                            [r[:len(hdr)] + [""] * (len(hdr) - len(r)) for r in rows[1:]])
                            con.commit(); tables.append({"table": t, "rows": len(rows) - 1})
                    except Exception as e:
                        tables.append({"table": fn, "error": str(e)[:80]})
        finally:
            con.close()
    return jsonify({"saved": saved, "tables": tables})

@app.post("/api/build/run")
def build_run():
    """构建本体:source=demo(主库)|uploads(上传库);快速数据驱动构建(表→对象,命名启发+FK/重叠),产物注册为新图谱"""
    body = request.json or {}
    src = body.get("source", "uploads"); name = body.get("name") or f"构建图谱{time.strftime('%m%d%H%M')}"
    db = DB if src == "demo" else UPLOAD_DB
    if not os.path.exists(db): return jsonify({"error": "数据库不存在,请先上传"}), 400
    key = "built_" + uuid.uuid4().hex[:6]
    jid = run_job([sys.executable, os.path.join(HERE, "quick_build.py"), db, os.path.join(WORK, key + ".json"), name],
                  cwd=HERE, tag=f"build:{name}")
    return jsonify({"job": jid, "graph_key": key})

# ── 本体构建·问询台:多模态数据源 + 多库连接 + 技能编排 + Hermes agentic 构建 ──
_BUILD_CONN_F = os.path.join(WORK, "build_connections.json")
_BUILD_SKILL_D = os.path.join(WORK, "custom_skills")
_MOD_MAP = {"csv": "表格", "tsv": "表格", "xlsx": "表格", "xls": "表格", "json": "结构", "xml": "结构",
            "pdf": "文档", "docx": "文档", "doc": "文档", "txt": "文本", "md": "文本",
            "png": "图像", "jpg": "图像", "jpeg": "图像", "gif": "图像", "svg": "图像",
            "sql": "代码", "py": "代码", "ddl": "代码", "wav": "音频", "mp3": "音频"}
_BUILD_SKILL_DESC = {
    "ontology-build": "从数据库+建表代码+业务代码(视图/ETL)+行业知识(Excel)构建可审计企业本体(对象/事件/关系/指标)",
    "gov-app-ontology-build": "多智能体自主编排,构建应用本体——对象/动作/事件绑治理资产",
    "ontology-forge": "按 OWL2 / Palantir 操作型四层 / SHACL / 斯坦福七步法,从任意数据+代码+文档建本体",
    "ontology-agentic": "Agentic 编排:规划→取证→提议→工具裁决→critic 对抗→迭代,从真实数据建图",
    "auto-ontology": "查询与完善企业本体:问对象/属性/指标/关系/缺口,确认候选语义、改名、挂属性"}

def _load_conns():
    v = _load_json(_BUILD_CONN_F)
    return v if isinstance(v, list) else []

def _sqlite_tables(path):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        c.close(); return n
    except Exception:
        return []

@app.get("/api/build/sources")
def build_sources():
    """构建数据源清单:内置库 + 已连接库 + 已上传多模态资产"""
    srcs = [{"id": "demo", "name": "示例主库", "kind": "sqlite", "builtin": True,
             "tables": len(_sqlite_tables(DB)), "desc": "108 表合成制造数据", "ready": os.path.exists(DB)}]
    utabs = _sqlite_tables(UPLOAD_DB) if os.path.exists(UPLOAD_DB) else []
    srcs.append({"id": "uploads", "name": "上传数据库", "kind": "sqlite", "builtin": True,
                 "tables": len(utabs), "desc": "CSV/TSV 上传自动建表", "ready": bool(utabs), "table_names": utabs[:50]})
    for c in _load_conns():
        ready, tabs = False, 0
        if c.get("kind") == "sqlite" and c.get("path") and os.path.exists(c["path"]):
            t = _sqlite_tables(c["path"]); ready, tabs = bool(t), len(t)
        elif c.get("kind") == "api":             # API 源:已物化(up.api_*)即就绪
            ready = _api_conn_table(c) in utabs; tabs = 1 if ready else 0
        srcs.append({**c, "tables": tabs, "ready": ready})
    assets = []
    for p in sorted(glob.glob(os.path.join(WORK, "uploads_*"))):
        fn = os.path.basename(p)[8:]
        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        assets.append({"name": fn, "modality": _MOD_MAP.get(ext, "其它"), "kb": round(os.path.getsize(p) / 1024, 1)})
    return jsonify({"sources": srcs, "assets": assets})

@app.post("/api/build/connect")
def build_connect():
    """登记数据源连接:sqlite(校验文件可读)或外部库(mysql/doris/hive,登记连接串)"""
    body = request.json or {}
    kind = body.get("kind", "sqlite"); name = (body.get("name") or "").strip()
    if not name: return jsonify({"error": "需要连接名"}), 400
    conn = {"id": "conn_" + uuid.uuid4().hex[:6], "name": name, "kind": kind, "builtin": False}
    if kind == "sqlite":
        path = (body.get("path") or "").strip()
        if not path or not os.path.exists(path): return jsonify({"error": "SQLite 文件不存在"}), 400
        if not _sqlite_tables(path): return jsonify({"error": "该文件无可读表(非 SQLite?)"}), 400
        conn["path"] = path
    elif kind == "api":                          # C8 API 型数据源:REST 端点 → 取数物化进上传库(up.api_*)
        url = (body.get("url") or "").strip()
        if not re.match(r"^https?://", url): return jsonify({"error": "API 地址须以 http(s):// 开头"}), 400
        conn["url"] = url[:500]
        conn["json_path"] = (body.get("json_path") or "").strip()[:120]
        conn["note"] = "API 源已登记;「取数」将结果物化为 up.api_* 表,问数/SQL 即可用"
    else:
        conn["dsn"] = (body.get("dsn") or "").strip()
        conn["note"] = "外部库已登记(取数需网络连通与驱动;凭据只写不回显)"
        if not conn["dsn"]: return jsonify({"error": "需要连接串 DSN"}), 400
        if body.get("user") or body.get("password"):     # C7 凭据:0600 独立文件,永不回显/入列表
            _save_conn_secret(conn["id"], body.get("user"), body.get("password"))
    # 幂等:同一物理源(sqlite 按 path、api 按 url、外部库按 dsn)已登记则复用,避免重复连接堆叠成脏列表
    def _same_source(c):
        if c.get("kind") != kind: return False
        if kind == "sqlite": return c.get("path") == conn.get("path")
        if kind == "api": return c.get("url") == conn.get("url")
        return c.get("dsn") == conn.get("dsn")
    with _WRITE_LOCK:
        conns = _load_conns()
        dup = next((c for c in conns if _same_source(c)), None)
        if dup:
            return jsonify({"ok": True, "conn": dup, "deduped": True})
        conns.insert(0, conn); _atomic_json(_BUILD_CONN_F, conns[:30])
    return jsonify({"ok": True, "conn": conn})

@app.post("/api/build/connect/delete")
def build_connect_del():
    cid = (request.json or {}).get("id")
    with _WRITE_LOCK:
        _atomic_json(_BUILD_CONN_F, [c for c in _load_conns() if c.get("id") != cid])
    _save_conn_secret(cid, "", "")               # 连带清除凭据
    return jsonify({"ok": True})

def _api_conn_table(conn):
    """API 源物化目标表名:api_<连接名 slug>"""
    slug = re.sub(r"[^a-z0-9_]+", "_", (conn.get("name") or "src").lower()).strip("_")[:24] or "src"
    return "api_" + slug

@app.post("/api/conn/api_fetch")
def conn_api_fetch():
    """C8 API 源取数:拉取 JSON → json_path 下钻 → 对象数组物化为 uploads.db 表(up.api_*)。
    上限 2MB / 5000 行;仅 http(s);物化后 深度问数/SQL 工作台/可视化 直接可用。"""
    cid = (request.json or {}).get("id")
    conn = _find_conn(cid)
    if not conn or conn.get("kind") != "api": return jsonify({"error": "API 连接不存在"}), 404
    url = conn.get("url") or ""
    if not re.match(r"^https?://", url): return jsonify({"error": "非法 API 地址"}), 400
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "DataMind/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(2_000_000)
        data = json.loads(raw)
    except Exception as e:
        return jsonify({"error": f"API 拉取失败:{str(e)[:140]}"}), 502
    try:
        for part in (conn.get("json_path") or "").split("."):
            if not part: continue
            data = data[int(part)] if re.match(r"^\d+$", part) and isinstance(data, list) else data[part]
    except Exception:
        return jsonify({"error": f"json_path「{conn.get('json_path')}」在返回结构中不存在"}), 400
    if isinstance(data, dict):                   # 单对象 → 单行
        data = [data]
    if not (isinstance(data, list) and data and all(isinstance(x, dict) for x in data[:20])):
        return jsonify({"error": "json_path 需指向对象数组(list of objects)"}), 400
    data = data[:5000]
    cols, seen = [], set()
    for row in data[:50]:
        for k in row.keys():
            if k not in seen and len(cols) < 40:
                # 列名消毒后才可进 DDL:去除引号等可越出标识符边界的字符(与 CSV 上传同规则)
                safe = re.sub(r"[^A-Za-z0-9_\u4e00-\u9fff]", "_", str(k))[:64] or f"c{len(cols)}"
                seen.add(k); cols.append(safe)
    table = _api_conn_table(conn)
    with _WRITE_LOCK:
        con = sqlite3.connect(UPLOAD_DB)
        try:
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            con.execute(f'CREATE TABLE "{table}" ({", ".join(chr(34)+c+chr(34)+" TEXT" for c in cols)})')
            con.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" for _ in cols)})',
                            [tuple("" if row.get(c) is None else str(row.get(c)) for c in cols) for row in data])
            con.commit()
        finally:
            con.close()
        conns = _load_conns()
        for c in conns:
            if c.get("id") == cid:
                c["last_fetch"] = time.strftime("%Y-%m-%d %H:%M:%S"); c["last_rows"] = len(data); c["table"] = table
        _atomic_json(_BUILD_CONN_F, conns)
    return jsonify({"ok": True, "table": "up." + table, "rows": len(data), "columns": cols})

@app.get("/api/build/skills")
def build_skills():
    """内置本体构建技能 + 用户上传的自定义技能说明"""
    out = []
    for d in sorted(glob.glob(os.path.join(PLATFORM, "web", "skills_seed", "*"))):
        n = os.path.basename(d)
        if os.path.isdir(d):
            out.append({"name": n, "desc": _BUILD_SKILL_DESC.get(n, ""), "builtin": True,
                        "runnable": os.path.exists(os.path.join(d, "run.sh"))})
    if os.path.isdir(_BUILD_SKILL_D):
        for f in sorted(glob.glob(os.path.join(_BUILD_SKILL_D, "*.md"))):
            try: txt = open(f, errors="replace").read()
            except Exception: txt = ""
            m = re.search(r"description:\s*(.+)", txt)
            out.append({"name": os.path.basename(f)[:-3], "desc": (m.group(1) if m else txt[:120]).strip()[:140],
                        "builtin": False, "runnable": False})
    return jsonify(out)

# ── 技能管理(DR-021):浏览/新建/编辑/删除;内置只读可复制;自定义内容真正注入构建方法论 ──
_SKILL_NAME_RE = re.compile(r"^[\w\-]{1,40}$")           # \w 含中文;禁路径字符

def _builtin_skill_names():
    return {os.path.basename(d) for d in glob.glob(os.path.join(PLATFORM, "web", "skills_seed", "*")) if os.path.isdir(d)}

def _custom_skill_path(name):
    for ext in (".md", ".txt"):
        p = os.path.join(_BUILD_SKILL_D, name + ext)
        if os.path.exists(p): return p
    return None

def _skill_body(text):
    """去掉 front-matter 的技能正文(供注入构建 prompt)"""
    m = re.match(r"^---\n[\s\S]*?\n---\n", text)
    return text[m.end():].strip() if m else text.strip()

@app.get("/api/build/skill/<name>")
def build_skill_get(name):
    """浏览技能内容:自定义=可编辑全文;内置=只读 SKILL.md(附目录清单)。"""
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "非法技能名"}), 400
    p = _custom_skill_path(name)
    if p:
        try: content = open(p, encoding="utf-8", errors="replace").read()
        except Exception as e: return jsonify({"error": str(e)[:100]}), 500
        return jsonify({"name": name, "builtin": False, "editable": True, "content": content})
    d = os.path.join(PLATFORM, "web", "skills_seed", name)
    sk = os.path.join(d, "SKILL.md")
    if os.path.isdir(d):
        content = open(sk, encoding="utf-8", errors="replace").read() if os.path.exists(sk) else "(该内置技能无 SKILL.md 说明)"
        files = sorted(os.path.basename(x) for x in glob.glob(os.path.join(d, "*")))[:20]
        return jsonify({"name": name, "builtin": True, "editable": False, "content": content, "files": files})
    return jsonify({"error": "技能不存在"}), 404

@app.post("/api/build/skill/save")
def build_skill_save():
    """新建/编辑自定义技能(在线编辑器)。内置技能名不可占用(内置只读,可另存副本)。"""
    body = request.json or {}
    name = str(body.get("name") or "").strip()
    content = str(body.get("content") or "")
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "技能名须为 1~40 字中英文/数字/下划线/连字符"}), 400
    if name in _builtin_skill_names(): return jsonify({"error": f"「{name}」是内置技能(只读),请换名保存为自定义副本"}), 400
    if not content.strip(): return jsonify({"error": "技能内容不能为空"}), 400
    if len(content) > 200_000: return jsonify({"error": "技能内容过大(上限 200KB)"}), 400
    os.makedirs(_BUILD_SKILL_D, exist_ok=True)
    p = _custom_skill_path(name) or os.path.join(_BUILD_SKILL_D, name + ".md")
    with _WRITE_LOCK:
        open(p, "w", encoding="utf-8").write(content)
    return jsonify({"ok": True, "name": name})

@app.post("/api/build/skill/delete")
def build_skill_delete():
    name = str((request.json or {}).get("name") or "").strip()
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "非法技能名"}), 400
    if name in _builtin_skill_names(): return jsonify({"error": "内置技能不可删除"}), 400
    p = _custom_skill_path(name)
    if not p: return jsonify({"error": "技能不存在"}), 404
    with _WRITE_LOCK:
        os.remove(p)
    return jsonify({"ok": True})

@app.post("/api/build/skill/from_graph")
def build_skill_from_graph():
    """#3 构建产物→技能沉淀:从图谱提取 动词表/类型分布/定义风格 → 生成自定义技能(确定性,不经 LLM)。"""
    from collections import Counter
    body = request.json or {}
    gk = body.get("graph") or ""
    if _bad_gkey(gk): return jsonify({"error": "非法图谱键"}), 400
    ir = load_ir_edited(gk)
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    rels = _rels(ir)[0]
    verbs = Counter((r.get("verb") or "关联") for r in rels if r.get("status") in ("verified", "asserted"))
    kinds = Counter((o.get("kind") or "object") for o in ir.get("objects", []))
    defs = [(o.get("cn") or o.get("name") or "", (o.get("definition") or "").strip())
            for o in ir.get("objects", []) if (o.get("definition") or "").strip()][:2]
    _sc = ir.get("scenario")
    gname = (_sc.get("name") if isinstance(_sc, dict) else _sc) or gk
    gname = str(gname).strip()[:30]
    name = str(body.get("name") or "").strip() or ("from-" + re.sub(r"[^\w\-]+", "-", gk)[:24])
    lines = [f"---\ndescription: 从构建产物「{gname}」沉淀的建模纪律(动词表/类型分布/定义风格)\n---\n",
             f"## 来源\n构建产物 `{gk}`(对象 {len(ir.get('objects', []))} · 已验证/断言关系 {sum(verbs.values())}),沉淀于 {time.strftime('%Y-%m-%d')}。\n",
             "## 关系动词表(建模时优先沿用)"]
    lines += [f"- {v}({n} 次)" for v, n in verbs.most_common(8)] or ["-(该图谱暂无已验证关系)"]
    lines.append("\n## 对象类型分布(kind 判定参照)")
    lines += [f"- {k}:{n} 个" for k, n in kinds.most_common()]
    if defs:
        lines.append("\n## 定义风格样例(属+种差,非循环)")
        lines += [f"- 「{cn}」:{d[:120]}" for cn, d in defs]
    lines.append("\n## 纪律\n1. 关系动词优先复用上表,不新造同义动词;\n2. 单据/台账/目录类信息记录判 kind=ice,勿与物理实体混淆;\n3. 定义用「属+种差」句式,定义体不得复用被定义术语本身。")
    content = "\n".join(lines)
    if not _SKILL_NAME_RE.match(name): return jsonify({"error": "技能名非法"}), 400
    if name in _builtin_skill_names(): return jsonify({"error": "技能名与内置冲突,请换名"}), 400
    os.makedirs(_BUILD_SKILL_D, exist_ok=True)
    with _WRITE_LOCK:
        open(os.path.join(_BUILD_SKILL_D, name + ".md"), "w", encoding="utf-8").write(content)
    return jsonify({"ok": True, "name": name, "verbs": dict(verbs.most_common(8)), "chars": len(content)})

# ── #1 技能对比实验(DR-022):同一构建目标 × 两组技能,各跑一次真实构建,对比产物 ──
_SKILL_CMP_F = os.path.join(WORK, "skill_compare.json")
SKILL_CMP_JOB = {"running": False, "progress": "", "started": ""}

def _build_stats(ir):
    from collections import Counter
    objs = ir.get("objects", [])
    rels = _rels(ir)[0]
    ok_rels = [r for r in rels if r.get("status") in ("verified", "asserted")]
    return {"objects": len(objs), "relations": len(rels),
            "verified": sum(1 for r in rels if r.get("status") == "verified"),
            "verbs": dict(Counter((r.get("verb") or "关联") for r in ok_rels).most_common(6)),
            "kinds": dict(Counter((o.get("kind") or "object") for o in objs).most_common()),
            "def_coverage": round(sum(1 for o in objs if (o.get("definition") or "").strip()) * 100.0 / max(1, len(objs)), 1)}

def _run_skill_compare(query, arms):
    """两臂顺序真实构建(LLM 抽取+反造假裁决,不走兜底造假);每臂落一个 built_* 产物。"""
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "query": query, "arms": []}
    for i, skills in enumerate(arms):
        label = chr(65 + i)
        SKILL_CMP_JOB["progress"] = f"{label} 组构建中(技能:{'、'.join(skills) or '无'})"
        ev = _gather_evidence(DB)
        extracted = _bounded(lambda: _llm_extract_ontology(query, ev, skills), 640)
        arm = {"label": label, "skills": skills, "ok": False}
        if extracted and extracted.get("objects"):
            key = "built_" + uuid.uuid4().hex[:6]
            ir = _adjudicate_ir(DB, f"技能对比-{label}臂", extracted, ev)
            _atomic_json(os.path.join(WORK, key + ".json"), ir)
            arm.update(ok=True, key=key, stats=_build_stats(ir))
        else:
            arm["error"] = "LLM 抽取失败/超时(该组如实记为失败,不用兜底数据冒充)"
        out["arms"].append(arm)
        _atomic_json(_SKILL_CMP_F, out)               # 每臂落盘
    SKILL_CMP_JOB.update(running=False, progress="完成")

@app.post("/api/build/skill_compare")
def skill_compare_run():
    """启动技能对比:{query?, skills_a:[], skills_b:[]};后台跑两次真实构建(约 4-10 分钟)。"""
    if SKILL_CMP_JOB["running"]: return jsonify({"error": "对比实验进行中", "job": SKILL_CMP_JOB}), 409
    body = request.json or {}
    query = (body.get("query") or "").strip() or "从 示例主库自动识别核心对象、事件与关系,构建一张可审计的制造企业本体"
    a = [s for s in (body.get("skills_a") or []) if isinstance(s, str)][:5]
    b = [s for s in (body.get("skills_b") or []) if isinstance(s, str)][:5]
    known = _builtin_skill_names() | {os.path.basename(p).rsplit(".", 1)[0] for p in glob.glob(os.path.join(_BUILD_SKILL_D, "*"))}
    bad = [s for s in a + b if s not in known]
    if bad: return jsonify({"error": f"技能不存在: {', '.join(bad)}"}), 400
    SKILL_CMP_JOB.update(running=True, progress="启动", started=time.strftime("%Y-%m-%d %H:%M:%S"))
    threading.Thread(target=_run_skill_compare, args=(query, [a, b]), daemon=True).start()
    return jsonify({"ok": True}), 202

@app.get("/api/build/skill_compare/status")
def skill_compare_status(): return jsonify(SKILL_CMP_JOB)

@app.get("/api/build/skill_compare/results")
def skill_compare_results():
    try: return jsonify(json.load(open(_SKILL_CMP_F)))
    except Exception: return jsonify({"empty": True})

@app.post("/api/build/skill/upload")
def build_skill_upload():
    """上传自定义技能说明(仅收 .md/.txt 的 SKILL 说明,不收可执行文件,防任意代码)"""
    os.makedirs(_BUILD_SKILL_D, exist_ok=True); saved = []
    for f in request.files.getlist("files"):
        fn = os.path.basename(f.filename or "skill.md")
        if not re.match(r"^[\w\-. ]+$", fn): continue
        if not fn.lower().endswith((".md", ".txt")): fn = re.sub(r"\.\w+$", "", fn) + ".md"
        try: open(os.path.join(_BUILD_SKILL_D, fn), "wb").write(f.read()); saved.append(fn)
        except Exception: pass
    return jsonify({"saved": saved})

@app.get("/api/build/defaults")
def build_defaults():
    """本体构建页各表单的可用默认参数(前端预填,可改):均指向本机真实可用资源"""
    return jsonify({
        "sqlite_path": os.path.abspath(DB),                       # 真实 示例 库绝对路径(填入即可校验通过)
        "conn_name": "示例制造数据库",
        "build_name": "示例企业本体",
        "build_query": "从 示例主库自动识别核心对象、事件与关系,构建一张可审计的制造企业本体(对象/事件/关系,每条关系带数据取证)",
        "default_skill": "ontology-agentic",                       # 默认编排技能
        "ext": {"kind": "hive", "host": "<hive-host>", "port": "10000", "db": "default"},
    })

@app.get("/api/build/built")
def build_built():
    """已构建本体清单(built_*.json),供本体构建页展示历史产物"""
    out = []
    for p in sorted(glob.glob(os.path.join(WORK, "built_*.json")), key=os.path.getmtime, reverse=True):
        k = os.path.basename(p)[:-5]; ir = _load_json(p)
        if not isinstance(ir, dict) or not ir.get("objects"): continue
        g = ir_to_graph(k, ir)
        objs = ir.get("objects", []); rels = ir.get("relations", []) or ir.get("links", [])
        ev = sum(1 for o in objs if o.get("kind") == "event")
        ver = sum(1 for r in rels if r.get("status") == "verified")
        sc = ir.get("scenario") or {}
        out.append({"key": k, "name": sc.get("name") or k, "style": sc.get("style", ""),
                    "objects": len(g["nodes"]), "events": ev, "links": len(g["edges"]), "verified": ver,
                    "ts": time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return jsonify(out)

@app.post("/api/build/delete")
def build_delete():
    """删除一个已构建本体产物"""
    k = (request.json or {}).get("key", "")
    if not re.match(r"^built_[\w]+$", k): return jsonify({"error": "非法 key"}), 400
    p = os.path.join(WORK, k + ".json")
    try:
        if os.path.exists(p): os.remove(p)
    except Exception as e: return jsonify({"error": str(e)[:80]}), 500
    return jsonify({"ok": True})

def _build_intent(q, sname, skills):
    """Hermes 解析建模意图 → {name, strategy, note}"""
    from agent_runtime import get_runtime, available
    sk = ("；可用技能:" + "、".join(skills)) if skills else ""
    prompt = f"""你是企业本体建模编排 agent。用户诉求:「{q}」;数据源:{sname}{sk}。
只输出一个 JSON(无其它文字):{{"name":"目标本体简名(≤12字)","strategy":"一句话建模策略","note":"给用户的一句话说明"}}"""
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"bi_{uuid.uuid4().hex[:6]}", prompt, timeout=28)
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try: return json.loads(m.group(0))
                except Exception: pass
    return None

def _build_summary(q, name, ir):
    """Hermes 生成本体说明(基于已构建 IR,不编造)"""
    from agent_runtime import get_runtime, available
    objs = ir.get("objects", []); rels = ir.get("relations", [])
    brief = json.dumps({"name": name,
                        "objects": [o.get("name") for o in objs[:30]],
                        "events": [o.get("name") for o in objs if o.get("kind") == "event"][:15],
                        "sample_links": [{"s": l.get("source_concept"), "v": l.get("verb"),
                                          "t": l.get("target_concept"), "status": l.get("status")} for l in rels[:15]]},
                       ensure_ascii=False)[:2500]
    prompt = f"用户想建的本体:「{q}」。已数据驱动构建出:{brief}。用中文写 3-5 句本体说明:覆盖哪些核心对象/事件、关系可信度如何、可支撑什么分析。仅基于给定内容,不要编造。直接输出文本。"
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"bs_{uuid.uuid4().hex[:6]}", prompt, timeout=33)
        if ok and reply and not _looks_like_error(reply):
            return reply.strip()[:1400]
    return None

def _build_rule_summary(name, ir):
    objs = ir.get("objects", []); rels = ir.get("relations", [])
    ev = [o.get("name") for o in objs if o.get("kind") == "event"]
    ver = sum(1 for l in rels if l.get("status") == "verified")
    return (f"本体「{name}」共构建 {len(objs)} 个对象(含 {len(ev)} 个事件对象)、{len(rels)} 条关系"
            f"(其中 {ver} 条经取值重叠/父键唯一验证)。核心对象:{('、'.join(o.get('name') for o in objs[:8])) or '—'}。"
            f"可支撑对象画像、关系溯源与跨表指标分析。(引擎离线,此为规则化摘要)")

# ── 多模态 LLM 本体自动构建(算法亮点):证据聚合 → LLM 抽取 → 真实数据反造假取证 ──
_SKILL_METHOD = {
    "ontology-build": "从数据库+建表代码+业务代码(视图/ETL)+行业知识构建可审计企业本体,对象/事件/关系/指标齐备。",
    "gov-app-ontology-build": "构建应用本体:显式区分对象(object)、动作(action)、事件(event),绑定治理资产。",
    "ontology-forge": "遵循 OWL2 本体公理、Palantir 操作型本体四层(对象/属性/链接/动作)、SHACL 约束与斯坦福七步法。",
    "ontology-agentic": "按 规划→取证→提议→工具裁决→critic 对抗→迭代 的 agentic 流程,只保留有数据/文档证据支撑的结论。",
    "auto-ontology": "以对象为中心补全属性、指标与关系,标注候选语义待确认。"}
_TEXT_EXT = ("sql", "ddl", "py", "md", "txt", "json", "xml", "yaml", "yml", "csv", "tsv", "js", "java", "sh")

def _read_asset_text(fname, cap=3500):
    """读取上传的可文本化多模态资产(建表代码/业务文档/知识片段/Excel 知识包);二进制/图像返回空(标注为引用证据)"""
    p = os.path.join(WORK, "uploads_" + fname)
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if not os.path.exists(p): return ""
    if ext in ("xlsx", "xls"):
        # Excel 知识包(看板/指标口径等):抽工作表名 + 前若干行,喂 LLM 以获得有业务意义的中文命名
        try:
            import openpyxl
            wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
            out = []                                     # 取样即可(供 LLM 感知术语口径),不喂全表,避免撑大 prompt 拖慢/超时
            for sn in wb.sheetnames[:5]:
                rows = []
                for i, r in enumerate(wb[sn].iter_rows(values_only=True)):
                    if i >= 12: break
                    cells = [str(c) for c in r if c not in (None, "")]
                    if cells: rows.append(" | ".join(cells)[:180])
                if rows: out.append(f"[工作表「{sn}」]\n" + "\n".join(rows))
            wb.close()
            return ("\n".join(out))[:cap]
        except Exception:
            return ""
    if ext in _TEXT_EXT:
        try: return open(p, encoding="utf-8", errors="replace").read()[:cap]
        except Exception: return ""
    return ""

def _gather_evidence(db, cap_tabs=400, cap_docs=6):
    """聚合多模态证据:① 库表结构(表→列)② 上传的文档/代码文本 ③ 图像/二进制的引用清单"""
    schema, tab_cols = [], {}
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")][:cap_tabs]
        for t in tabs:
            try:
                cols = [(r[1], r[2] or "TEXT") for r in c.execute(f'PRAGMA table_info("{t}")')]
            except Exception:
                cols = []
            tab_cols[t] = cols                       # 保留全部表(供关系取证的表名映射)
        c.close()
    except Exception:
        pass
    def _pri(t):                                     # schema 文本按语义重要度排序,确保 fact_/dws_ 不被截断丢弃
        tl = t.lower()
        for i, p in enumerate(("fact_", "dws_", "dwd_", "agg_", "dim_", "ods_", "stg_")):
            if tl.startswith(p): return i
        return 9
    for t in sorted(tab_cols, key=lambda x: (_pri(x), x)):
        schema.append(f"{t}({', '.join(cn for cn, _ in tab_cols[t][:22])})")
    docs, refs, used = [], [], 0
    for p in sorted(glob.glob(os.path.join(WORK, "uploads_*"))):
        fn = os.path.basename(p)[8:]
        ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
        txt = _read_asset_text(fn)
        if txt.strip() and len(docs) < cap_docs and used < 16000:
            docs.append(f"### 证据文件「{fn}」({_MOD_MAP.get(ext,'其它')})\n{txt}")
            used += len(txt)
        elif ext in ("png", "jpg", "jpeg", "gif", "svg", "wav", "mp3"):
            refs.append(f"{fn}({_MOD_MAP.get(ext,'其它')})")
    return {"schema": "\n".join(schema), "tab_cols": tab_cols,
            "docs": "\n\n".join(docs), "n_docs": len(docs), "refs": refs}

def _skill_method_text(skills):
    """选中技能 → 方法论文本:内置走 _SKILL_METHOD 摘要;自定义读其 md 正文(去 front-matter,截断)。
    自定义技能自此真正参与构建(编辑内容会改变抽取行为,而非仅作展示)。"""
    parts = []
    for s in skills:
        if s in _SKILL_METHOD:
            parts.append(_SKILL_METHOD[s])
        else:
            p = _custom_skill_path(s)
            if p:
                try: body = _skill_body(open(p, encoding="utf-8", errors="replace").read())
                except Exception: body = ""
                if body: parts.append(f"〔自定义技能 {s}〕{body[:1200]}")
    return "；".join(parts)

def _dg_bounds(causes, allow_set):
    """诊断质量门 G1(确定性):路径必须只引用白名单对象;越界即降 candidate 并标记。
    从原单次调用的 ④ 边界校验抽出复用,流水线各节点共用同一把尺。"""
    out = []
    for c in (causes or [])[:5]:
        path = str(c.get("path") or "")
        toks = [t for t in re.split(r"[→\->,、\s]+", path) if t]
        inb = all(t in allow_set for t in toks) if toks else False
        conf = c.get("confidence") if c.get("confidence") in ("verified", "candidate") else "candidate"
        if not inb: conf = "candidate"
        out.append({"cause": str(c.get("cause") or "")[:80], "path": path[:120],
                    "evidence": str(c.get("evidence") or "")[:120], "confidence": conf, "in_bounds": inb})
    return out

def _dg_rank(causes):
    """诊断评估节点(确定性打分,不调 LLM)。

    对标 Trane M3 的"评估 Agent",但刻意不用 LLM:排序依据是可枚举的客观特征
    ——是否在本体边界内、置信度、证据是否具体到表/字段。规则打分可复现、可解释、零延迟,
    比让模型自评"严重程度"更可信(模型自评正是该文警告的循环论证)。
    """
    def score(c):
        s = 0
        if c.get("in_bounds"): s += 4                     # 路径落在已验证的本体关系内
        if c.get("confidence") == "verified": s += 3      # 依据已验证关系
        ev = c.get("evidence") or ""
        if re.search(r"[A-Za-z_]{3,}", ev): s += 2        # 证据点到了具体表/字段名
        if len(ev) >= 12: s += 1                          # 证据具体而非空话
        return s
    ranked = sorted(causes or [], key=lambda c: -score(c))
    for i, c in enumerate(ranked, 1):
        c["rank"] = i; c["rank_score"] = score(c)
        c["rank_basis"] = "、".join(filter(None, [
            "路径在本体边界内" if c.get("in_bounds") else "路径越界",
            "依据已验证关系" if c.get("confidence") == "verified" else "依据待验证关系",
            "证据指向具体字段" if re.search(r"[A-Za-z_]{3,}", c.get("evidence") or "") else "证据未指向字段"]))
    return ranked

def _dg_checklist_gate(items, allow_set, tables):
    """诊断质量门 G2(确定性):检查清单只能引用白名单对象或真实表名。

    对标该文的"审核 Agent",但用规则而非 LLM——合规性检查本质是集合匹配,让模型自审
    等于把裁判权交还给被审对象。越界项不静默丢弃,而是标记出来供人看见(可审计)。
    """
    kept, flagged = [], []
    tl = {t.lower() for t in tables if t}
    for x in (items or [])[:8]:
        s = str(x)[:60]
        refs = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", s)
        bad = [r for r in refs if r.lower() not in tl and r not in allow_set]
        (flagged if bad else kept).append({"item": s, "unknown_refs": bad[:3]} if bad else s)
    return kept, flagged

_ACC_MIN_N = 5          # 样本下限:低于此不报准确率——小样本百分比是噪声,不是证据
_ACC_CACHE = {"ts": 0, "val": None}

def _iter_all_irs():
    """遍历所有可读图谱的 IR(种子 + 构建/锻造产物),用于跨图谱聚合人审历史。"""
    # 必须用 load_ir_edited:人审结论写在编辑栈里,基线 IR 上看不到(实测基线 0 条 / 应用编辑后 7 条)
    for k in list(IR_SOURCES.keys()):
        try:
            ir = load_ir_edited(k)
        except Exception:
            ir = None
        if ir: yield k, ir
    for pth in sorted(glob.glob(os.path.join(WORK, "built_*.json"))):
        k = os.path.basename(pth)[:-5]
        try:
            ir = load_ir_edited(k)
        except Exception:
            ir = _load_json(pth)
        if isinstance(ir, dict): yield k, ir

def _review_history(force=False):
    """⑥ 可解释性第三问「历史上类似情况准确率?」——聚合人审结论。

    口径:只统计带 human_review 的关系,按**系统原判**(review_prior_status)分组算人审同意率。
    这才是工程师真正要问的那一问:"系统说 verified 时,人有多少次认同?"
    另按动词分组,暴露"哪类语义关系机器最容易判错"。

    诚实纪律:样本 < _ACC_MIN_N 时返回 insufficient 且**不给百分比**——两三条记录算出的
    百分比是噪声而非证据,展示出来只会误导工程师建立错误的信任。缓存 5 分钟。
    """
    if not force and _ACC_CACHE["val"] is not None and time.time() - _ACC_CACHE["ts"] < 300:
        return _ACC_CACHE["val"]
    by_prior, by_verb, total = {}, {}, {"approved": 0, "rejected": 0}
    for _k, ir in _iter_all_irs():
        for r in (ir.get("relations") or ir.get("links") or []):
            hr = r.get("human_review")
            if hr not in ("approved", "rejected"): continue
            total[hr] += 1
            pri = r.get("review_prior_status") or "unknown"
            by_prior.setdefault(pri, {"approved": 0, "rejected": 0})[hr] += 1
            vb = (r.get("verb") or "未标注").strip()
            by_verb.setdefault(vb, {"approved": 0, "rejected": 0})[hr] += 1
    def rate(d):
        n = d["approved"] + d["rejected"]
        out = {"n": n, "approved": d["approved"], "rejected": d["rejected"]}
        if n >= _ACC_MIN_N: out["agree_rate"] = round(d["approved"] / n * 100, 1)
        else: out["insufficient"] = f"样本 {n} 条(< {_ACC_MIN_N}),不给准确率"
        return out
    n_all = total["approved"] + total["rejected"]
    val = {"min_n": _ACC_MIN_N, "reviewed": n_all,
           "overall": rate(total) if n_all else {"n": 0, "insufficient": "尚无人审记录"},
           "by_prior_status": {k: rate(v) for k, v in sorted(by_prior.items())},
           "by_verb": {k: rate(v) for k, v in sorted(by_verb.items(), key=lambda x: -(x[1]["approved"] + x[1]["rejected"]))[:12]}}
    _ACC_CACHE.update(ts=time.time(), val=val)
    return val

def _rejected_patterns(limit=8):
    """⑤ 反馈闭环:把人审否决过的关系模式回流成"已知误判模式",注入构建上下文。

    对标 Trane M4 增强注册表里的"已知故障模式"——让 AI 看到的不只是当前输入,还有
    这类判断历史上踩过的坑。人审已经把结论写在 IR 上了,此前只是没接回提议环节。
    只回流**否决**(负样本):它是稀缺且高信息量的;通过的关系本就在图谱里当正样本。
    """
    pats, seen = [], set()
    for _k, ir in _iter_all_irs():
        objs = {o.get("id"): o for o in (ir.get("objects") or [])}
        for r in (ir.get("relations") or ir.get("links") or []):
            if r.get("human_review") != "rejected": continue
            so, to = objs.get(r.get("source")), objs.get(r.get("target"))
            s = (so or {}).get("table") or r.get("source") or ""
            t = (to or {}).get("table") or r.get("target") or ""
            key = (s.lower(), t.lower(), (r.get("verb") or "").strip())
            if not (s and t) or key in seen: continue
            seen.add(key)
            why = (r.get("review_reason") or "").strip()
            pats.append(f'{s} →[{r.get("verb") or "?"}]→ {t}' + (f'(人审否决理由:{why[:40]})' if why else "(人审判为语义不成立)"))
            if len(pats) >= limit: return pats
    return pats

def _relset(extracted):
    """关系集合指纹:规范化 (source, target, verb) 三元组,供两次独立生成做集合比较。
    大小写与首尾空白归一,避免同一关系因书写差异被判为不同。"""
    out = set()
    for r in (extracted or {}).get("relations", []) or []:
        s, t = str(r.get("source") or "").strip().lower(), str(r.get("target") or "").strip().lower()
        v = str(r.get("verb") or "").strip()
        if s and t: out.add((s, t, v))
    return out

def _stability_annotate(first, second):
    """M1 迭代相似度收敛:用两次独立生成的 Jaccard 量化提议一致性,并逐条标注。

    对标 Trane M1(arXiv:2603.10047)。与该文的关键差别在于**一致性不作否决权**:
    数据裁决(取值重叠 ∧ 父键唯一)是确定性硬证据,若数据见证了某关系,不应因 LLM 这次
    提议不稳就降级——那等于用软信号推翻硬证据。故此处只做两件事:
      ① 给每条关系记 stable=True/False,供人审看到"该关系仅在 1/2 次生成中出现";
      ② 聚合出 jaccard 一致性分,量化引擎方差(补足论文 §8「引擎方差未量化」)。
    返回 (一致性统计, 被两次都提出的关系数)。
    """
    a, b = _relset(first), _relset(second)
    inter, union = a & b, a | b
    jac = round(len(inter) / len(union), 4) if union else 0.0
    for r in (first or {}).get("relations", []) or []:
        s, t = str(r.get("source") or "").strip().lower(), str(r.get("target") or "").strip().lower()
        r["stable"] = (s, t, str(r.get("verb") or "").strip()) in inter
    return {"jaccard": jac, "run1": len(a), "run2": len(b), "both": len(inter), "union": len(union)}, len(inter)

def _llm_extract_ontology(q, ev, skills):
    """多模态 LLM 抽取:综合库结构 + 文档/代码证据 → 提议 objects/relations(JSON)"""
    from agent_runtime import get_runtime, available
    method = _skill_method_text(skills)
    docs_block = ("\n[上传的多模态证据:建表代码/业务文档/知识片段]\n" + ev["docs"]) if ev["docs"] else ""
    refs_block = ("\n[引用但未解析的资产:" + "、".join(ev["refs"]) + "]") if ev["refs"] else ""
    _rp = _rejected_patterns()                       # ⑤ 反馈闭环:人审否决过的模式回流,别再犯同一个错
    bad_block = ("\n[已知误判模式(历史上被人审否决,勿再提议同类关系)]\n" + "\n".join("- " + x for x in _rp)) if _rp else ""
    prompt = f"""你是企业本体自动抽取智能体,综合结构化库表与多模态文档证据构建本体。
建模目标:{q}
{('建模方法论:' + method) if method else ''}
[数据库表结构(权威真值,对象优先绑定到这些真实表)]
{ev['schema'][:12000]}{docs_block[:9000]}{refs_block}{bad_block}

只输出一个 JSON(无其它文字):
{{"objects":[{{"name":"英文标识(能对齐表名就用表名)","cn":"有业务意义的中文名","kind":"object|event|asset|role|ice(信息记录:目录/单据/地址/台账等,非物理实体)","table":"绑定的真实表名或 null","evidence":"抽取依据(来自哪张表/哪份文档)","definition":"属+种差定义(如『销售订单是一种记录客户购买承诺的信息内容实体』);给不出严格定义就留空","example":"一个正例","counterExample":"一个易混淆的反例(如 报价单——尚无承诺)"}}],
  "relations":[{{"source":"对象name","target":"对象name","verb":"具体关系动词(归属/产生/包含/服务/触发…)","rationale":"依据"}}]}}
要求:①对象尽量绑定真实表;②由文档/流程推断出的业务事件用 kind=event;库存记录/地址/目录/单据等信息性条目用 kind=ice(BFO 信息内容实体,勿与物理实体混淆);③关系两端必须是上面列出的对象 name;④不虚构库表和文档中都没有的实体或关系;⑤**cn 必须是有业务意义的中文名**(如 客户 / 销售订单 / 退货事件 / 生产工单),优先复用表注释、上传文档/知识包(如看板指标口径)里的中文术语,严禁用拼音或直接照搬英文表名/键名做 cn;⑥**借鉴 IOF 定义纪律**:definition 用「属+种差」句式;**非循环**——定义体不得复用被定义术语名本身及其中文名(如定义『销售订单』不得出现『销售订单』字样),须用上位类(属)+区别特征(种差)描述;counterExample 给一个会被误认成该对象、实则不是的反例(帮助后续取证辨伪);无法给出严格充要定义时 definition 留空即可(将被标为原始概念)。"""
    for drv in _drv_order():
        if drv not in available(): continue
        ok, reply = get_runtime(drv).run_turn(f"be_{uuid.uuid4().hex[:6]}", prompt, timeout=600)
        if ok and reply and not _looks_like_error(reply):
            m = re.search(r"\{[\s\S]*\}", reply)
            if m:
                try:
                    d = json.loads(m.group(0))
                    if isinstance(d.get("objects"), list) and d["objects"]:
                        return d
                except Exception:
                    pass
    return None

def _llm_semantic_review(relations, ev):
    """三级控制环第二级:LLM 仅凭 schema 语义复审提议关系(不看数据)。
    返回 {(source,target): bool};引擎离线/超时返回 None(调用方记 skipped,绝不臆造)。
    实证依据:共享域巧合(数据为真、语义为假)只有语义评审能拦(判别实验 9 vs 2)。"""
    try:
        from agent_runtime import get_runtime, available
        if not available(): return None
    except Exception:
        return None
    sch = (ev.get("schema") or "")[:9000]
    out = {}
    for i in range(0, len(relations), 60):
        chunk = relations[i:i + 60]
        lines = [f'{j}. {r["source_concept"]} —{r.get("verb","关联")}→ {r["target_concept"]}' for j, r in enumerate(chunk)]
        prompt = ("你是数据库语义审阅专家。仅凭表结构判断每条提议关系在语义上是否为真实的业务引用"
                  "(同名巧合/共享取值域/顺序编号巧合应判 false;不要臆测数据)。\n"
                  f"表结构:\n{sch}\n提议:\n" + "\n".join(lines) +
                  '\n\n只输出JSON:{"<序号>":true/false,...}')
        got = None
        for drv in _drv_order():
            try:
                if drv not in available(): continue
                ok, rep = get_runtime(drv).run_turn(f"sr_{uuid.uuid4().hex[:6]}", prompt, timeout=110)
                if ok and rep:
                    m = re.search(r"\{[\s\S]*\}", rep)
                    if m:
                        d = json.loads(m.group(0))
                        got = {int(k): v for k, v in d.items() if str(k).isdigit()}
                        break
            except Exception:
                continue
        if got is None: return out or None
        for j, r in enumerate(chunk):
            if j in got and isinstance(got[j], bool):
                out[(r["source_concept"], r["target_concept"])] = got[j]
    return out

def _adjudicate_ir(db, name, extracted, ev):
    """反造假取证:LLM 提议的关系用真实数据裁决(取值重叠≥60%∧父键可辨→verified;有据无量→candidate)。
    产出与 ir_to_graph 兼容的 IR(objects 带 kind/table/attrs;relations 带 status)。"""
    tc = ev["tab_cols"]; low2real = {t.lower(): t for t in tc}
    # 归一对象:绑定真实表则补 attrs/field_count;非表对象标 candidate
    objects, name2tab, seen_obj = [], {}, set()
    for o in extracted.get("objects", []):
        nm = (o.get("name") or "").strip()
        if not nm or nm in seen_obj: continue        # LLM 可能重复同名对象,去重防图谱节点 id 冲突
        seen_obj.add(nm)
        tb = o.get("table")
        real = low2real.get((tb or "").lower()) or (low2real.get(nm.lower()))
        attrs = [{"col": c, "cn": "", "type": ty} for c, ty in tc.get(real, [])] if real else []
        kind = o.get("kind") if o.get("kind") in ("object", "event", "asset", "role", "ice") else "object"
        defn = (o.get("definition") or "").strip()
        # 证据来源(IOF-AV provenance):绑定的真实表为 directSource;文档抽取依据入 adaptedFrom
        ev_src = (o.get("evidence") or "").strip()
        doc_src = [ev_src] if (ev_src and not real) else []
        objects.append({"name": nm, "cn": o.get("cn") or nm, "kind": kind,
                        "table": real, "tables": [real] if real else [], "field_count": len(attrs),
                        "attrs": attrs, "candidate": not bool(real), "indicators": [],
                        "evidence": {"sources": [ev_src] if ev_src else []},
                        "remark": ev_src,
                        # ── IOF-AV 机读注释(借鉴 Industrial Ontology Foundry)──
                        "bfo": _KIND_BFO.get(kind, "MaterialEntity"),
                        "definition": defn, "isPrimitive": (not defn),          # 无充要定义→标原始概念
                        "example": (o.get("example") or "").strip(),
                        "counterExample": (o.get("counterExample") or "").strip(),
                        "maturity": "Provisional",                              # 新建产物默认临时,经审可升 Released
                        "provenance": {"directSource": real, "adaptedFrom": doc_src, "excerptedFrom": None}})
        if real: name2tab[nm] = real
    valid = {o["name"] for o in objects}

    con = None
    try: con = ro_connect(db)
    except Exception: con = None
    def distinct(t, c, cap=8000):
        try: return set(r[0] for r in con.execute(f'SELECT DISTINCT "{c}" FROM "{t}" LIMIT {cap}') if r[0] not in (None, ""))
        except Exception: return set()
    def is_unique(t, c):
        try:
            tot, dis = con.execute(f'SELECT COUNT("{c}"), COUNT(DISTINCT "{c}") FROM "{t}"').fetchone()
            return tot and tot == dis
        except Exception: return False

    def pair_distinct(t, c1, c2, cap=8000):
        """二列元组取值集(复合键联合裁决用;跳过含空的行)。"""
        try:
            return set((r[0], r[1]) for r in con.execute(
                f'SELECT DISTINCT "{c1}", "{c2}" FROM "{t}" WHERE "{c1}" IS NOT NULL AND "{c2}" IS NOT NULL LIMIT {cap}'))
        except Exception: return set()
    def pair_unique(t, c1, c2):
        try:
            tot, dis = con.execute(
                f'SELECT COUNT(*), COUNT(DISTINCT "{c1}" || CHAR(31) || "{c2}") FROM "{t}" '
                f'WHERE "{c1}" IS NOT NULL AND "{c2}" IS NOT NULL').fetchone()
            return tot and tot == dis
        except Exception: return False
    # 声明主键感知(Burr-Mondial 发现:自然键 schema 的父键不叫 *_id,须查 PK;企业 *_id 命名此前掩盖了该盲区)
    pkmap = {}
    if con:
        for tt2 in {o.get("table") for o in objects if o.get("table")}:
            try: pkmap[tt2] = [r2[1] for r2 in con.execute(f'PRAGMA table_info("{tt2}")') if r2[5]]
            except Exception: pkmap[tt2] = []

    relations, seen = [], set()
    for r in extracted.get("relations", []):
        s, t = (r.get("source") or "").strip(), (r.get("target") or "").strip()
        if not s or not t or s == t or s not in valid or t not in valid or (s, t) in seen: continue
        seen.add((s, t))
        status, overlap, note = "candidate", None, "LLM 提议·待取证"
        ts, tt = name2tab.get(s), name2tab.get(t)
        if con and ts and tt:
            cs = [c for c, _ in tc.get(ts, [])]; ct = [c for c, _ in tc.get(tt, [])]
            ctl = [x.lower() for x in ct]
            stem = re.sub(r"^(dim_|fact_|dws_|dwd_|ods_|agg_)", "", tt, flags=re.I).lower()
            pk_t = pkmap.get(tt) or []
            # 连接键候选(多候选逐一尝试直到验证——Burr 系列实证:首个候选失败不代表无引用):
            # ①后缀词干 ②等值(列名==父表词干/表名,无后缀键名) ③前缀(Country1→Country) ④与父列同名
            cand_keys = []
            for c in cs:
                cl = c.lower()
                if re.search(r"_(id|code)$", c, re.I) and (re.sub(r"_(id|code)$", "", c, flags=re.I).lower() in stem or cl in ctl):
                    cand_keys.append(c)
            for c in cs:
                cl = c.lower()
                if c not in cand_keys and (cl == stem or cl == tt.lower()): cand_keys.append(c)
            for c in cs:
                cl = c.lower()
                if c not in cand_keys and len(stem) >= 4 and cl.startswith(stem) and re.fullmatch(r"[a-z]*\d?", cl[len(stem):]):
                    cand_keys.append(c)
            for c in cs:
                if c not in cand_keys and c.lower() in ctl and re.search(r"(id|code|key|no)$", c, re.I): cand_keys.append(c)
            best_ov = None
            for key in cand_keys[:6]:
                # 父列候选序:声明PK优先(Mondial 发现:父表可有与表同名的非键列,同名优先会撞错列)
                pcols = []
                if len(pk_t) == 1: pcols.append(pk_t[0])
                for x in ct:
                    if x.lower() == key.lower() and x not in pcols: pcols.append(x)
                for x in ct:
                    if x.lower() == "id" and x not in pcols: pcols.append(x)
                for x in ct:
                    if re.search(r"_(id|code)$", x, re.I) and x not in pcols: pcols.append(x)
                child = distinct(ts, key)
                if not child: continue
                for pcol in pcols[:3]:
                    ov = 100.0 * len(child & distinct(tt, pcol)) / len(child)
                    if best_ov is None or ov > best_ov: best_ov, overlap = ov, round(ov, 1)
                    if ov >= 60 and is_unique(tt, pcol):
                        status, note = "verified", f"{key}→{tt}.{pcol} 重叠{ov:.0f}%·父键唯一"; break
                if status == "verified": break
            if status != "verified" and best_ov is not None:
                note = f"弱重叠{best_ov:.0f}%,送审" if best_ov >= 20 else f"重叠仅{best_ov:.0f}%,存疑"
            # 复合键二列联合裁决(Burr-Mondial 发现:单列重叠无法见证复合外键):
            # 单列未 verified 时,取两端同名列对(≤2列)做元组重叠 ∧ 父侧成对唯一
            if status != "verified":
                shared = [c for c in cs if c.lower() in ctl][:4]
                # 父表二列复合 PK 且子表同名俱备 → 该对优先(声明键即语义键)
                if len(pk_t) == 2 and all(p.lower() in [c.lower() for c in cs] for p in pk_t):
                    pri = [next(c for c in cs if c.lower() == p.lower()) for p in pk_t]
                    shared = pri + [c for c in shared if c not in pri]
                for i in range(len(shared)):
                    for j in range(i + 1, len(shared)):
                        c1, c2 = shared[i], shared[j]
                        p1 = next(x for x in ct if x.lower() == c1.lower())
                        p2 = next(x for x in ct if x.lower() == c2.lower())
                        chp = pair_distinct(ts, c1, c2)
                        if not chp: continue
                        ovp = 100.0 * len(chp & pair_distinct(tt, p1, p2)) / len(chp)
                        if ovp >= 60 and pair_unique(tt, p1, p2):
                            status, overlap = "verified", round(ovp, 1)
                            note = f"复合键({c1},{c2})→{tt} 元组重叠{ovp:.0f}%·父键成对唯一"
                            break
                    if status == "verified": break
        verb = r.get("verb", "关联")
        fr, tq = _ground_verb(verb)                       # 接地到 BFO 有根据关系 + 时间指标(IOF 借鉴)
        relations.append({"source_concept": s, "target_concept": t, "verb": verb,
                          "status": status, "overlap": overlap, "note": note,
                          "founded_relation": fr, "temporal": tq})
    if con: con.close()
    # ── 三级控制环第二级:LLM 语义评审(样本判别实验:语义与数据滤除不相交 FP,组合最优)──
    # 不改变 verified(其语义=经数据见证),仅附 semantic 标注供人审;引擎离线记 skipped,不臆造
    _sem_budget = 90 + 120 * ((len(relations) + 59) // 60)   # 每批(≤60) ~120s 预算,防多批被外层截断
    sem = _bounded(lambda: _llm_semantic_review(relations, ev), _sem_budget) if relations else None
    for rel in relations:
        v = (sem or {}).get((rel["source_concept"], rel["target_concept"]))
        rel["semantic"] = "pass" if v is True else ("fail" if v is False else "skipped")
        if v is False and rel["status"] == "verified":
            rel["note"] += ";语义评审存疑(数据成立但语义可疑,建议人审)"
    ir = {"scenario": {"name": name, "style": "multimodal-llm(多模态LLM抽取+反造假取证)",
                       "object_count": len(objects), "relation_count": len(relations),
                       "evidence": {"tables": len(tc), "docs": ev["n_docs"], "refs": ev["refs"]}},
          "objects": objects, "relations": relations}
    return ir

@app.post("/api/build/inquire")
def build_inquire():
    """Hermes 本体构建问询台:对话式驱动 → 意图解析 → 数据驱动构建(反造假规则) → agent 命名/摘要。SSE 流式 agentic 步骤。"""
    body = request.json or {}
    q = (body.get("q") or "").strip()
    source = body.get("source") or "uploads"
    name = (body.get("name") or "").strip()
    skills = body.get("skills") or []
    stability = bool(body.get("stability"))    # M1 opt-in:二次独立生成量化一致性(构建耗时翻倍)
    def sse(o): return "data: " + json.dumps(o, ensure_ascii=False, default=str) + "\n\n"
    def gen():
        import time as _t
        t0 = _t.time()
        def push(step, ok, info=""):
            return sse({"type": "step", "step": step, "ok": ok, "info": info, "ts": _t.strftime("%H:%M:%S")})
        if not q:
            yield sse({"type": "error", "error": "请输入构建诉求"}); return
        db, sname = None, source
        if source == "demo": db, sname = DB, "示例主库"
        elif source == "uploads": db, sname = UPLOAD_DB, "上传数据库"
        else:
            for c in _load_conns():
                if c.get("id") == source:
                    sname = c.get("name")
                    if c.get("kind") == "sqlite" and c.get("path") and os.path.exists(c["path"]):
                        db = c["path"]
                    else:
                        yield push("scope_source", False, f"「{sname}」为外部库,当前离线不可取数,请改选可用 SQLite 源")
                        yield sse({"type": "error", "error": "数据源不可用(外部库需内网/驱动)"}); return
                    break
        if not db or not os.path.exists(db):
            yield push("scope_source", False, "数据源不存在,请先上传数据或连接库")
            yield sse({"type": "error", "error": "无可用数据源"}); return
        if not _sqlite_tables(db):
            yield push("scope_source", False, f"「{sname}」无可读表,请先上传/建表")
            yield sse({"type": "error", "error": "数据源为空"}); return
        yield push("intake", True, f"接收构建诉求 · 数据源「{sname}」· 编排技能 {len(skills)} 个")
        yield sse({"type": "status", "text": "多智能体引擎解析建模意图与范围…"})
        plan = _bounded(lambda: _build_intent(q, sname, skills), 30) or {}
        gname = name or plan.get("name") or (q[:14] + "本体")
        yield push("intent", True, f"意图解析 · 目标本体「{gname}」· 策略:{plan.get('strategy', '多模态 LLM 抽取 + 反造假取证')}")
        if skills:
            yield push("orchestrate", True, "编排技能方法论:" + "、".join(skills[:5]))
            _mt = _skill_method_text(skills)          # #2 技能注入痕迹:方法论进 prompt 在流水线里可见
            _nseg = sum(1 for s in skills if s in _SKILL_METHOD or _custom_skill_path(s))
            yield push("skill_inject", bool(_mt), f"技能注入 · {_nseg} 段方法论并入抽取 prompt(共 {len(_mt)} 字)" if _mt
                       else "技能注入 · 选中技能无可注入正文(内容为空?)")
        key = "built_" + uuid.uuid4().hex[:6]; outp = os.path.join(WORK, key + ".json")
        # ① 多模态证据聚合(库结构 + 上传文档/代码 + 图像引用)
        yield sse({"type": "status", "text": "聚合多模态证据(库表结构 / 建表代码 / 业务文档 / 图像引用)…"})
        ev = _gather_evidence(db)
        ntab = len(ev["tab_cols"])
        emeta = f"库表 {ntab} 张"
        if ev["n_docs"]: emeta += f" · 文档/代码证据 {ev['n_docs']} 份"
        if ev["refs"]: emeta += f" · 引用资产 {len(ev['refs'])} 项"
        yield push("gather_evidence", True, "取证:" + emeta)
        method = "多模态 LLM 抽取"
        ir = None
        # ② 多模态 LLM 抽取(算法亮点):真实调用底层引擎,综合结构化+非结构化证据提议本体
        yield sse({"type": "status", "text": "多模态智能体抽取实体与关系(综合表结构与文档证据,约 2-4 分钟)…"})
        extracted = _bounded(lambda: _llm_extract_ontology(q, ev, skills), 640)
        if extracted and extracted.get("objects"):
            yield push("llm_extract", True, f"LLM 抽取 · 对象 {len(extracted.get('objects', []))} 个 · 提议关系 {len(extracted.get('relations', []))} 条")
            _stab = None
            if stability:      # M1:再独立生成一次,用 Jaccard 量化提议一致性(不作否决,仅记录+提示人审)
                yield sse({"type": "status", "text": "一致性门控:第二次独立生成中(用于量化引擎方差,约 2-4 分钟)…"})
                second = _bounded(lambda: _llm_extract_ontology(q, ev, skills), 640)
                if second and second.get("relations") is not None:
                    _stab, _both = _stability_annotate(extracted, second)
                    yield push("stability", True,
                               f"一致性:Jaccard {_stab['jaccard']} · 两次均提出 {_stab['both']}/{_stab['union']} 条"
                               f"(仅标注不否决——数据裁决才是硬证据)")
                else:
                    yield push("stability", False, "第二次生成失败/超时,本次不产出一致性指标(不臆造)")
            # ③ 反造假取证:LLM 提议的关系用真实数据裁决
            yield sse({"type": "status", "text": "反造假取证:用真实数据校验每条提议关系(取值重叠 / 父键唯一)…"})
            ir = _adjudicate_ir(db, gname, extracted, ev)
            if _stab: ir["stability"] = _stab          # M1 一致性指标随图谱留档,供论文与人审引用
            _sc = {"pass": 0, "fail": 0, "skipped": 0}
            for _r in ir.get("relations", []): _sc[_r.get("semantic", "skipped")] = _sc.get(_r.get("semantic", "skipped"), 0) + 1
            if _sc["pass"] + _sc["fail"]:
                yield push("semantic_review", True, f"语义评审:{_sc['pass']} 通过 · {_sc['fail']} 存疑(建议人审)")
            else:
                yield push("semantic_review", True, "语义评审:引擎不可用,已跳过(不臆造)")
            _atomic_json(outp, ir)
        else:
            # 兜底:LLM 离线/超时 → 纯数据驱动 quick_build(仍是反造假规则)
            method = "数据驱动(LLM 离线兜底)"
            yield push("llm_extract", False, "LLM 引擎超时/离线 → 回退纯数据驱动构建(反造假规则)")
            yield sse({"type": "status", "text": "数据驱动构建本体中(读表 / 主外键推断 / 取值重叠验证)…"})
            try:
                proc = subprocess.Popen([sys.executable, os.path.join(HERE, "quick_build.py"), db, outp, gname],
                                        cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                for line in iter(proc.stdout.readline, ""):
                    line = line.strip()
                    if line: yield push("construct", True, line[:120])
                proc.wait(timeout=10)
            except Exception as e:
                yield push("construct", False, f"构建异常:{str(e)[:90]}")
            ir = _load_json(outp)
        if not isinstance(ir, dict) or not ir.get("objects"):
            yield sse({"type": "error", "error": "构建失败(无产物)"}); return
        g = ir_to_graph(key, ir)
        objs = ir.get("objects", []); rels = ir.get("relations", [])
        nev = sum(1 for o in objs if o.get("kind") == "event")
        ver = sum(1 for l in rels if l.get("status") == "verified"); cand = len(rels) - ver
        yield push("verify", True, f"关系反造假裁决 · verified {ver} 条 · candidate {cand} 条 · 事件对象 {nev} 个")
        yield sse({"type": "status", "text": "智能引擎生成本体说明与建模摘要…"})
        summ = _bounded(lambda: _build_summary(q, gname, ir), 35) or _build_rule_summary(gname, ir)
        yield push("narrate", True, f"生成本体说明 · {len(summ)} 字")
        stats = {"objects": len(g["nodes"]), "events": nev, "links": len(g["edges"]), "verified": ver, "candidate": cand}
        yield sse({"type": "done", "graph_key": key, "name": gname, "stats": stats, "summary": summ,
                   "method": method, "evidence": {"tables": ntab, "docs": ev["n_docs"], "refs": ev["refs"]},
                   "note": plan.get("note", ""), "elapsed": round(_t.time() - t0, 1)})
    from flask import Response, stream_with_context
    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── 数据连接浏览 + 数据可视化(对齐平台『配置数据源·连接原始数据库』与『数据看板/大屏』)──
# ── C7 外部库实连:连接器层(mysql/doris=pymysql, postgres=psycopg2;只读约束;凭据 0600 只写不回显)──
_CONN_SECRETS_F = os.path.join(WORK, "conn_secrets.json")

def _conn_secret(cid):
    try: return (_load_json(_CONN_SECRETS_F) or {}).get(cid) or {}
    except Exception: return {}

def _save_conn_secret(cid, user, password):
    with _WRITE_LOCK:
        d = _load_json(_CONN_SECRETS_F) or {}
        if user or password:
            d[cid] = {"user": (user or "")[:60], "password": (password or "")[:120]}
        else:
            d.pop(cid, None)
        _atomic_json(_CONN_SECRETS_F, d)
        try: os.chmod(_CONN_SECRETS_F, 0o600)
        except Exception: pass

def _parse_dsn(dsn):
    """jdbc:mysql://host:port/db 或 mysql://user:pass@host/db → 部件字典"""
    from urllib.parse import urlsplit
    s = re.sub(r"^jdbc:", "", (dsn or "").strip())
    u = urlsplit(s if "://" in s else "//" + s)
    return {"scheme": (u.scheme or "").lower(), "host": u.hostname or "", "port": u.port,
            "db": (u.path or "/").lstrip("/"), "user": u.username or "", "password": u.password or ""}

def _jsonable_rows(cols, rows):
    """外部驱动返回 Decimal/datetime/bytes → JSON 可序列化(str 兜底)"""
    import decimal, datetime as _dt
    def cv(v):
        if isinstance(v, decimal.Decimal): return float(v)
        if isinstance(v, (_dt.date, _dt.datetime, _dt.time)): return str(v)
        if isinstance(v, (bytes, bytearray)): return v.decode("utf-8", "replace")
        return v
    return [{c: cv(r[i]) for i, c in enumerate(cols)} for r in rows]

def _ext_kind(conn):
    k = (conn.get("kind") or "").lower()
    if k in ("external",):                       # 自定义 DSN:按 scheme 判型
        k = _parse_dsn(conn.get("dsn"))["scheme"] or "external"
    return {"postgresql": "postgres", "pg": "postgres"}.get(k, k)

def _ext_query(conn, sql, limit=500):
    """外部库真查询:只读放行 SELECT/WITH;驱动未装/不可达给明确报错(不静默)。→ {columns, rows}"""
    if not sql_is_readonly(sql): raise ValueError("仅允许只读 SELECT/WITH 查询")
    kind = _ext_kind(conn)
    u = _parse_dsn(conn.get("dsn"))
    sec = _conn_secret(conn.get("id"))
    user = sec.get("user") or u["user"] or "root"
    pwd = sec.get("password") or u["password"] or ""
    if kind in ("mysql", "doris"):
        try: import pymysql
        except ImportError: raise RuntimeError("未安装 MySQL 驱动:pip install pymysql 后重启服务")
        con = pymysql.connect(host=u["host"], port=int(u["port"] or (9030 if kind == "doris" else 3306)),
                              user=user, password=pwd, database=u["db"] or None,
                              connect_timeout=6, read_timeout=20, charset="utf8mb4")
        try:
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description or []]
            return {"columns": cols, "rows": _jsonable_rows(cols, cur.fetchmany(limit))}
        finally:
            con.close()
    if kind == "postgres":
        try: import psycopg2
        except ImportError: raise RuntimeError("未安装 PostgreSQL 驱动:pip install psycopg2-binary 后重启服务")
        con = psycopg2.connect(host=u["host"], port=int(u["port"] or 5432), user=user,
                               password=pwd, dbname=u["db"] or "postgres", connect_timeout=6)
        try:
            con.set_session(readonly=True)       # 会话级只读,双保险
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description or []]
            return {"columns": cols, "rows": _jsonable_rows(cols, cur.fetchmany(limit))}
        finally:
            con.close()
    if kind == "hive":
        raise RuntimeError("Hive 实连需 pyhive/thrift 与内网通道,当前经 hive-client 命令行侧通;HTTP 取数暂未接")
    raise RuntimeError(f"不支持的连接类型: {kind}")

def _ext_tables(conn):
    """外部库表清单(表名/行数估计/列数),经 information_schema/pg_stat 一次取回"""
    kind = _ext_kind(conn)
    if kind in ("mysql", "doris"):
        d = _ext_query(conn, "SELECT t.table_name AS name, COALESCE(t.table_rows,0) AS rows_, "
                             "(SELECT COUNT(*) FROM information_schema.columns c "
                             " WHERE c.table_schema=t.table_schema AND c.table_name=t.table_name) AS cols_ "
                             "FROM information_schema.tables t WHERE t.table_schema=DATABASE() ORDER BY 1", limit=500)
        return [{"name": r["name"], "rows": int(r["rows_"] or 0), "cols": int(r["cols_"] or 0)} for r in d["rows"]]
    if kind == "postgres":
        d = _ext_query(conn, "SELECT relname AS name, n_live_tup AS rows_, "
                             "(SELECT COUNT(*) FROM information_schema.columns c WHERE c.table_name=s.relname) AS cols_ "
                             "FROM pg_stat_user_tables s ORDER BY 1", limit=500)
        return [{"name": r["name"], "rows": int(r["rows_"] or 0), "cols": int(r["cols_"] or 0)} for r in d["rows"]]
    raise RuntimeError(f"该类型不支持表清单: {kind}")

def _find_conn(src):
    return next((c for c in _load_conns() if c.get("id") == src), None)

def _resolve_src(src):
    """数据源 id → (sqlite 路径, 名称);外部库/离线返回 (None, 名称)"""
    if src == "demo": return DB, "示例主库"
    if src == "uploads": return UPLOAD_DB, "上传数据库"
    for c in _load_conns():
        if c.get("id") == src:
            if c.get("kind") == "sqlite" and c.get("path") and os.path.exists(c["path"]):
                return c["path"], c.get("name")
            return None, c.get("name")
    return None, src

@app.get("/api/conn/tables")
def conn_tables():
    """浏览某数据源的表清单(表名/行数/列数)"""
    src = request.args.get("src", "demo")
    db, nm = _resolve_src(src)
    if not db:
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 外部库:真连取表清单
            try:
                return jsonify({"source": nm, "live": True, "tables": _ext_tables(conn)})
            except Exception as e:
                return jsonify({"source": nm, "error": f"外部库连接失败:{str(e)[:140]}", "tables": []})
        return jsonify({"source": nm, "error": f"「{nm}」不可浏览(外部库离线,需内网与驱动)", "tables": []})
    out = []
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            try: n = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except Exception: n = 0
            try: cols = len(c.execute(f'PRAGMA table_info("{t}")').fetchall())
            except Exception: cols = 0
            out.append({"name": t, "rows": n, "cols": cols})
        c.close()
    except Exception as e:
        return jsonify({"source": nm, "error": str(e)[:120], "tables": []})
    return jsonify({"source": nm, "tables": out})

@app.get("/api/conn/preview")
def conn_preview():
    """预览某数据源某表前 100 行"""
    src = request.args.get("src", "demo"); table = request.args.get("table", "")
    if not re.match(r"^[A-Za-z0-9_]+$", table): return jsonify({"error": "非法表名"}), 400
    db, nm = _resolve_src(src)
    if not db:
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 外部库:真连预览
            try:
                return jsonify({"live": True, **_ext_query(conn, f"SELECT * FROM {table} LIMIT 100", limit=100)})
            except Exception as e:
                return jsonify({"error": f"外部库预览失败:{str(e)[:140]}"})
        return jsonify({"error": f"「{nm}」不可预览(外部库离线)"})
    try:
        return jsonify(q(f'SELECT * FROM "{table}" LIMIT 100', db=db))
    except Exception as e:
        return jsonify({"error": str(e)[:120]})

@app.post("/api/viz/run")
def viz_run():
    """可视化取数:在指定数据源上执行只读 SELECT/WITH,返回列与行(供前端 ECharts 出图)"""
    body = request.json or {}
    src = body.get("src", "demo"); sql = (body.get("sql") or "").strip()
    if not sql: return jsonify({"error": "请输入查询 SQL"}), 400
    if not sql_is_readonly(sql): return jsonify({"error": "仅允许只读 SELECT/WITH 查询"}), 400
    db, nm = _resolve_src(src)
    if not db:
        conn = _find_conn(src)
        if conn and conn.get("kind") not in ("sqlite", "api"):    # C7 外部库:真连取数
            try:
                return jsonify({"source": nm, "live": True, **_ext_query(conn, sql)})
            except Exception as e:
                return jsonify({"error": f"外部库取数失败:{str(e)[:160]}"})
        return jsonify({"error": f"「{nm}」不可取数(外部库离线,需内网与驱动)"})
    try:
        data = q(sql, db=db)
        return jsonify({"source": nm, **data})
    except Exception as e:
        return jsonify({"error": str(e)[:160]})

_VIZ_BOARDS_F = os.path.join(WORK, "viz_boards.json")
def _load_boards():
    v = _load_json(_VIZ_BOARDS_F)
    return v if isinstance(v, list) else []

@app.get("/api/viz/boards")
def viz_boards():
    return jsonify(_load_boards())

@app.post("/api/viz/save")
def viz_save():
    """保存/更新数据看板(一组图表卡片规格)"""
    body = request.json or {}
    name = (body.get("name") or "").strip()
    charts = body.get("charts") or []
    if not name: return jsonify({"error": "需要看板名称"}), 400
    if not isinstance(charts, list) or not charts: return jsonify({"error": "看板至少含一张图表"}), 400
    bid = body.get("id") or ("board_" + uuid.uuid4().hex[:6])
    board = {"id": bid, "name": name, "charts": charts[:24], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _WRITE_LOCK:
        boards = [b for b in _load_boards() if b.get("id") != bid]
        boards.insert(0, board); _atomic_json(_VIZ_BOARDS_F, boards[:50])
    return jsonify({"ok": True, "id": bid})

@app.post("/api/viz/delete")
def viz_delete():
    bid = (request.json or {}).get("id")
    with _WRITE_LOCK:
        _atomic_json(_VIZ_BOARDS_F, [b for b in _load_boards() if b.get("id") != bid])
    return jsonify({"ok": True})

@app.get("/api/skills")
def skills():
    out = []
    for d in sorted(glob.glob(os.path.join(PLATFORM, "web", "skills_seed", "*"))):
        sk = os.path.join(d, "SKILL.md")
        if os.path.isdir(d) and os.path.exists(sk):
            txt = open(sk).read()
            m = re.search(r"description:\s*(.+)", txt)
            out.append({"name": os.path.basename(d), "desc": (m.group(1) if m else "")[:140],
                        "runnable": os.path.exists(os.path.join(d, "run.sh"))})
    return jsonify(out)

@app.post("/api/skill/run")
def skill_run():
    body = request.json or {}
    name = body.get("name", ""); args = body.get("args", "")
    if not re.match(r"^[\w\-]+$", name): return jsonify({"error": "非法技能名"}), 400   # 防穿越:与同族端点一致
    d = os.path.join(PLATFORM, "web", "skills_seed", name)
    if not os.path.exists(os.path.join(d, "run.sh")): return jsonify({"error": "该技能无 run.sh"}), 400
    if re.search(r"[;&|`$]", args): return jsonify({"error": "参数含非法字符"}), 400
    jid = run_job(["bash", os.path.join(d, "run.sh")] + args.split(), cwd=PLATFORM, tag=f"skill:{name}",
                  env={"GOV_TOKEN": os.environ.get("GOV_TOKEN", "")})
    return jsonify({"job": jid})

@app.get("/api/jobs")
def jobs(): return jsonify(sorted(JOBS.values(), key=lambda j: -j["ts"])[:20])

@app.get("/api/job/<jid>")
def job(jid):
    j = JOBS.get(jid)
    if not j: return jsonify({"error": "no job"}), 404
    tail = ""
    if os.path.exists(j["log"]):
        tail = open(j["log"], errors="replace").read()[-4000:]
    return jsonify({**j, "tail": tail})

@app.get("/api/outputs")
def outputs():
    out = []
    for base, label in ((OUTPUTS, "outputs"),):
        if not os.path.isdir(base): continue
        for p in sorted(glob.glob(os.path.join(base, "**", "*"), recursive=True)):
            if os.path.isfile(p) and os.path.getsize(p) < 20_000_000:
                out.append({"group": label, "name": os.path.relpath(p, base), "path": p, "kb": round(os.path.getsize(p) / 1024, 1)})
    return jsonify(out[:400])

@app.get("/api/outputs/file")
def outputs_file():
    p = request.args.get("p", "")
    allowed = [OUTPUTS]
    rp = os.path.realpath(p)
    if not any(rp.startswith(os.path.realpath(a) + os.sep) for a in allowed): return "forbidden", 403
    return send_file(rp)

if __name__ == "__main__":
    print("Cosmo DataMind → http://127.0.0.1:8092")
    app.run(host=os.environ.get("DATAMIND_HOST", "127.0.0.1"),
            port=int(os.environ.get("DATAMIND_PORT", "8092")), debug=False)

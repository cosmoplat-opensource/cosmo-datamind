# -*- coding: utf-8 -*-
"""场景回归基准(DR-067):两种数据形态,端到端走真实代码路径,与标准答案比对。

构建体检 → 上传代码 → 构建(含交叉复核)→ 问数,全部经 HTTP 接口走生产代码;只把调用模型的四处
(意图解析、抽取、语义评审、生成说明)换成固定输出——抽取返回一份「录制」的提议,
其余返回不可用或固定文本。问数的 SQL 也由固定输出给出:一条会导致计数错误的,一条正确的。

此前修复过的缺陷(计数翻倍、口径误拦已验证关系、降级构建全库、上传代码未读、构建流中断)若再次出现,
对应检查项会失败。用法:pytest tests/scenarios,或 python3 scripts/scenario_bench.py 查看逐项结果。
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import concept_hints
from tests.scenarios import fixtures

SKILLS = ["ontology-semi-auto", "ontology-build-method", "ontology-cross-check"]


@dataclass
class Check:
    scenario: str
    id: str
    ok: bool
    detail: str = ""


@contextmanager
def _patched(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


def _server():
    import server
    return server


def _register(server, work, db, name):
    (work / "build_connections.json").write_text(json.dumps(
        [{"id": "conn_bench", "name": name, "kind": "sqlite", "path": str(db)}], ensure_ascii=False), encoding="utf-8")
    server._GRAPH_ROW_CACHE.clear()
    server._QA_CACHE.clear()
    return "conn_bench"


def _build(server, conn, proposal, tables=None):
    llm = (lambda *a, **k: copy.deepcopy(proposal)) if proposal is not None else (lambda *a, **k: None)
    body = {"q": "场景回归构建", "source": conn, "name": "场景回归", "skills": SKILLS}
    if tables:
        body["tables"] = tables
    try:
        with _patched(server, _llm_extract_ontology=llm):
            raw = server.app.test_client().post("/api/build/inquire", json=body).get_data(as_text=True)
    except Exception as exc:                    # 构建流中途抛异常:记为「构建没走完」,不让整个基准崩掉
        return {}, None, None, f"{type(exc).__name__}: {exc}"
    events = [json.loads(x[6:]) for x in raw.splitlines() if x.startswith("data: ")]
    steps = {e["step"]: e for e in events if e.get("type") == "step"}
    done = next((e for e in events if e.get("type") == "done"), None)
    error = next((e.get("error") for e in events if e.get("type") == "error"), None)
    ir = server.load_ir(done["graph_key"]) if done else None
    return steps, done, ir, error


def _ask(server, graph, sql):
    with _patched(server, agent_sql_plan=lambda *a, **k: {"analyses": [{"title": "基准", "sql": sql}]}):
        server._QA_CACHE.clear()
        return server.app.test_client().post("/api/chat", json={"q": "场景回归问数", "graphs": [graph],
                                                                "nocache": True}).get_json()


def _ask_with_repair(server, graph, first, fixed):
    """计划先给出会被拦下的 SQL;收到「修正要求」时给出修正后的 SQL(DR-069)。"""
    def plan(question, *a, **k):
        return {"analyses": [{"title": "基准", "sql": fixed if "[修正要求]" in question else first}]}
    with _patched(server, agent_sql_plan=plan):
        server._QA_CACHE.clear()
        return server.app.test_client().post("/api/chat", json={"q": "场景回归问数", "graphs": [graph],
                                                                "nocache": True}).get_json()


def _relation(ir, s, t):
    return next((r for r in ir["relations"] if r.get("source_concept") == s and r.get("target_concept") == t), None)


def _relation_checks(scenario, ir, expect):
    out = []
    for (s, t), (status, rule) in expect.items():
        r = _relation(ir, s, t)
        got = (r or {}).get("status")
        vr = ((r or {}).get("evidence") or {}).get("valid_rows") or {}
        ok = r is not None and got == status and (rule is None or vr.get("rule") == rule)
        out.append(Check(scenario, f"关系 {s}→{t} 为 {status}" + (f"({rule})" if rule else ""), ok,
                         f"实际 {got} {vr.get('rule') or ''} · {(r or {}).get('note', '')[:80]}"))
    return out


def _gate_blocked(resp, word):
    return any(st["step"] == "ontology_gate" and not st["ok"] and word in st["info"] for st in resp.get("steps", []))


def _rows(resp):
    res = resp.get("results") or []
    return res[0]["data"]["rows"] if res else None


def run_lakehouse(tmp):
    server = _server()
    work = Path(tmp) / "work_lake"
    work.mkdir(parents=True, exist_ok=True)
    fx = fixtures.lakehouse(str(Path(tmp) / "lakehouse.db"))
    (work / "uploads_code.zip").write_bytes(fx["code_zip"])
    checks, name = [], "多厂多层湖仓"
    with _patched(server, WORK=str(work), _BUILD_CONN_F=str(work / "build_connections.json"),
                  _build_intent=lambda *a, **k: {}, _build_summary=lambda *a, **k: "摘要",
                  _llm_semantic_review=lambda *a, **k: None, _match_qa_skill=lambda _q: None,
                  narrative_llm=lambda *a, **k: "ok"):
        conn = _register(server, work, fx["db"], "湖仓回归库")
        prep = server.app.test_client().post("/api/build/prep", json={"source": conn}).get_json()
        checks.append(Check(name, "体检识别为多厂多层湖仓", "lakehouse" in [k["id"] for k in prep.get("scenario", [])],
                            str([k["name"] for k in prep.get("scenario", [])])))
        fams = {f["core"]: f for f in concept_hints.collect(fx["db"])["families"]}
        wo = fams.get("work_order", {})
        checks.append(Check(name, "工单的多厂副本归为一个表族,代表表为明细层",
                            wo.get("representative") == "dwd_work_order" and len(wo.get("members", [])) == 3, str(wo)[:120]))
        steps, done, ir, error = _build(server, conn, fx["proposal"])
        checks.append(Check(name, "带代码包的构建走完", done is not None, error or ""))
        if not ir:
            return checks
        cc = steps.get("code_contracts", {}).get("info", "")
        checks.append(Check(name, "上传的代码包被读取并对上库表", "对上库表" in cc and "对上库表 0" not in cc, cc[:100]))
        wip = next((o for o in ir["objects"] if o["name"] == "过站"), {})    # 对象缺失记为检查失败,不中断基准
        labels = {a["col"]: a.get("cn") for a in wip.get("attrs", [])}
        checks.append(Check(name, "代码注释补全字段中文名", labels.get("zone_sn") == "线别ID", str(labels)[:120]))
        checks += _relation_checks(name, ir, fx["expect"])
        graph = done["graph_key"]
        naive = ("SELECT z.zone_name AS 线别, COUNT(*) AS n FROM dwd_pass_record p "
                 "JOIN dim_line_zone z ON z.zone_id = p.zone_sn GROUP BY 1 ORDER BY 1")
        scoped = naive.replace("ON z.zone_id = p.zone_sn", "ON z.zone_id = p.zone_sn AND z.factory_code = p.factory_code")
        direct = sum(r["n"] for r in server.q(naive, db=fx["db"])["rows"])
        checks.append(Check(name, "问题场景成立:不带工厂关联时计数翻倍", direct == 2 * sum(fx["truth_pass_by_line"].values()),
                            f"直接执行得 {direct}"))
        r1 = _ask(server, graph, naive)
        checks.append(Check(name, "问数:不带工厂的关联被口径校验拦下", _gate_blocked(r1, "factory_code"),
                            str([s["info"][:60] for s in r1.get("steps", [])][-2:])))
        r2 = _ask(server, graph, scoped)
        got = {r["线别"]: r["n"] for r in (_rows(r2) or [])}
        checks.append(Check(name, "问数:带工厂关联的答案与标准答案一致", got == fx["truth_pass_by_line"], str(got)[:120]))
        checks.append(Check(name, "问数在本体的来源库上执行", "湖仓回归库" in (r2.get("steps") or [{}])[0].get("info", ""),
                            (r2.get("steps") or [{}])[0].get("info", "")[:80]))
    return checks


def run_business(tmp):
    server = _server()
    work = Path(tmp) / "work_biz"
    work.mkdir(parents=True, exist_ok=True)
    fx = fixtures.business(str(Path(tmp) / "business.db"))
    checks, name = [], "元数据驱动业务库"
    with _patched(server, WORK=str(work), _BUILD_CONN_F=str(work / "build_connections.json"),
                  _build_intent=lambda *a, **k: {}, _build_summary=lambda *a, **k: "摘要",
                  _llm_semantic_review=lambda *a, **k: None, _match_qa_skill=lambda _q: None,
                  narrative_llm=lambda *a, **k: "ok"):
        conn = _register(server, work, fx["db"], "业务回归库")
        prep = server.app.test_client().post("/api/build/prep", json={"source": conn}).get_json()
        checks.append(Check(name, "体检识别为元数据驱动业务库",
                            "metadata_driven" in [k["id"] for k in prep.get("scenario", [])],
                            str([k["name"] for k in prep.get("scenario", [])])))
        split = next((s for s in concept_hints.collect(fx["db"])["category_splits"] if s["table"] == "inst_device"), {})
        got = {c["name"]: c["rows"] for c in split.get("categories", [])}
        checks.append(Check(name, "设备表按类别拆出三种器具", got == fx["truth_devices_by_type"], str(got)))
        steps, done, ir, error = _build(server, conn, fx["proposal"])
        checks.append(Check(name, "构建走完", done is not None, error or ""))
        if not ir:
            return checks
        checks += _relation_checks(name, ir, fx["expect"])
        graph = done["graph_key"]
        naive = ("SELECT t.dev_type_name AS 类型, COUNT(*) AS n FROM inst_device d "
                 "JOIN meta_device_type t ON t.dev_type_code = d.mete_dev_type_code GROUP BY 1")
        live = naive.replace("GROUP BY 1", "AND t.is_del = 0 GROUP BY 1")
        direct = {r["类型"]: r["n"] for r in server.q(naive, db=fx["db"])["rows"]}
        checks.append(Check(name, "问题场景成立:不排除已删除行时计数翻倍",
                            direct.get("电能表") == 2 * fx["truth_devices_by_type"]["电能表"], str(direct)))
        r1 = _ask(server, graph, naive)
        checks.append(Check(name, "问数:未排除已删除行的关联被口径校验拦下", _gate_blocked(r1, "is_del"),
                            str([s["info"][:60] for s in r1.get("steps", [])][-2:])))
        r2 = _ask(server, graph, live)
        got = {r["类型"]: r["n"] for r in (_rows(r2) or [])}
        checks.append(Check(name, "问数:排除已删除行后的答案与标准答案一致", got == fx["truth_devices_by_type"], str(got)))
        r2b = _ask_with_repair(server, graph, naive, live)
        got = {r["类型"]: r["n"] for r in (_rows(r2b) or [])}
        checks.append(Check(name, "问数:被拦下的 SQL 按拦截原因修正一次后答案正确",
                            got == fx["truth_devices_by_type"] and any("修正一次" in s["info"] for s in r2b.get("steps", [])),
                            str(got)))
        # 删除标记写成 'N'/'Y':条件按复核观察到的取值生成,固定写 = 0 的 SQL 会被拦下
        naive_e = ("SELECT e.energy_name AS 能源, COUNT(*) AS n FROM measure_relation m "
                   "JOIN meta_energy_type e ON e.energy_type = m.energy_type GROUP BY 1")
        zero_e = naive_e.replace("GROUP BY 1", "AND e.del_flag = 0 GROUP BY 1")
        live_e = naive_e.replace("GROUP BY 1", "AND e.del_flag = 'N' GROUP BY 1")
        r3 = _ask(server, graph, zero_e)
        checks.append(Check(name, "问数:删除标记为 'N'/'Y' 时,按 = 0 过滤的关联被口径校验拦下", _gate_blocked(r3, "del_flag = 'N'"),
                            str([s["info"][:80] for s in r3.get("steps", [])][-2:])))
        r4 = _ask(server, graph, live_e)
        got = {r["能源"]: r["n"] for r in (_rows(r4) or [])}
        checks.append(Check(name, "问数:按 'N' 排除已删除行后的答案与标准答案一致", got == fx["truth_measures_by_energy"], str(got)))
        steps, done, ir, error = _build(server, conn, None, tables=["inst_device", "meta_device_type"])
        tabs = {t for o in (ir or {}).get("objects", []) for t in (o.get("tables") or [o.get("table")]) if t}
        checks.append(Check(name, "模型不可用时的降级构建只建本轮范围", bool(tabs) and tabs <= {"inst_device", "meta_device_type"},
                            str(sorted(tabs))))
    return checks


def run_all(tmp):
    return run_lakehouse(tmp) + run_business(tmp)

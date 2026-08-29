#!/usr/bin/env python3
"""从严格使用者视角执行 10 轮变序回归。

该脚本复用一次完整构建得到的产物，为它建立临时副本，再以不同顺序验证：
输入追溯、图结构、CQ、会话隔离、人审编辑、审计、候选关系边界、
不存在概念、同步深度问数和 SSE 锚定。不会重复发起 10 次耗时的 LLM 全量构建。

对象和关系总数从被测产物读取，不把旧版 108 节点/261 关系写死。若产物包含动作
节点，还会核对登记来源、对象绑定状态和“只形成动作记录、不写回业务系统”声明。

测试仅写临时 built_strict10_* 图谱、其编辑日志与测试会话，结束时精确清理。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import requests  # type: ignore[import-untyped]


CQ_SUPPLIER = "哪些供应商的采购订单交付延期且质量评分低？"
CQ_PRODUCTION = "哪些生产订单的不合格率高于5%，涉及哪些产品与产线？"
CQ_METRIC = "营业收入、毛利率和计划达成率分别由哪些表与字段支撑？"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("DATAMIND_URL", "http://127.0.0.1:8093"))
    ap.add_argument("--workdir", required=True, help="被测服务的 workdir 绝对路径")
    ap.add_argument("--graph", required=True, help="已完成的 108 表构建产物")
    ap.add_argument("--report", required=True, help="JSON 测试报告路径")
    args = ap.parse_args()

    base = args.url.rstrip("/")
    work = Path(args.workdir).resolve()
    source_path = (work / f"{args.graph}.json").resolve()
    if source_path.parent != work or not source_path.is_file():
        print(f"找不到源产物: {source_path}", file=sys.stderr)
        return 2
    with open(source_path, encoding="utf-8") as f:
        source_ir = json.load(f)
    source_objects = source_ir.get("objects") or []
    source_relations = source_ir.get("relations") or []
    source_actions = [x for x in source_objects if x.get("kind") == "action"]
    source_table_objects = [
        x for x in source_objects
        if x.get("kind") != "action" and (x.get("table") or x.get("tables"))
    ]
    source_statuses: dict[str, int] = {}
    for relation in source_relations:
        status = relation.get("status") or "unknown"
        source_statuses[status] = source_statuses.get(status, 0) + 1

    temp_graph = "built_strict10_" + uuid.uuid4().hex[:8]
    temp_path = work / f"{temp_graph}.json"
    edits_path = work / f"edits_{temp_graph}.json"
    shutil.copyfile(source_path, temp_path)

    session = requests.Session()
    rounds: list[dict] = []
    created_chats: list[str] = []
    state: dict = {}

    def api(method: str, path: str, *, expected: int = 200, timeout: int = 60, **kwargs):
        r = session.request(method, base + path, timeout=timeout, **kwargs)
        if r.status_code != expected:
            raise AssertionError(f"{method} {path}: HTTP {r.status_code}, {r.text[:240]}")
        try:
            return r.json()
        except ValueError as e:
            raise AssertionError(f"{method} {path}: 非 JSON 响应 {r.text[:160]}") from e

    def require(cond: bool, message: str):
        if not cond:
            raise AssertionError(message)

    def run_round(number: int, name: str, fn):
        started = time.monotonic()
        try:
            detail = fn() or {}
            rounds.append({"round": number, "name": name, "status": "passed",
                           "elapsed_s": round(time.monotonic() - started, 3), "detail": detail})
            print(f"✓ 第{number}轮 {name}")
        except Exception as e:
            rounds.append({"round": number, "name": name, "status": "failed",
                           "elapsed_s": round(time.monotonic() - started, 3), "error": str(e)[:600]})
            print(f"✗ 第{number}轮 {name}: {e}")

    def quality(cqs):
        return api("POST", "/api/build/quality", json={"graph": temp_graph, "cqs": cqs})

    def round1():
        with open(temp_path, encoding="utf-8") as f:
            ir = json.load(f)
        m = ir.get("build_manifest") or {}
        scenario = ir.get("scenario") or {}
        require(len(source_table_objects) == 108, f"表对象数不是 108: {len(source_table_objects)}")
        require(scenario.get("object_count") == len(source_objects), "场景对象数与实际产物不一致")
        require(scenario.get("relation_count") == len(source_relations), "场景关系数与实际产物不一致")
        require(not (scenario.get("query_errors") or []), "构建期间存在未解决的数据查询错误")
        files = m.get("evidence", {}).get("files") or []
        if m:
            require(m.get("evidence", {}).get("tables") == 108, "构建清单未记录 108 张表")
            require("ontology-semi-auto" in (m.get("skills") or []), "未记录构建技能")
        excel_files = [x for x in files
                       if str(x.get("name", "")).lower().endswith((".xlsx", ".xls"))]
        require(all(x.get("consumed_as_text") for x in excel_files), "Excel 证据已登记但未被解析")
        return {"tables": 108, "manifest_present": bool(m),
                "excel_files": [x.get("name") for x in excel_files],
                "skills": m.get("skills") or [], "cqs": len(m.get("cqs") or []),
                "query_errors": len(scenario.get("query_errors") or [])}

    def round2():
        g = api("GET", f"/api/graph/{temp_graph}")
        statuses = {}
        for e in g.get("edges") or []:
            statuses[e.get("status")] = statuses.get(e.get("status"), 0) + 1
        require(len(g.get("nodes") or []) == len(source_objects), "图谱对象数与源产物不一致")
        require(len(g.get("edges") or []) == len(source_relations), "图谱关系数与源产物不一致")
        require(statuses == source_statuses, f"关系证据分层异常: {statuses} != {source_statuses}")
        require(all(e.get("grounding_status") == "unmapped" for e in g["edges"]),
                "基础本体映射状态与产物不一致")
        action_nodes = [x for x in g["nodes"] if x.get("kind") == "action"]
        require(len(action_nodes) == len(source_actions), "动作节点数与源产物不一致")
        if action_nodes:
            require(all(x.get("action_spec", {}).get("execution_mode") == "decision_capture"
                        for x in action_nodes), "动作执行模式存在不实声明")
            require(all(x.get("action_spec", {}).get("real_writeback") is False
                        for x in action_nodes), "动作节点不得伪称已接通真实业务写回")
            bindings = [x for x in g["edges"] if x.get("verb") == "作用于"]
            require(all(x.get("status") == "candidate" and x.get("evidence_status") == "configured"
                        for x in bindings), "动作对象绑定未保持‘已配置、待语义确认’边界")
        return {"objects": len(source_objects), "table_objects": len(source_table_objects),
                "actions": len(action_nodes), "relations": len(source_relations),
                "statuses": statuses, "unmapped_grounding": len(source_relations)}

    def round3():
        q = quality([CQ_SUPPLIER, CQ_PRODUCTION, CQ_METRIC])
        counts = q["cq"]["counts"]
        require(counts == {"answerable": 0, "partial": 0, "unanswerable": 3},
                f"零编辑 CQ 基线不符预期: {counts}")
        require(q["cq"]["coverage"] == 0.0, "不可答 CQ 不得计入覆盖率")
        state["baseline_cq"] = counts
        return {"counts": counts, "coverage": q["cq"]["coverage"]}

    def round4():
        c = api("POST", "/api/ont/chats/new", json={"graph": temp_graph})
        created_chats.append(c["id"])
        state["chat"] = c["id"]
        rows = api("GET", f"/api/ont/chats?graph={temp_graph}")
        require(any(x["id"] == c["id"] and x["graph"] == temp_graph for x in rows), "会话未按图谱归属")
        bad = api("POST", "/api/ont/chat", expected=409,
                  json={"id": c["id"], "message": "这个会话不应跨图谱", "graph": "demo"})
        require("不一致" in bad.get("error", ""), "跨图谱拒绝原因不清晰")
        return {"chat_id": c["id"], "cross_graph_http": 409}

    def round5():
        op = {"op": "set_alias", "target": "obj:dim_supplier", "params": {"aliases": "供应商"},
              "reason": "业务问句使用供应商，本体对象名为 dim_supplier"}
        a = api("POST", "/api/ont/apply", json={"graph": temp_graph, "op": op,
                                                  "reviewer": "10轮严格回归", "source": "chat"})
        require(a.get("ok") and a.get("ops") == 1, "人审别名未生效")
        audit = api("GET", f"/api/ont/audit/{temp_graph}")
        require(audit.get("total") == 1 and audit.get("unsigned") == 0 and audit.get("no_reason") == 0,
                f"审计三要素不完整: {audit}")
        require(audit.get("by_source", {}).get("chat") == 1, "审计未标记对话来源")
        return {"ops": a["ops"], "reviewer": "10轮严格回归", "source": "chat"}

    def round6():
        q = quality([CQ_SUPPLIER])
        counts = q["cq"]["counts"]
        require(counts == {"answerable": 0, "partial": 1, "unanswerable": 0},
                f"单一别名不应把多概念问题误判为可答: {counts}")
        require("仅锚定到一个对象" in q["cq"]["items"][0]["reason"], "部分支撑原因不清晰")
        return {"counts": counts, "verdict": q["cq"]["items"][0]["verdict"]}

    def round7():
        cq = {"q": "供应商与采购订单能否关联？", "expect": ["dim_supplier", "fact_purchase_order"]}
        q = quality([cq])
        item = q["cq"]["items"][0]
        require(item["verdict"] == "answerable", f"已验证关系未判为可达: {item}")
        require(item.get("path") in ([['dim_supplier', 'fact_purchase_order']],
                                     [['fact_purchase_order', 'dim_supplier']]), "返回路径不正确")
        return {"verdict": item["verdict"], "path": item["path"]}

    def round8():
        # 这对对象之间只有 candidate 路径；不能选存在另一条 verified 绕行路径的对象对。
        candidate = {"q": "这两个声明对象能否关联？",
                     "expect": ["dim_metric_catalog", "agg_metric_monthly"]}
        missing = {"q": "量子碳足迹预测引擎是否存在？", "expect": ["quantum_carbon_predictor"]}
        q = quality([candidate, missing])
        verdicts = [x["verdict"] for x in q["cq"]["items"]]
        require(verdicts == ["partial", "unanswerable"], f"候选关系/不存在概念边界错误: {verdicts}")
        require(q["cq"]["coverage"] == 0.0, "候选路径不得计入可答覆盖")
        return {"verdicts": verdicts, "coverage": q["cq"]["coverage"]}

    def round9():
        d = api("POST", "/api/chat", timeout=180,
                json={"q": "客户维度表有多少条记录？", "graphs": [temp_graph],
                      "tables": ["dim_customer"], "use_cache": False})
        require(d.get("anchor", {}).get("ontology", {}).get("keys") == [temp_graph], "同步问数未使用所选本体")
        require(d.get("anchor", {}).get("scoped") is True, "显式选表未收窄问数范围")
        results = d.get("results") or []
        rows = results[0].get("data", {}).get("rows", []) if results else []
        scalar_values = list(rows[0].values()) if len(rows) == 1 else []
        require(25 in scalar_values, f"客户计数结果不是 25: {rows!r}")
        require("dim_customer" in results[0]["sql"].lower(), "SQL 未查询显式选择的表")
        return {"graph": temp_graph, "rows": rows, "sql": results[0]["sql"]}

    def round10():
        r = session.post(base + "/api/chat/stream", timeout=(10, 80), stream=True,
                         json={"q": "客户维度表有多少条记录？", "graphs": [temp_graph],
                               "tables": ["dim_customer"], "nocache": 1})
        require(r.status_code == 200, f"SSE 问数 HTTP {r.status_code}")
        anchor = None
        try:
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if isinstance(event.get("anchor"), dict):
                    anchor = event["anchor"]
                    break
        finally:
            r.close()
        require(anchor and anchor.get("ontology", {}).get("keys") == [temp_graph], "SSE 未推送所选本体锚定")
        undone = api("POST", "/api/ont/undo", json={"graph": temp_graph})
        require(undone.get("ok") and undone.get("ops") == 0, "未精确撤销本轮别名")
        q = quality([CQ_SUPPLIER])
        require(q["cq"]["counts"] == {"answerable": 0, "partial": 0, "unanswerable": 1},
                "撤销后 CQ 未回到基线")
        audit = api("GET", f"/api/ont/audit/{temp_graph}")
        require(audit.get("total") == 0, "撤销后当前生效变更应为 0")
        return {"sse_anchor_graph": temp_graph, "undo_ops": 0,
                "cq_after_undo": q["cq"]["counts"], "audit_total": audit["total"]}

    try:
        for number, name, fn in [
            (1, "构建输入与 Excel/技能/CQ 追溯", round1),
            (2, "对象、关系、动作与证据分层", round2),
            (3, "零编辑 CQ 基线", round3),
            (4, "会话图谱隔离与跨图拒绝", round4),
            (5, "人审别名与变更审计", round5),
            (6, "别名后多概念 CQ 从严判定", round6),
            (7, "显式期望对象与 verified 路径", round7),
            (8, "candidate 路径与不存在概念边界", round8),
            (9, "同步深度问数按所选本体/表执行", round9),
            (10, "SSE 锚定、撤销与 CQ 回归", round10),
        ]:
            run_round(number, name, fn)
    finally:
        for cid in created_chats:
            try:
                session.post(base + "/api/ont/chats/delete", json={"id": cid}, timeout=20)
            except requests.RequestException:
                pass
        for path in (edits_path, temp_path):
            try:
                if path.is_file() and path.parent == work:
                    path.unlink()
            except OSError:
                pass

    passed = sum(1 for x in rounds if x["status"] == "passed")
    report = {
        "title": "本体半自动构建与深度问数严格 10 轮变序回归",
        "service": base,
        "source_graph": args.graph,
        "method_note": "复用一次完整 108 表构建，在临时副本上变更操作顺序；如有 Excel/动作投影则按产物事实校验；含一次真实同步问数与一次 SSE 锚定。",
        "passed": passed,
        "failed": len(rounds) - passed,
        "cleanup": {"temporary_graph_removed": not temp_path.exists(),
                    "temporary_edits_removed": not edits_path.exists(),
                    "test_chats_requested_for_deletion": len(created_chats)},
        "rounds": rounds,
    }
    out = Path(args.report).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果: {passed}/10 通过；报告: {out}")
    return 0 if passed == 10 else 1


if __name__ == "__main__":
    raise SystemExit(main())

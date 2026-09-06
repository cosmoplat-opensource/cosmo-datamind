#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标反解 → 归一 → 执行核验 → 并入 IR 的编排 —— DR-054。

纯确定性、不调模型;只吃数据源路径、IR 与已经读进内存的文本。server 在构建收尾时调用,
也可对既有图谱单独重跑(``/api/metric/adjudicate``)。
"""
from __future__ import annotations

import metric_contract as MC
import metric_mining as MM


def run(db_path, ir, tab_cols=None, sql_texts=None, glossary_rows=None, qa_skills=None, gold_items=None,
        max_candidates=200):
    """返回 {"metric_layers", "report"};不修改入参 IR。"""
    mined = MM.collect(tab_cols, sql_texts, glossary_rows, qa_skills, gold_items)
    contracts, rejected = [], []
    for raw in mined["candidates"][:max_candidates]:
        refs = raw.pop("references", []) or []
        try:
            c = MC.normalize(raw, ir)
        except ValueError as exc:
            rejected.append({"name": raw.get("name"), "reason": str(exc)})
            continue
        if MC.is_contract(c) and c.get("table"):
            c = MC.adjudicate(db_path, c, ir, refs)
        else:
            c["evidence"] = {"compiled_sql": "", "executed": False, "match": None,
                             "error": "未绑定表或未声明聚合,不可编译"}
        contracts.append(c)
    layers, stat = MC.upsert_layers(ir, contracts)
    verified = sum(1 for c in contracts if c.get("status") == "verified")
    executable = sum(1 for c in contracts if (c.get("evidence") or {}).get("executed"))
    report = {
        "sources": mined["sources"],
        "candidates": len(mined["candidates"]),
        "contracts": len(contracts),
        "verified": verified,
        "executable_unverified": executable - verified,
        "unbound": sum(1 for c in contracts if not MC.is_contract(c) or not c.get("table")),
        "skipped": mined["skipped"][:50],
        "rejected": rejected[:50],
        "merge": stat,
        "note": ("verified 只来自「编译执行成功且与参照一致」;可执行但无参照的指标仍为 candidate,"
                 "人工确认口径后置 certified"),
    }
    return {"metric_layers": layers, "report": report}


def readjudicate(db_path, ir):
    """对 IR 里已有的契约重新执行核验(不新增候选);证据里保存的参照 SQL 作为参照复用。"""
    contracts = []
    for arr in ((ir.get("metric_layers") or {}).values()):
        for m in (arr or []):
            if not MC.is_contract(m):
                continue
            try:
                c = MC.normalize(m, ir)
            except ValueError:
                continue
            refs = []
            ref = ((m.get("evidence") or {}).get("reference") or {})
            for p in (m.get("provenance") or []):
                if p.get("kind") == "sql" and p.get("snippet"):
                    refs.append({"kind": "sql", "sql": p["snippet"], "source": p.get("source", ""),
                                 "dimensions": c.get("dimensions"), "grain": (c.get("time") or {}).get("grain", [None])[0]})
            if ref.get("kind") == "value" and "value" in ref:
                refs.append(ref)
            contracts.append(MC.adjudicate(db_path, c, ir, refs))
    layers, stat = MC.upsert_layers(ir, contracts)
    return {"metric_layers": layers,
            "report": {"contracts": len(contracts),
                       "verified": sum(1 for c in contracts if c["status"] == "verified"), "merge": stat}}

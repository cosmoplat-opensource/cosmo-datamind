#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""问数锚定阶段的离线对照(DR-055 后续):现行关键词锚定 vs 概念画像检索。

概念画像只改变「问句 → 本体对象/表」这一步,所以不需要 LLM 就能单独度量:
参考基准每题给出 gold_tables(正确答案必须用到的表),比较两种锚定各自召回了多少、
带进了多少无关表。结论只针对锚定这一步,不代表端到端问数正确率。

用法:DATAMIND_DB=examples/demo_metrics.db python3 scripts/eval_anchoring.py [--graph demo] [--k 5]
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _prf(picked, gold):
    p, g = {t.lower() for t in picked}, {t.lower() for t in gold}
    hit = len(p & g)
    return {"recall": hit / len(g) if g else 1.0, "precision": hit / len(p) if p else 0.0,
            "picked": len(p), "all_gold": g <= p}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="demo")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--out")
    a = ap.parse_args()
    import server
    import concept_profile as CP
    ir, _, note = server._anchor_ir([a.graph])
    profiles = CP.build(ir)
    items = json.load(open(os.path.join(ROOT, "benchmark", "qa_set.json"), encoding="utf-8"))["items"]
    rows = []
    for it in items:
        trace = {}
        server.build_context(it["q"], trace=trace, graph_keys=[a.graph])
        kw = [o["table"] for o in trace.get("objects", []) if o.get("table")]
        hits = CP.search(profiles, it["q"], limit=a.k)
        pr = [h["table"] for h in hits if h.get("table")]
        rows.append({"id": it["id"], "q": it["q"], "gold": it["gold_tables"],
                     "keyword": {**_prf(kw, it["gold_tables"]), "tables": kw},
                     "profile": {**_prf(pr, it["gold_tables"]), "tables": pr}})
    def agg(key):
        n = len(rows)
        return {"mean_recall": round(sum(r[key]["recall"] for r in rows) / n, 3),
                "mean_precision": round(sum(r[key]["precision"] for r in rows) / n, 3),
                "all_gold_hit": sum(r[key]["all_gold"] for r in rows),
                "mean_tables": round(sum(r[key]["picked"] for r in rows) / n, 1)}
    report = {"graph": a.graph, "k": a.k, "n": len(rows), "note": note or "",
              "keyword": agg("keyword"), "profile": agg("profile"), "rows": rows,
              "caveat": "仅度量锚定阶段;8 题参考基准规模很小,只作方向性依据"}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Protocol v2 statistics on the re-scored units (rescore_v2.py): strict and column-tolerant execution accuracy.

For every contrast: three repeats averaged within question, database-cluster bootstrap (10,000 resamples,
seed 20261013) for the 95% interval and a two-sided bootstrap p-value, Holm correction over all contrasts of
one metric and pool, per-run exact McNemar tests, and breakdowns by database and difficulty / task type.
Writes runs/v2/rescore/statistics_v3.json and runs/v2/rescore/统计_v3.md.  No model calls.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import aggregate_v2 as ag
from complete_v2_statistics import metadata_for

R = ag.V2 / "rescore"
METRICS = {"strict": 0, "tolerant": 1}
CONTRASTS = ([("native", ("dm-w", "dm"), ("evo-w", "react")),
              ("construction_d", ("dm-d", "dm"), ("evo-d", "dm")),
              ("construction_w", ("dm-w", "dm"), ("evo-w", "dm")),
              ("construction_d_react", ("dm-d", "react"), ("evo-d", "react")),
              ("construction_w_react", ("dm-w", "react"), ("evo-w", "react")),
              ("ontology_d", ("dm-d", "dm"), ("none", "dm")),
              ("ontology_w", ("dm-w", "dm"), ("none", "dm")),
              ("evo_ontology_d", ("evo-d", "react"), ("none", "react")),
              ("evo_ontology_w", ("evo-w", "react"), ("none", "react"))]
             + [("consumer_" + o, (o, "dm"), (o, "react")) for o in ("none", "evo-d", "evo-w", "dm-d", "dm-w")])


def load_units(metric):
    """units[(dir, O, Q, S)][rep] = {qid: score}. Cells reused by design (plan.json reuse_of) are read from the reused unit."""
    plan = [j for j in json.loads((ag.V2 / "plan.json").read_text()) if j["phase"] == "qa"]
    out = {}
    by_out = {j["out"]: j["id"].split("__", 1)[1] for j in plan}
    for j in plan:
        _, d, O, Q, tail = j["id"].split("__")
        S, rep = tail.rsplit("_r", 1)
        unit = by_out.get(j.get("reuse_of"), j["id"].split("__", 1)[1]) if j.get("reuse_of") else j["id"].split("__", 1)[1]
        f = R / f"{unit}.json"
        if not f.exists():
            continue
        res = json.loads(f.read_text())
        out.setdefault((d, O, Q, S), {})[int(rep)] = {k: int(v[METRICS[metric]]) for k, v in res.items()}
    return out


def pooled(units, dirs, O, Q, S):
    out = {}
    for rep in (1, 2, 3):
        parts = [units.get((d, O, Q, S), {}).get(rep) for d in dirs]
        if all(p is not None for p in parts):
            merged = {}
            for p in parts:
                merged.update(p)
            out[rep] = merged
    return out if len(out) == 3 else None


def stats(left, right, meta, samples=10000):
    ids = sorted(set(left[1]))
    for reps in (left, right):
        for r in (1, 2, 3):
            if set(reps[r]) != set(ids):
                raise ValueError("paired question sets differ")
    groups = defaultdict(list)
    for q in ids:
        groups[meta[q]["db_id"]].append(q)
    mean = lambda reps, qs: sum(sum(reps[r][q] for r in (1, 2, 3)) / 3 for q in qs)
    def row(qs, label):
        a, b = mean(left, qs), mean(right, qs)
        return {"group": label, "n": len(qs), "left": 100 * a / len(qs), "right": 100 * b / len(qs), "diff": 100 * (a - b) / len(qs)}
    tot = np.array([[len(qs), mean(left, qs), mean(right, qs)] for _, qs in sorted(groups.items())])
    rng = np.random.default_rng(20261013)
    ch = tot[rng.integers(0, len(tot), (samples, len(tot)))].sum(axis=1)
    diffs = 100 * (ch[:, 1] - ch[:, 2]) / ch[:, 0]
    p_boot = min(1.0, 2 * min((diffs <= 0).mean(), (diffs >= 0).mean()))
    kinds = sorted({meta[q]["difficulty"] for q in ids})
    return {**row(ids, "all"), "databases": len(groups), "ci95": np.quantile(diffs, [.025, .975]).tolist(), "p_boot": p_boot,
            "mcnemar": [ag.mcnemar({k: v for k, v in left[r].items()}, {k: v for k, v in right[r].items()})[0] for r in (1, 2, 3)],
            "by_database": [row(qs, db) for db, qs in sorted(groups.items())],
            "by_kind": [row([q for q in ids if meta[q]["difficulty"] == k], k) for k in kinds]}


def holm(pvals):
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    adj, running = [0.0] * len(pvals), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvals) - rank) * pvals[i]))
        adj[i] = running
    return adj


def main():
    results, lines = {}, ["# 协议 v2 重评分统计（严格与列容忍执行准确率）", "",
                          "三次重复先在题内平均；按数据库整组重采样 10,000 次得 95% 区间与双侧 p；Holm 校正在同一口径、同一题池的全部比较内进行。",
                          "工业库只有两个合成库，区间与 p 只作描述，不能代表跨企业泛化。", ""]
    for metric in METRICS:
        units = load_units(metric)
        for pool, dirs in ag.POOLS.items():
            meta = metadata_for(dirs)
            keys, rows = [], []
            for setting in ("full", "nofk", "bare"):
                for label, a, b in CONTRASTS:
                    left, right = pooled(units, dirs, *a, setting), pooled(units, dirs, *b, setting)
                    if not left or not right:
                        continue
                    r = stats(left, right, meta)
                    r.update(left_condition=a, right_condition=b)
                    keys.append(f"{metric}|{pool}|{setting}|{label}"); rows.append(r)
            for k, r, ph in zip(keys, rows, holm([r["p_boot"] for r in rows])):
                r["p_holm"] = ph
                results[k] = r
    for metric in METRICS:
        lines += [f"## {metric}", "", "| 题池 / 模式 / 比较 | 题数/库 | 左 | 右 | 差 [按库95%区间] | p(Holm) | McNemar 3 次 |", "|---|---|---:|---:|---|---:|---|"]
        for k, r in results.items():
            if not k.startswith(metric + "|"):
                continue
            lo, hi = r["ci95"]
            lines.append(f"| {k.split('|',1)[1].replace('|',' / ')} | {r['n']}/{r['databases']} | {r['left']:.1f} | {r['right']:.1f} | {r['diff']:+.1f} [{lo:+.1f}, {hi:+.1f}] | {r['p_holm']:.3f} | {', '.join('%.3f' % p for p in r['mcnemar'])} |")
        lines.append("")
    for k, r in results.items():
        if k.endswith("|native") and "|full|" in k:
            lines += [f"## {k}：按题型/难度", "", "| 分组 | 题数 | 左 | 右 | 差 |", "|---|---:|---:|---:|---:|"]
            lines += [f"| {x['group']} | {x['n']} | {x['left']:.1f} | {x['right']:.1f} | {x['diff']:+.1f} |" for x in r["by_kind"]]
            lines.append("")
    (R / "statistics_v3.json").write_text(json.dumps(results, ensure_ascii=False, indent=1))
    (R / "统计_v3.md").write_text("\n".join(lines) + "\n")
    print(len(results), "contrasts")


if __name__ == "__main__":
    main()

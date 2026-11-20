#!/usr/bin/env python3
"""Flat term-dictionary control (2026-11-20, review). Condition evo_flat (runs/v5/qa/MG_<fold>/evo_flat_r<rep>; store
from make_flat_dictionary.py) against none, evo and doc (runs/v4) on the 400 MG test questions. Column-tolerant EX;
95% bootstrap over questions within source database. Output: runs/v5/flat_dictionary_stats_20261120.json"""
import json
import analysis_review_20261120 as A

meta = A.mg_meta()
d = {"none": A.per_question(A.V4, "MG", "none"), "flat": A.per_question(A.V5, "MG", "evo_flat"),
     "evo": A.per_question(A.V4, "MG", "evo"), "doc": A.per_question(A.V4, "MG", "doc")}
keys = sorted(set.intersection(*[set(v) for v in d.values()]))
db = {k: meta[k]["source_db"] for k in keys}
res = {"n": len(keys), "acc": {}, "acc_ci": {}, "acc_by_fold": {}}
for c, v in d.items():
    res["acc"][c] = round(100 * A.mean(v, keys), 1)
    res["acc_ci"][c] = A.ci(lambda ks, v=v: A.mean(v, ks), keys, db)
    res["acc_by_fold"][c] = {f: round(100 * A.mean(v, [k for k in keys if k.startswith(f)]), 1) for f in ("fwd", "rev")}
for name, (x, y) in {"flat-none": ("flat", "none"), "evo-flat": ("evo", "flat"), "doc-flat": ("doc", "flat")}.items():
    f = lambda ks, x=x, y=y: A.mean(d[x], ks) - A.mean(d[y], ks)                     # noqa: E731
    res[name] = {"diff": round(100 * f(keys), 1), "ci": A.ci(f, keys, db)}
res["share_of_evo_gain_pct"] = round(100 * (A.mean(d["flat"], keys) - A.mean(d["none"], keys))
                                     / (A.mean(d["evo"], keys) - A.mean(d["none"], keys)))
(A.V5 / "flat_dictionary_stats_20261120.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res))

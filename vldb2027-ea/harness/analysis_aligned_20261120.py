#!/usr/bin/env python3
"""Input-aligned rebuild (2026-11-20, review). Both builders read only question text and evidence, in shuffled order,
with no source database (variants/opaque_merged/workload_aligned); conditions evoA and dmA on the 400 MG test
questions, v4 answering settings, three repeats per fold. Compared with the Section 5 ontologies (evo, dm), the agent
without ontology (none) and documentation (doc). Column-tolerant EX; 95% bootstrap over questions within source db.
Output: runs/v4/aligned_rebuild_20261120.json"""
import json
import analysis_review_20261120 as A

meta = A.mg_meta()
d = {c: A.per_question(A.V4, "MG", c) for c in ("none", "doc", "evo", "dm", "evoA", "dmA")}
keys = sorted(set.intersection(*[set(v) for v in d.values()]))
db = {k: meta[k]["source_db"] for k in keys}
out = {"n_questions": len(keys), "acc": {c: round(100 * A.mean(v, keys), 1) for c, v in d.items()}, "pairs": {}}
for a, b in (("evoA", "none"), ("dmA", "none"), ("evoA", "evo"), ("dmA", "dm"), ("evoA", "dmA"), ("evo", "dm"),
             ("doc", "evoA")):
    f = lambda ks, a=a, b=b: A.mean(d[a], ks) - A.mean(d[b], ks)                     # noqa: E731
    out["pairs"][f"{a}-{b}"] = {"diff": round(100 * f(keys), 1), "ci": A.ci(f, keys, db)}
for fold in ("fwd", "rev"):
    ks = [k for k in keys if k.startswith(fold)]
    out[f"acc_{fold}"] = {c: round(100 * A.mean(v, ks), 1) for c, v in d.items()}
open(A.V4 / "aligned_rebuild_20261120.json", "w").write(json.dumps(out, indent=1))
print(json.dumps(out))

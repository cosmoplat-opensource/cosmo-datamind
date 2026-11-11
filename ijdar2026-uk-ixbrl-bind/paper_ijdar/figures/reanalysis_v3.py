#!/usr/bin/env python3
"""Two post hoc development-set checks of the final system (v34, semantic-memory composer), scored with the paper's
statistics (company-cluster bootstrap, 2,000 resamples, seed 0; Section 5.1).

1. v34_top40: the frozen v34 composer with every concept candidate outside the language-model prompts' 40-concept list
   masked at answer time (cvpr_complete_v9/native_v34_top40_eval.py).  Answers whether the concept lead over the
   language-model controls comes only from scoring all 512 candidates.
2. v34loco: v34 with every prior fitted leave-one-company-out (cvpr_complete_v9/native_structural_prior_v34_loco.py,
   native_v34loco_eval.py).  Answers how much of v34's development accuracy depends on priors that saw the evaluated
   company's own 2023 cells.
Reported: Table 2 convention (budget 10, hash-prefix order, seed means), pooled differences to v34 over orders, budgets
and seeds, and differences to the two language-model controls at budget 10 (seed means) and pooled.
Writes reanalysis_v3.json.  usage: python3 reanalysis_v3.py [v34_top40] [v34loco]
"""
import collections
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_figures as M  # noqa: E402
import reanalysis_v1 as RA  # noqa: E402
from compare_runs_v27 import SEEDS  # noqa: E402
from llm_baseline_v30 import TOP_FIELDS  # noqa: E402

R = M.R
OUT = Path(__file__).resolve().parent / "reanalysis_v3.json"
COMPS = ("semantic_tuple_exact", "field", "period", "dimensions", "unit", "quantity")


def prompt_concepts(task):
    fields = sorted(task["field_candidates"], key=lambda c: -(0.65 * c["retrieval_scores"][0] + 0.25 * c["retrieval_scores"][1] + 0.1 * c["retrieval_scores"][2]))
    return {"{" + c["namespace"] + "}" + c["name"] for c in fields[:TOP_FIELDS]}


def summarise(version, base, refs, company, llmc, inside):
    run = RA.run_correct(version, "semantic_memory", refs)
    hp10 = {c: round(100 * np.mean([float(run["hash_prefix", s, 10][t][c]) for s in SEEDS for t in refs]), 1) for c in COMPS}
    out = {"table2_convention_budget10_hash_prefix": hp10}
    out["pooled_minus_v34"] = {c: RA.cluster_boot(*RA.diffs_to_sums(RA.pooled_run_vs_run(base, run, refs, company, c))) for c in ("semantic_tuple_exact", "field", "period")}
    for name, lab in (("fewshot", "history_matched"), ("annotated", "annotated")):
        for c in ("semantic_tuple_exact", "field"):
            seedmean = {t: np.mean([float(run["hash_prefix", s, 10][t][c]) for s in SEEDS]) for t in refs}
            diff = collections.defaultdict(list)
            for t in refs:
                diff[company[t]].append(seedmean[t] - float(llmc[name][t][c]))
            r10 = RA.cluster_boot(*RA.diffs_to_sums(diff))
            r10["companies_ahead"] = int(sum(np.mean([seedmean[t] - float(llmc[name][t][c]) for t in refs if company[t] == k]) > 0 for k in sorted(set(company.values()))))
            out.setdefault("minus_" + lab, {})[c] = {"budget10_hash_prefix": r10,
                                                       "pooled": RA.cluster_boot(*RA.diffs_to_sums(RA.pooled_run_vs_llm(run, llmc[name], refs, company, c)))}
    for part, ts in (("inside_40", [t for t in refs if inside[t]]), ("outside_40", [t for t in refs if not inside[t]])):
        out.setdefault("by_list", {})[part] = {"cells": len(ts), **{c: round(100 * np.mean([float(run["hash_prefix", s, 10][t][c]) for s in SEEDS for t in ts]), 1) for c in ("semantic_tuple_exact", "field")}}
    return out


def main():
    M.load_all()
    d = M.SPLIT_DATA["dev"]; refs, company, llmc = d["refs"], d["company"], d["llmc"]
    tasks = {t["task_id"]: t for t in json.loads((R / "common_candidate_v30/public/tasks.json").read_text())}
    inside = {t: refs[t]["field"] in prompt_concepts(tasks[t]) for t in refs}
    base = RA.run_correct("v34", "semantic_memory", refs)
    res = {"cells": len(refs), "filed_concept_in_40_list_pct": round(100 * np.mean(list(inside.values())), 1),
           "v34": {"table2_convention_budget10_hash_prefix": {c: round(100 * np.mean([float(base["hash_prefix", s, 10][t][c]) for s in SEEDS for t in refs]), 1) for c in COMPS},
                   "by_list": {p: {"cells": len(ts), **{c: round(100 * np.mean([float(base["hash_prefix", s, 10][t][c]) for s in SEEDS for t in ts]), 1) for c in ("semantic_tuple_exact", "field")}}
                               for p, ts in (("inside_40", [t for t in refs if inside[t]]), ("outside_40", [t for t in refs if not inside[t]]))}},
           "controls": {lab: {c: round(100 * np.mean([float(llmc[name][t][c]) for t in refs]), 1) for c in ("semantic_tuple_exact", "field")}
                        for name, lab in (("fewshot", "history_matched"), ("annotated", "annotated"))}}
    versions = sys.argv[1:] or ["v34_top40", "v34loco"]
    if OUT.exists():
        res = {**json.loads(OUT.read_text()), **res}
    for version in versions:
        res[version] = summarise(version, base, refs, company, llmc, inside)
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()

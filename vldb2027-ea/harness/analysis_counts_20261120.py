#!/usr/bin/env python3
"""Counts quoted in Sections 4.2, 5 and 6.1 that are not part of another analysis output (2026-11-20).
1. evo_nomap tool responses: share of question-runs whose ontology tool responses name at least one column code
   (clNNNN), per fold, over the three repeats (runs/v5/qa/MG_<fold>/evo_nomap_r<rep>; traces as read by
   analysis_v5_common.ontology_responses). Question-runs without a trace are counted as missing.
2. EvoOntology's merged-schema relations (runs/v2/evo/w_opaque_merged_<fold>): how many name at least one / two
   table.column references (tbNNN.clNNNN) anywhere in the record.
3. DataMind IR of BIRD-readable-merged: relations, data-verified relations and concepts per fold, for the build with
   the opaque-merged code (b9fa576, conditions dm_b9 / dmdoc_b9) and the earlier build with later code.
Output: runs/v5/counts_20261120.json"""
import glob
import json
import re
from pathlib import Path

import analysis_v5_common as C

EXP = Path(__file__).resolve().parent
out = {"nomap_tool_responses": {}, "evo_relations": {}, "dm_readable_merged_ir": {}}
for fold in ("fwd", "rev"):
    runs = with_code = missing = 0
    for rep in (1, 2, 3):
        rdir = sorted(glob.glob(str(EXP / f"runs/v5/qa/MG_{fold}/evo_nomap_r{rep}/*/*")))
        rdir = [Path(p) for p in rdir if (Path(p) / "bird_merged").is_dir()][-1]
        res = json.loads((rdir / "all_results.json").read_text())["results"]
        for r in res:
            runs += 1
            t = C.load_trace(rdir, "bird_merged", r["question_id"])
            if t is None:
                missing += 1
                continue
            texts = [txt for _, txt in C.ontology_responses(t.get("messages") or [])]
            with_code += any(re.search(r"\bcl\d{4}\b", x) for x in texts)
    out["nomap_tool_responses"][fold] = {"question_runs": runs, "missing_traces": missing,
                                         "with_column_code": with_code,
                                         "pct_of_runs_with_trace": round(100 * with_code / (runs - missing), 1)}
    rel = json.loads((EXP / f"runs/v2/evo/w_opaque_merged_{fold}/bird_merged/versions/ontology_v0/relations.json").read_text())
    refs = [set(re.findall(r"\btb\d{3}\.cl\d{4}\b", json.dumps(x))) for x in rel]
    out["evo_relations"][fold] = {"relations": len(rel), "with_table_column": sum(1 for r in refs if r),
                                  "with_two_or_more": sum(1 for r in refs if len(r) >= 2)}
for name, sfx in (("b9fa576", "_b9fa576"), ("later_code", "")):
    for fold in ("fwd", "rev"):
        ir = json.loads((EXP / f"runs/v2/dm/w_readable_merged_{fold}{sfx}/ir/bird_merged.json").read_text())
        rels = ir.get("relations") or []
        out["dm_readable_merged_ir"][f"{name}_{fold}"] = {
            "relations": len(rels),
            "verified": sum(1 for r in rels if str(r.get("status") or r.get("verification") or "").startswith("verified")),
            "concepts": len(ir.get("concepts") or [])}
(EXP / "runs/v5/counts_20261120.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))

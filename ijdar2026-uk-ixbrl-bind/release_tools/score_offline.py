#!/usr/bin/env python3
"""Score released predictions or language-model answers against the released references, without any filing,
network or API access.  Prints exact-tuple and per-component accuracy (and writes per-cell correctness with --cells).

    python3 release_tools/score_offline.py dev     <predictions .json file(s) or answers directory>
    python3 release_tools/score_offline.py heldout <...>
    python3 release_tools/score_offline.py sealed  <...>

Component rules are those of cvpr_complete_v9/score_native_binding_v17.component_correct (copied below).
Run `python3 unpack.py` first.
"""
import json, sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NC = ROOT / "runs/cvpr_complete_v10_20261014/native_correspondence"
REFS = {"dev": NC / "scoring_only/references_resolved_dev.json", "heldout": NC / "heldout_v1/scoring_only/references.json",
        "sealed": NC / "sealed_v1/scoring_only/references.json"}
TASKS = {"dev": NC / "common_candidate_v30/public/tasks.json", "heldout": NC / "heldout_v1/bank/public/tasks.json",
         "sealed": NC / "sealed_v1/bank/public/tasks.json"}
COMPS = ("field", "entity", "period", "dimensions", "unit", "quantity", "semantic_tuple_exact")


def component_correct(prediction, reference):
    result = {key: prediction.get(key) is not None and prediction[key] == reference[key] for key in ("field", "entity", "period", "unit")}
    result["dimensions"] = prediction.get("dimensions") is not None and sorted(prediction["dimensions"], key=lambda d: d["axis"]) == sorted(reference["dimensions"], key=lambda d: d["axis"])
    result["quantity"] = prediction.get("quantity") is not None and Decimal(prediction["quantity"]) == Decimal(reference["quantity"])
    result["semantic_tuple_exact"] = all(result.values())
    return result


def main():
    split, paths = sys.argv[1], [Path(p) for p in sys.argv[2:] if not p.startswith("--")]
    future = {t["task_id"] for t in json.loads(TASKS[split].read_text()) if t["source_year"] != 2023}
    allrefs = json.loads(REFS[split].read_text())
    refs = {k: v for k, v in allrefs.items() if k in future}
    preds = {}
    for p in paths:
        files = sorted(p.glob("*.json")) if p.is_dir() else [p]
        for f in files:
            d = json.loads(f.read_text())
            if "predictions" in d:
                preds.update({q["task_id"]: q["predicted_binding"] for q in d["predictions"]})
            elif "task_id" in d:
                preds[d["task_id"]] = d.get("predicted_binding")
    cells = {t: component_correct(preds[t] or {}, refs[t]) for t in sorted(preds) if t in refs}
    n = len(cells)
    print(f"{split}: {n} scored cells (of {len(refs)} references)")
    for c in COMPS:
        print(f"  {c:22s} {100 * sum(r[c] for r in cells.values()) / max(n, 1):6.2f}")
    if "--cells" in sys.argv:
        print("task_id\t" + "\t".join(COMPS))
        for t, r in cells.items():
            print(t + "\t" + "\t".join(str(int(r[c])) for c in COMPS))


if __name__ == "__main__":
    main()

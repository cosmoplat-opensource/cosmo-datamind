"""Page-level consistency decoding (candidate v34): cells in one table column share period and dimensions;
cells in one row share the concept.  Applied as post-processing to a frozen run's predictions.

For every page, targets are grouped by their structural column (column_bounds of the v27 context) and by their
row (vertical overlap of the target boxes).  Within a column group the period and the dimension tuple are
re-decided by a confidence-weighted vote over the group's own predictions (the composer's witness posteriors as
weights); within a row group the concept likewise.  Nothing outside the frozen predictions and the public table
contexts is read.  Reports exact-tuple accuracy before and after, paired per target, for the development run
(pooled over policies, budgets, seeds; company-cluster bootstrap) and the held-out evaluation folder.

usage: column_consistency_v1.py dev <run_predictions_dir> <out.json>
       column_consistency_v1.py heldout <evaluation_dir> <out.json>
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from compare_llm_run_v1 import HELD, bootstrap
from compare_runs_v27 import BUDGETS, COMPONENTS, METHODS, POLICIES, SEEDS
from score_native_binding_v17 import component_correct, scoring_references

YEAR_COL = {}
MIN_GROUP = 2
import os
PERIOD_VOTE = os.environ.get("PERIOD_VOTE", "year_columns")   # none | year_columns | all
FIELD_VOTE = os.environ.get("FIELD_VOTE", "1") == "1"


def groups(tasks, contexts, anchors):
    """Column groups: same page and overlapping column bounds. Row groups: same page and vertical overlap."""
    by_page = defaultdict(list)
    for t in tasks:
        by_page[anchors[t["task_id"]]["page_hash"]].append(t)
    col_of, row_of = {}, {}
    for page, ts in by_page.items():
        cols, rows = [], []
        for t in ts:
            b = contexts[t["task_id"]].get("column_bounds")
            if b and isinstance(b[0], list) and len(b) == 2 and isinstance(b[1], (int, float)):
                pass
            cb = t["target_box"]
            cx = (cb[0] + cb[2]) / 2
            placed = False
            for g in cols:
                if abs(g["cx"] - cx) < 0.03:
                    g["ids"].append(t["task_id"]); g["cx"] = (g["cx"] * (len(g["ids"]) - 1) + cx) / len(g["ids"]); placed = True; break
            if not placed:
                cols.append({"cx": cx, "ids": [t["task_id"]]})
            cy0, cy1 = cb[1], cb[3]
            placed = False
            for g in rows:
                if min(g["y1"], cy1) - max(g["y0"], cy0) > 0.4 * (cy1 - cy0):
                    g["ids"].append(t["task_id"]); placed = True; break
            if not placed:
                rows.append({"y0": cy0, "y1": cy1, "ids": [t["task_id"]]})
        for g in cols:
            for tid in g["ids"]:
                col_of[tid] = tuple(g["ids"])
        for g in rows:
            for tid in g["ids"]:
                row_of[tid] = tuple(g["ids"])
    return col_of, row_of


def confidence(pred, component):
    post = (pred.get("visual_witness_posteriors") or {}).get(component)
    if not post:
        return 0.5
    return max(p["probability"] for p in post)


def key(value):
    return json.dumps(value, sort_keys=True)


def revise(preds, col_of, row_of):
    """preds: task_id -> prediction record (with predicted_binding and posteriors). Returns revised bindings."""
    out = {}
    for tid, p in preds.items():
        b = p.get("predicted_binding")
        if not b:
            out[tid] = b; continue
        nb = dict(b)
        for component, grp in (("period", col_of.get(tid, (tid,))), ("dimensions", col_of.get(tid, (tid,)))):
            members = [m for m in grp if preds.get(m) and preds[m].get("predicted_binding")]
            if len(members) < MIN_GROUP:
                continue
            if component == "period":
                if PERIOD_VOTE == "none":
                    continue
                if PERIOD_VOTE == "year_columns" and not all(YEAR_COL.get(m) for m in members):
                    continue
            votes = defaultdict(float); values = {}
            for m in members:
                v = preds[m]["predicted_binding"][component]
                votes[key(v)] += confidence(preds[m], component if component != "dimensions" else "dimensions"); values[key(v)] = v
            best = max(votes, key=votes.get)
            if votes[best] > 0.5 * sum(votes.values()):
                nb[component] = values[best]
        grp = row_of.get(tid, (tid,))
        members = [m for m in grp if preds.get(m) and preds[m].get("predicted_binding")]
        if FIELD_VOTE and len(members) >= MIN_GROUP:
            votes = defaultdict(float)
            for m in members:
                votes[preds[m]["predicted_binding"]["field"]] += 1.0
            best = max(votes, key=votes.get)
            if votes[best] > 0.5 * sum(votes.values()):
                nb["field"] = best
        out[tid] = nb
    return out


def main(split, run_dir, out):
    if split == "dev":
        from native_v30_bank import DEST as BANK
        from native_v30_bank import PERCEPTION
        tasks = json.loads((BANK / "public/tasks.json").read_text())
        contexts = json.loads((BANK / "prior_v27/table_contexts.json").read_text())
        anchors = json.loads((PERCEPTION / "localization/public_anchors.json").read_text())
        future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
        refs = scoring_references(future)[0]
        configs = [(p, m, s, b) for p in POLICIES for m in METHODS for s in SEEDS for b in BUDGETS]
        companies = sorted({t["company"] for t in future.values()})

        def load(p, m, s, b):
            rows = {}
            for c in companies:
                for q in json.loads((run_dir / p / c / m / str(s) / f"{b}.json").read_text())["predictions"]:
                    rows[q["task_id"]] = q
            return rows
    else:
        tasks = json.loads((HELD / "bank/public/tasks.json").read_text())
        contexts = json.loads((HELD / "bank/table_contexts.json").read_text())
        anchors = json.loads((HELD / "localization/public_anchors.json").read_text())
        future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
        allrefs = json.loads((HELD / "scoring_only/references.json").read_text())
        refs = {k: allrefs[k] for k in future}
        companies = sorted({t["company"] for t in future.values()})
        configs = [("hash_prefix", m, s, b) for m in METHODS for s in SEEDS for b in BUDGETS]

        def load(p, m, s, b):
            rows = {}
            for c in companies:
                for q in json.loads((run_dir / "predictions" / c / m / str(s) / f"{b}.json").read_text())["predictions"]:
                    rows[q["task_id"]] = q
            return rows
    col_of, row_of = groups(list(future.values()), contexts, anchors)
    YEAR_COL.update({tid: bool(contexts[tid].get("col_is_year_unit")) for tid in future})
    sizes = [len(set(v)) for v in col_of.values()]
    company = {t["task_id"]: t["company"] for t in future.values()}
    report = {"split": split, "targets": len(refs), "column_group_size_mean": float(np.mean(sizes)), "targets_in_groups_of_2plus": int(sum(1 for s in sizes if s >= 2)), "methods": {}}
    for m in METHODS:
        before = {c: [] for c in COMPONENTS}; after = {c: [] for c in COMPONENTS}
        diffs = {c: {cl: [] for cl in companies} for c in COMPONENTS}
        for p, mm, s, b in configs:
            if mm != m:
                continue
            preds = load(p, m, s, b)
            revised = revise(preds, col_of, row_of)
            for tid, ref in refs.items():
                r0 = component_correct(preds[tid]["predicted_binding"], ref) if preds.get(tid, {}).get("predicted_binding") else {c: False for c in COMPONENTS}
                r1 = component_correct(revised[tid], ref) if revised.get(tid) else {c: False for c in COMPONENTS}
                for c in COMPONENTS:
                    before[c].append(float(r0[c])); after[c].append(float(r1[c])); diffs[c][company[tid]].append(float(r1[c]) - float(r0[c]))
        entry = {"before": {c: float(np.mean(before[c])) for c in COMPONENTS}, "after": {c: float(np.mean(after[c])) for c in COMPONENTS}, "paired": {}}
        for c in COMPONENTS:
            allv = [v for cl in companies for v in diffs[c][cl]]
            entry["paired"][c] = {"difference": float(np.mean(allv)), "ci95": bootstrap(diffs[c])}
        report["methods"][m] = entry
        print("%-22s" % m, "exact %.1f -> %.1f" % (100 * entry["before"]["semantic_tuple_exact"], 100 * entry["after"]["semantic_tuple_exact"]),
              "  ".join("%s %+4.1f [%+4.1f,%+4.1f]" % (c[:6], 100 * entry["paired"][c]["difference"], 100 * entry["paired"][c]["ci95"][0], 100 * entry["paired"][c]["ci95"][1]) for c in COMPONENTS), flush=True)
    print("column groups: mean size %.2f, targets in groups of 2+: %d / %d" % (report["column_group_size_mean"], report["targets_in_groups_of_2plus"], len(refs)))
    Path(out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), sys.argv[3])

#!/usr/bin/env python3
"""Two development-set statistics of Section 5.2 / S3.1 / S3.3, recomputed from the stored predictions.

(a) Period errors before the structural prior (v26, semantic-memory composer, hash-prefix order, budget 10, three
    seeds): share of the period errors whose predicted period ends one year before or after the filed period
    (the neighbouring year column).
(b) Row-label repeats: development evaluation cells whose OCR row label (the row-label memory's input,
    prior_v29/row_labels.json; lower-cased, punctuation removed, whitespace collapsed) equals the row label of one of the
    company's own 2023 teacher cells, and the exact-tuple accuracy of v32 (budget 10, hash-prefix, three seeds) on
    them and on the other cells.
Writes reanalysis_v2.json next to this file.  usage: python3 reanalysis_v2.py
"""
import collections
import json
import re
from pathlib import Path

import numpy as np

import make_figures as M

HERE = Path(__file__).resolve().parent
R = M.R


def period_end(p):
    return (p or {}).get("instant") or (p or {}).get("end_date")


def part_a(refs, comp):
    c = collections.Counter()
    for s in M.SEEDS:
        for co in sorted(set(comp.values())):
            f = R / f"common_candidate_v26/learning_v1/predictions/hash_prefix/{co}/semantic_memory/{s}/10.json"
            for q in json.loads(f.read_text())["predictions"]:
                t = q["task_id"]
                if t not in refs:
                    continue
                b, r = q["predicted_binding"], refs[t]
                if M.component_correct(b, r)["period"]:
                    continue
                c["period_errors"] += 1
                a, e = period_end(b.get("period")), period_end(r["period"])
                if a and e and abs(int(a[:4]) - int(e[:4])) == 1:
                    c["neighbouring_year"] += 1
    return {**c, "share_pct": round(100 * c["neighbouring_year"] / c["period_errors"], 1)}


def part_b(refs, comp):
    tasks = json.loads((R / "common_candidate_v30/public/tasks.json").read_text())
    year = {t["task_id"]: str(t["source_year"]) for t in tasks}
    company = {t["task_id"]: t["company"] for t in tasks}
    norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()
    labels = json.loads((R / "common_candidate_v30/prior_v29/row_labels.json").read_text())
    teach = collections.defaultdict(set)
    for t, v in labels.items():
        if year.get(t) == "2023" and norm(v):
            teach[company[t]].add(norm(v))
    hit = [t for t in refs if norm(labels.get(t)) and norm(labels.get(t)) in teach[comp[t]]]
    rest = [t for t in refs if t not in set(hit)]
    v32 = R / "common_candidate_v32/learning_v1"
    P = {s: M.pred_rows("dev", v32, refs, comp, s, 10) for s in M.SEEDS}
    acc = lambda ts: round(100 * float(np.mean([float(M.component_correct(P[s][t], refs[t])["semantic_tuple_exact"])
                                                  for s in M.SEEDS for t in ts])), 1)
    return {"cells": len(refs), "repeated_label_cells": len(hit), "share_pct": round(100 * len(hit) / len(refs), 1),
            "v32_exact_repeated": acc(hit), "v32_exact_other": acc(rest)}


if __name__ == "__main__":
    refs, comp = M.dev_refs()
    out = {"a_period_errors_v26": part_a(refs, comp), "b_row_label_repeats_v32": part_b(refs, comp)}
    (HERE / "reanalysis_v2.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))

#!/usr/bin/env python3
"""v5 re-analysis 3: robustness checks of the protocol-v4 results (zero API cost).

(a) Per-repeat accuracy (both folds pooled per repeat) for every setting x condition, and the share of questions
    whose correctness differs across the three repeats (tolerant and strict).
(b) Holm step-down adjustment over the family of Figure 5 (paper_vldb/make_figures.py forest_v4: 6 pairs x 3
    settings = 18 column-tolerant paired differences). Raw p-values are two-sided bootstrap p-values from the same
    paired resamples as stats_v4.py (seed 20261107, B = 10,000): p = min(1, 2*min((#d*<=0 + 1)/(B+1), (#d*>=0 + 1)/(B+1))).
    The smallest attainable p is 2/(B+1) ~ 2.0e-4.
(c) Equivalence (TOST) for the "no gain" claims: a difference is declared equivalent within +-delta (delta = 3, 5
    points) when its 90% bootstrap interval (same resamples) lies inside (-delta, +delta). Claims tested: evo-none and
    dm-none on R and O; evodoc-doc and dmdoc-doc on R, O and MG (Section 1 finding 3 "with documentation ... within
    +-1 point").
(d) BIRD extra-column effect by question type for Table 1 row 1 (DataMind full system, runs/v2/rescore
    {fwd,rev}__dm-w__dm__full_r{1,2,3}.json vs ReAct + EvoOntology, {fwd,rev}__evo-w__react__full_r*.json), from stored
    per-question [strict(set rule), tolerant] scores (no re-execution). Question type from the readable gold SQL:
    ratio (division / percentage), top-k (ORDER BY ... LIMIT), count, aggregate (SUM/AVG/MIN/MAX), lookup (other);
    and BIRD difficulty. Contribution of a type = its share of the overall gap (points of all questions).
(e) Stricter column-tolerant EX allowing at most one extra column: a question-run is correct if strict-correct, or
    tolerant-correct and (#pred columns - #gold columns) <= 1. Column counts are read on the local SQLite files
    (read-only, uri mode=ro) with SELECT * FROM (<sql>) LIMIT 0, 30 s progress-handler timeout, only for the
    question-runs that are tolerant-correct but strict-wrong. The stats_v4 pairs are recomputed under this metric.
Inputs: runs/v4/qa/*, runs/v2/rescore/*, question files, dev_databases (R/O/MG).
Output: runs/v4/analysis_v5_robust.json
Usage: python3 -B analysis_v5_robust.py
Fixed seeds: 20261107 (all bootstraps). No API/model calls.
"""
import json
import re
import time
from collections import defaultdict

import numpy as np

import analysis_v5_common as C
import stats_v4 as SV

OUT = C.V4 / "analysis_v5_robust.json"
FIG5 = ["doc-none", "evo-none", "dm-none", "evodoc-doc", "dmdoc-doc", "M-doc"]
COND = SV.CONDS


def per_q_reps(S):
    """key -> cond -> list of 3 [strict, tol] (rep order), plus db map."""
    reps = defaultdict(lambda: defaultdict(lambda: [None, None, None]))
    for fold in ("fwd", "rev"):
        for c in COND:
            for rep in (1, 2, 3):
                r = json.load(open(C.run_dir(S, fold, c, rep) / "rescore.json"))
                for i, v in r.items():
                    reps[f"{fold}:{i}"][c][rep - 1] = v
    return reps


def part_a():
    out = {}
    for S in ("R", "O", "MG"):
        reps = per_q_reps(S)
        o = {}
        for c in COND:
            keys = [k for k in reps if all(x is not None for x in reps[k][c])]
            pr = {nm: [round(100 * np.mean([reps[k][c][r][j] for k in keys]), 1) for r in range(3)]
                  for j, nm in ((1, "tolerant"), (0, "strict"))}
            flip = {nm: round(100 * np.mean([len({reps[k][c][r][j] for r in range(3)}) > 1 for k in keys]), 1)
                    for j, nm in ((1, "tolerant"), (0, "strict"))}
            o[c] = {"n": len(keys), "per_repeat": pr, "range_tolerant": round(max(pr["tolerant"]) - min(pr["tolerant"]), 1),
                    "flip_pct": flip}
        out[S] = o
    return out


def setting_data(S, score=None):
    acc, db, _ = SV.load(S)
    if score is not None:
        acc = score
    return acc, db


def holm(pv):
    items = sorted(pv.items(), key=lambda x: x[1])
    m, adj, run = len(items), {}, 0.0
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        adj[k] = run
    return adj


def part_bc():
    raw, diffs, tost = {}, {}, {}
    for S in ("R", "O", "MG"):
        acc, db = setting_data(S)
        keys = list(db)
        idx = C.index_samples(db, C.CLUSTER[S])
        for pair in FIG5 + ["evodoc-doc", "dmdoc-doc"]:
            a, b = pair.split("-")
            for j, nm in ((1, "tolerant"), (0, "strict")):
                v = np.array([acc[a][k][j] - acc[b][k][j] for k in keys])
                d = C.dist(v, idx)
                key = f"{S}:{pair}:{nm}"
                diffs[key] = {"diff": round(100 * v.mean(), 1), "ci95": C.ci(d), "ci90": C.ci(d, 0.90), "p_two_sided": round(C.pval(d), 5)}
        for pair in (["evo-none", "dm-none", "evodoc-doc", "dmdoc-doc"] if S != "MG" else ["evodoc-doc", "dmdoc-doc"]):
            for nm in ("tolerant", "strict"):
                x = diffs[f"{S}:{pair}:{nm}"]
                lo, hi = x["ci90"]
                tost[f"{S}:{pair}:{nm}"] = {"diff": x["diff"], "ci90": x["ci90"],
                                            "equivalent_within_3": bool(lo > -3 and hi < 3),
                                            "equivalent_within_5": bool(lo > -5 and hi < 5),
                                            "noninferior_upper_lt_3": bool(hi < 3), "noninferior_upper_lt_5": bool(hi < 5)}
    fam = {f"{S}:{p}:tolerant": diffs[f"{S}:{p}:tolerant"]["p_two_sided"] for S in ("R", "O", "MG") for p in FIG5}
    adj = holm(fam)
    hb = {k: {"diff": diffs[k]["diff"], "ci95": diffs[k]["ci95"], "p_raw": fam[k], "p_holm": round(adj[k], 5),
              "sig_unadjusted_ci": diffs[k]["ci95"][0] > 0 or diffs[k]["ci95"][1] < 0, "sig_holm_0.05": adj[k] < 0.05}
          for k in fam}
    return {"family": "Figure 5: 6 pairs x 3 settings, column-tolerant", "m": len(fam), "tests": hb}, tost, diffs


def qtype(sql, question):
    s = re.sub(r"'(?:[^']|'')*'", "''", sql).upper()
    if re.search(r"(?<![*/])\s/\s|\)\s*/|/\s*\(|\*\s*100|/\s*COUNT|/\s*SUM", s) or re.search(r"\bpercent|\bratio\b|\brate\b", question, re.I):
        return "ratio"
    if re.search(r"ORDER\s+BY", s) and re.search(r"\bLIMIT\b", s):
        return "topk"
    sel = s.split("FROM")[0]
    if re.search(r"\bCOUNT\s*\(", sel):
        return "count"
    if re.search(r"\b(SUM|AVG|MIN|MAX)\s*\(", sel):
        return "aggregate"
    return "lookup"


def part_d():
    R2 = C.EXP / "runs/v2/rescore"
    qs = {}
    for fold in ("fwd", "rev"):
        for i, q in C.questions("R", fold).items():
            qs[i] = q

    def pool(tmpl):
        out = defaultdict(list)
        for fold in ("fwd", "rev"):
            for rep in (1, 2, 3):
                for k, v in json.load(open(R2 / tmpl.format(d=fold, rep=rep))).items():
                    out[k].append(v)
        return out
    dm, ev = pool("{d}__dm-w__dm__full_r{rep}.json"), pool("{d}__evo-w__react__full_r{rep}.json")
    keys = sorted(dm)
    n = len(keys)
    m = lambda xs, j: sum(x[j] for x in xs) / len(xs)
    res = {"n_questions": n,
           "overall": {"strict_gap": round(100 * sum(m(dm[k], 0) - m(ev[k], 0) for k in keys) / n, 1),
                       "tolerant_gap": round(100 * sum(m(dm[k], 1) - m(ev[k], 1) for k in keys) / n, 1)}}
    for lab, f in (("by_sql_type", lambda k: qtype(qs[k]["SQL"], qs[k]["question"])), ("by_difficulty", lambda k: qs[k]["difficulty"])):
        g = defaultdict(list)
        for k in keys:
            g[f(k)].append(k)
        o = {}
        for t, ks in sorted(g.items(), key=lambda x: -len(x[1])):
            sg = 100 * sum(m(dm[k], 0) - m(ev[k], 0) for k in ks) / n
            tg = 100 * sum(m(dm[k], 1) - m(ev[k], 1) for k in ks) / n
            ev_wrong = sum(1 for k in ks for v in ev[k] if not v[0])
            ev_tol = sum(1 for k in ks for v in ev[k] if not v[0] and v[1])
            dm_wrong = sum(1 for k in ks for v in dm[k] if not v[0])
            dm_tol = sum(1 for k in ks for v in dm[k] if not v[0] and v[1])
            o[t] = {"n": len(ks), "strict_gap_contribution": round(sg, 2), "tolerant_gap_contribution": round(tg, 2),
                    "extra_column_effect": round(sg - tg, 2),
                    "within_type_strict_gap": round(100 * sum(m(dm[k], 0) - m(ev[k], 0) for k in ks) / len(ks), 1),
                    "react_strict_wrong_runs": ev_wrong, "react_of_which_tolerant_correct": ev_tol,
                    "dm_strict_wrong_runs": dm_wrong, "dm_of_which_tolerant_correct": dm_tol}
        res[lab] = o
    return res


def part_e():
    out, t0, nexec, fails = {}, time.time(), 0, 0
    cache = {}
    for S in ("R", "O", "MG"):
        acc, db, _ = SV.load(S)
        new = {c: {} for c in COND}
        extra_hist = defaultdict(lambda: defaultdict(int))
        for fold in ("fwd", "rev"):
            qs = C.questions(S, fold)
            for c in COND:
                per = defaultdict(list)
                for rep in (1, 2, 3):
                    results, rescore, _ = C.run_results(S, fold, c, rep)
                    for qid, (st, tol) in rescore.items():
                        val = st
                        if tol and not st:
                            q = qs[qid]
                            dbid = q["db_id"]
                            path = C.DBROOT[S] / dbid / f"{dbid}.sqlite"
                            pred = (results.get(qid) or {}).get("pred_sql") or ""
                            for sql in (pred, q["SQL"]):
                                if (S, dbid, sql) not in cache:
                                    cache[(S, dbid, sql)] = C.sql_width(path, sql)
                                    nexec += 1
                            pw, gw = cache[(S, dbid, pred)], cache[(S, dbid, q["SQL"])]
                            if pw is None or gw is None:
                                fails += 1
                                extra = None
                            else:
                                extra = pw - gw
                            extra_hist[c][str(extra)] += 1
                            val = 1 if (extra is not None and extra <= 1) else 0
                        per[qid].append([st, val])
                for qid, vs in per.items():
                    new[c][f"{fold}:{qid}"] = [sum(v[0] for v in vs) / len(vs), sum(v[1] for v in vs) / len(vs)]
        idx = C.index_samples(db, C.CLUSTER[S])
        keys = list(db)
        o = {"conditions": {}, "pairs": {}, "tolerant_only_runs_by_extra_columns": {c: dict(h) for c, h in extra_hist.items()}}
        for c in COND:
            o["conditions"][c] = {"strict": round(100 * np.mean([acc[c][k][0] for k in keys]), 1),
                                  "tolerant": round(100 * np.mean([acc[c][k][1] for k in keys]), 1),
                                  "tolerant_max1extra": round(100 * np.mean([new[c][k][1] for k in keys]), 1)}
        for a, b in SV.PAIRS:
            v = np.array([new[a][k][1] - new[b][k][1] for k in keys])
            d = C.dist(v, idx)
            o["pairs"][f"{a}-{b}"] = {"diff_max1extra": round(100 * v.mean(), 1), "ci": C.ci(d),
                                      "diff_tolerant_paper": round(100 * np.mean([acc[a][k][1] - acc[b][k][1] for k in keys]), 1)}
        out[S] = o
    out["meta"] = {"width_queries_executed": nexec, "width_failures": fails, "seconds": round(time.time() - t0, 1)}
    return out


def main():
    res = {"a_repeats": part_a()}
    res["b_holm"], res["c_tost"], res["all_diffs"] = part_bc()
    res["d_bird_extra_column_by_type"] = part_d()
    res["e_tolerant_max1extra"] = part_e()
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: res[k] for k in ("b_holm", "c_tost", "d_bird_extra_column_by_type")}, ensure_ascii=False, indent=1))
    print(json.dumps(res["e_tolerant_max1extra"], ensure_ascii=False))


if __name__ == "__main__":
    main()

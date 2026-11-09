#!/usr/bin/env python3
"""v5 re-analysis 1: stratification of the MG (BIRD-opaque-merged) gains (zero API cost).

Purpose: test whether the MG ontology gains are consistent with term-to-column mappings rather than verified joins.
  (a) gains by whether the gold SQL uses more than one table (and, separately, whether it has a JOIN keyword);
  (b) gains by number of gold tables (1, 2, >=3);
  (c) for the evo and dm question-runs: whether the ontology tool responses of that question-run contained every gold
      column code (cl####) and every gold table code (tb###); share surfaced, accuracy when surfaced / not surfaced,
      and the gain over the same question's no-ontology accuracy (mean of its 3 runs) in each group; and whether the
      responses contained a relation (or mapping join_path) naming both columns of every gold join equality.
Pairs: evo-none, dm-none, doc-none, evodoc-doc, dmdoc-doc. Column-tolerant primary, strict secondary.
Per question: mean over the 3 repeats; both folds pooled (400 questions). Intervals: the paper's MG method
(stats_v4.boot, question bootstrap stratified by source database, seed 20261107, 10,000 resamples), re-drawn within
each stratum. For (c) the interval resamples questions (stratified by source db) and keeps all their question-runs.
Tool-response detection: see analysis_v5_common.ontology_responses (agent message with tool_call naming
browse_semantics / resolve_semantics, followed by the environment message "Tool '<name>' result:\\n<json>").
Inputs: runs/v4/qa/MG_{fwd,rev}/<cond>_r{1,2,3}/ (rescore.json, all_results.json, traces), MG test question files.
Output: runs/v4/analysis_v5_strat.json
Usage: python3 -B analysis_v5_strat.py
Fixed seeds: 20261107 (bootstrap). No API/model calls; no SQLite access needed.
"""
import json
from collections import defaultdict

import numpy as np

import analysis_v5_common as C
import stats_v4 as SV

PAIRS = [("evo", "none"), ("dm", "none"), ("doc", "none"), ("evodoc", "doc"), ("dmdoc", "doc")]
OUT = C.V4 / "analysis_v5_strat.json"


def pair_stats(acc, db, keys):
    sub = {k: db[k] for k in keys}
    idx = C.index_samples(sub, False)
    out = {"n": len(keys)}
    for a, b in PAIRS:
        for j, nm in ((1, "tolerant"), (0, "strict")):
            v = np.array([acc[a][k][j] - acc[b][k][j] for k in sub])
            d = C.dist(v, idx)
            out[f"{a}-{b}:{nm}"] = {"diff": round(100 * v.mean(), 1), "ci": C.ci(d)}
    for c in ("none", "doc", "evo", "dm", "evodoc", "dmdoc"):
        out[f"acc_{c}:tolerant"] = round(100 * np.mean([acc[c][k][1] for k in sub]), 1)
    return out


def relation_texts(text):
    """Relation-like elements of one ontology tool response: results[*].relations[*] and mappings[*].join_path."""
    try:
        js = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    els = []
    for r in js.get("results") or []:
        if not isinstance(r, dict):
            continue
        for rel in r.get("relations") or []:
            els.append(json.dumps(rel))
        for m in r.get("mappings") or []:
            if isinstance(m, dict) and m.get("join_path"):
                els.append(json.dumps(m["join_path"]))
    return els


def main():
    acc, db, _ = SV.load("MG")
    gold, gq = {}, {}
    for fold in ("fwd", "rev"):
        for i, q in C.questions("MG", fold).items():
            gq[f"{fold}:{i}"] = q["SQL"]
    for k, sql in gq.items():
        gold[k] = {"tables": C.mg_tables(sql), "cols": C.mg_columns(sql), "join_kw": C.has_join_kw(sql),
                   "joins": C.mg_join_pairs(sql)}
    keys = list(db)
    assert all(k in gold for k in keys)
    res = {"n_questions": len(keys), "overall": pair_stats(acc, db, keys)}

    # (a) multi-table / JOIN keyword
    multi = [k for k in keys if len(gold[k]["tables"]) > 1]
    single = [k for k in keys if len(gold[k]["tables"]) <= 1]
    res["a_multi_table"] = {"gt1_table": pair_stats(acc, db, multi), "one_table": pair_stats(acc, db, single)}
    res["a_join_keyword"] = {"join": pair_stats(acc, db, [k for k in keys if gold[k]["join_kw"]]),
                             "no_join": pair_stats(acc, db, [k for k in keys if not gold[k]["join_kw"]])}
    # (b) number of tables
    res["b_n_tables"] = {lab: pair_stats(acc, db, [k for k in keys if f(len(gold[k]["tables"]))])
                         for lab, f in (("1", lambda n: n <= 1), ("2", lambda n: n == 2), ("ge3", lambda n: n >= 3))}

    # (c) surfaced gold columns in ontology responses, per question-run
    none_mean = {k: acc["none"][k] for k in keys}
    pos = {k: i for i, k in enumerate(keys)}
    idx = C.index_samples(db, False)
    res["c_surfaced"] = {}
    for cond in ("evo", "dm"):
        # per question accumulators per group
        G = defaultdict(lambda: {"num_t": np.zeros(len(keys)), "num_s": np.zeros(len(keys)), "gain_t": np.zeros(len(keys)),
                                 "gain_s": np.zeros(len(keys)), "den": np.zeros(len(keys))})
        cnt = defaultdict(int)
        for fold in ("fwd", "rev"):
            for rep in (1, 2, 3):
                results, rescore, rdir = C.run_results("MG", fold, cond, rep)
                for qid, r in results.items():
                    k = f"{fold}:{qid}"
                    if k not in pos:
                        continue
                    t = C.load_trace(rdir, r["db_id"], qid)
                    if t is None:
                        cnt["missing_trace"] += 1
                        continue
                    resp = C.ontology_responses(t.get("messages") or [])
                    text = "\n".join(x for _, x in resp)
                    codes_c, codes_t = set(C.CODE_COL.findall(text)), set(C.CODE_TAB.findall(text))
                    g = gold[k]
                    cols_ok = g["cols"] <= codes_c
                    tabs_ok = g["tables"] <= codes_t
                    st, tol = rescore.get(qid, [0, 0])
                    groups = ["all", "cols_and_tables_surfaced" if (cols_ok and tabs_ok) else "not_all_surfaced",
                              "cols_surfaced" if cols_ok else "cols_not_surfaced",
                              "tables_surfaced" if tabs_ok else "tables_not_surfaced"]
                    if g["joins"]:
                        rels = []
                        unparsable = False
                        for _, x in resp:
                            e = relation_texts(x)
                            if e is None:
                                unparsable = True
                            else:
                                rels += e
                        cnt["join_runs"] += 1
                        cnt["join_runs_unparsable_response"] += unparsable
                        rel_ok = all(any(a in e and b in e for e in rels) for a, b in g["joins"])
                        any_ok = all(a in codes_c and b in codes_c for a, b in g["joins"])
                        groups += ["join_relation_covered" if rel_ok else "join_relation_not_covered",
                                   "join_cols_anywhere" if any_ok else "join_cols_not_anywhere"]
                    if not resp:
                        groups.append("no_ontology_response")
                    i = pos[k]
                    for gname in groups:
                        a = G[gname]
                        a["num_t"][i] += tol; a["num_s"][i] += st; a["den"][i] += 1
                        a["gain_t"][i] += tol - none_mean[k][1]; a["gain_s"][i] += st - none_mean[k][0]
        n_all = G["all"]["den"].sum()
        out = {"question_runs": int(n_all), "counts": dict(cnt), "groups": {}}
        for gname, a in sorted(G.items()):
            den = a["den"]
            if den.sum() == 0:
                continue
            out["groups"][gname] = {
                "question_runs": int(den.sum()), "share_of_runs_pct": round(100 * den.sum() / n_all, 1),
                "n_questions": int((den > 0).sum()),
                "acc_tolerant": round(100 * a["num_t"].sum() / den.sum(), 1),
                "acc_tolerant_ci": C.ci_nan(C.ratio_dist(a["num_t"], den, idx)),
                "acc_strict": round(100 * a["num_s"].sum() / den.sum(), 1),
                "gain_vs_none_tolerant": round(100 * a["gain_t"].sum() / den.sum(), 1),
                "gain_vs_none_tolerant_ci": C.ci_nan(C.ratio_dist(a["gain_t"], den, idx)),
                "gain_vs_none_strict": round(100 * a["gain_s"].sum() / den.sum(), 1)}
        res["c_surfaced"][cond] = out
    res["notes"] = {
        "gold_tables_columns": "tb### / cl#### codes in the MG gold SQL after removing string literals",
        "join_pairs": "column equalities x.clA = y.clB anywhere in the gold SQL (ON or WHERE)",
        "surfaced": "all gold codes occur in the concatenated texts of the question-run's ontology tool responses",
        "join_relation_covered": "every gold join equality has a relation element (results[*].relations[*] or a mapping "
                                 "join_path) of some response whose JSON text contains both column codes",
        "gain_vs_none": "question-run correctness minus the same question's mean no-ontology correctness (3 runs)",
        "ci": "95% bootstrap, questions resampled within source database (stats_v4.boot seed 20261107), ratio of sums"}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()

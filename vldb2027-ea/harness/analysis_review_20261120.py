#!/usr/bin/env python3
"""Reviewer-requested analyses for the PVLDB draft (2026-11-20, zero API cost). Writes runs/v4/analysis_review_20261120.json.

1. four_cell      EvoOntology - no ontology (column-tolerant, mean of three repeats per question) on the SAME questions
                  in four settings: readable small databases (R), opaque small databases (O), readable merged schema (MGR,
                  protocol v5), opaque merged schema (MG); plus (MG - MGR) - (O - R). Common questions = MG test questions
                  present in all four. CI: 95% bootstrap, questions resampled within source database (stats_v4.boot).
2. table_recall   MG, every condition: share of question-runs whose final SQL references every physical table of the gold
                  SQL (empty or unparsable SQL counts as not found), next to column-tolerant accuracy; and, for evo vs doc,
                  question-runs that found all tables, split by whether the final SQL also references every gold column.
3. fig10_by_source  compiled condition (M) on MG: share of question-runs whose excerpt expands columns of every gold table,
                  by source database (analysis_sec6.manifest_tables: tables listed only by name are not counted).
4. turn_limit     MG: question-runs that reach 30 turns, how many still return SQL (after the forced final call) and how
                  many of those are correct.
5. joins          MG gold SQL: questions with column-equality joins, and how many have every join between equal column codes.
6. rebuild_overlap  Section 6.2 rebuild gain (dm93 - dm) on all 400 test questions and without the 61 fold-A->B validation
                  questions (all of which are fold-B->A test questions).
"""
import glob
import json
import random
import re
from collections import defaultdict
from pathlib import Path

import analysis_sec6 as S6
import analysis_v5_common as C
import stats_v4 as SV

EXP = Path(__file__).resolve().parent
V4, V5 = EXP / "runs/v4", EXP / "runs/v5"
OUT = V4 / "analysis_review_20261120.json"
SR = {"fwd": "minidev", "rev": "minidev_rev"}


def q_file(variant, fold):
    return json.loads(Path(glob.glob(str(EXP / f"variants/{variant}/questions/{SR[fold]}/test/*.json"))[0]).read_text())["questions"]


def mg_meta():
    meta = {}
    for fold in ("fwd", "rev"):
        for q in q_file("opaque_merged", fold):
            meta[f"{fold}:{q['question_id']}"] = q
    return meta


def per_question(root, setting, cond):
    """{fold:qid: mean tolerant} over the three repeats, or None if a unit is missing."""
    acc = {}
    for fold in ("fwd", "rev"):
        per = defaultdict(list)
        for rep in (1, 2, 3):
            f = root / "qa" / f"{setting}_{fold}" / f"{cond}_r{rep}" / "rescore.json"
            if not f.exists():
                return None
            for i, v in json.loads(f.read_text()).items():
                per[i].append(v[1])
        for i, vs in per.items():
            acc[f"{fold}:{i}"] = sum(vs) / len(vs)
    return acc


def ci(stat, keys, db, seed=20261107):
    sub = {k: db[k] for k in keys}
    samples = SV.boot(None, sub, False, seed)
    vals = sorted(stat(ks) for ks in samples)
    return [round(100 * vals[int(0.025 * len(vals))], 1), round(100 * vals[int(0.975 * len(vals)) - 1], 1)]


def mean(d, ks):
    return sum(d[k] for k in ks) / len(ks)


def four_cell(meta):
    srcs = {"R": (V4, "R"), "O": (V4, "O"), "MGR": (V5, "MGR"), "MG": (V4, "MG")}
    data = {}
    for name, (root, setting) in srcs.items():
        data[name] = {c: per_question(root, setting, c) for c in ("none", "evo")}
    keys = sorted(set(meta).intersection(*[set(data[n]["none"]) & set(data[n]["evo"]) for n in data]))
    db = {k: meta[k]["source_db"] for k in keys}
    out = {"n_questions": len(keys), "gain_evo_minus_none": {}}
    for n in data:
        g = lambda ks, n=n: mean(data[n]["evo"], ks) - mean(data[n]["none"], ks)
        out["gain_evo_minus_none"][n] = {"gain": round(100 * g(keys), 1), "ci": ci(g, keys, db),
                                        "none": round(100 * mean(data[n]["none"], keys), 1),
                                        "evo": round(100 * mean(data[n]["evo"], keys), 1)}
    gain = lambda n, ks: mean(data[n]["evo"], ks) - mean(data[n]["none"], ks)
    inter = lambda ks: (gain("MG", ks) - gain("MGR", ks)) - (gain("O", ks) - gain("R", ks))
    names = lambda ks: gain("MG", ks) - gain("MGR", ks)
    out["opaque_minus_readable_on_merged"] = {"value": round(100 * names(keys), 1), "ci": ci(names, keys, db)}
    out["interaction_(MG-MGR)-(O-R)"] = {"value": round(100 * inter(keys), 1), "ci": ci(inter, keys, db)}
    return out


def sql_tables(sql):
    return set(re.findall(r"\btb\d{3}\b", C.strip_literals(sql or "")))


def sql_cols(sql):
    return set(re.findall(r"\bcl\d{4}\b", C.strip_literals(sql or "")))


def runs_mg(cond):
    for fold in ("fwd", "rev"):
        for rep in (1, 2, 3):
            d = V4 / "qa" / f"MG_{fold}" / f"{cond}_r{rep}"
            f = sorted(glob.glob(str(d / "*/*/all_results.json")))
            rs = d / "rescore.json"
            if not f or not rs.exists():
                continue
            yield fold, rep, d, json.loads(Path(f[-1]).read_text())["results"], json.loads(rs.read_text())


def table_recall(meta, conds):
    out = {}
    detail = defaultdict(dict)                       # cond -> (fold, qid, rep) -> (found_tables, found_cols, correct)
    for c in conds:
        n = found = correct = 0
        for fold, rep, d, results, rescore in runs_mg(c):
            for r in results:
                k = f"{fold}:{r['question_id']}"
                gold = meta[k]["SQL"]
                gt, gc = sql_tables(gold), sql_cols(gold)
                pt, pc = sql_tables(r.get("pred_sql")), sql_cols(r.get("pred_sql"))
                ok_t = bool(gt) and gt <= pt
                tol = rescore.get(str(r["question_id"]), [0, 0])[1]
                n += 1; found += ok_t; correct += tol
                detail[c][(k, rep)] = (ok_t, bool(gc) and gc <= pc, tol)
        if n:
            out[c] = {"question_runs": n, "final_sql_has_all_gold_tables_pct": round(100 * found / n, 1),
                      "accuracy_tolerant_pct": round(100 * correct / n, 1)}
    split = {}
    for c in ("evo", "doc"):
        rows = [v for v in detail[c].values() if v[0]]
        split[c] = {"runs_with_all_tables": len(rows),
                    "accuracy_given_all_tables_pct": round(100 * sum(v[2] for v in rows) / max(1, len(rows)), 1),
                    "of_which_all_gold_columns_pct": round(100 * sum(v[1] for v in rows) / max(1, len(rows)), 1),
                    "accuracy_given_all_tables_and_columns_pct": round(100 * sum(v[2] for v in rows if v[1]) / max(1, sum(v[1] for v in rows)), 1),
                    "accuracy_given_all_tables_not_all_columns_pct": round(100 * sum(v[2] for v in rows if not v[1]) / max(1, sum(not v[1] for v in rows)), 1)}
    both = [k for k in detail["evo"] if k in detail["doc"] and detail["evo"][k][0] and detail["doc"][k][0]]
    split["both_found_all_tables_same_run_index"] = {
        "pairs": len(both),
        "only_doc_correct": sum(1 for k in both if detail["doc"][k][2] and not detail["evo"][k][2]),
        "only_evo_correct": sum(1 for k in both if detail["evo"][k][2] and not detail["doc"][k][2]),
        "only_doc_correct_and_evo_missed_a_gold_column": sum(1 for k in both if detail["doc"][k][2] and not detail["evo"][k][2]
                                                             and not detail["evo"][k][1])}
    return out, split


def fig10_by_source(meta):
    per = defaultdict(lambda: [0, 0])
    tot = [0, 0]
    for fold, c, rep, d, results, rescore, rdir in S6.runs("MG"):
        if c != "M":
            continue
        for r in results:
            qid = str(r["question_id"])
            tf = glob.glob(str(rdir / r["db_id"] / "traces" / f"{qid}.json*"))
            if not tf:
                continue
            msgs = S6.load_trace(tf[0]).get("messages") or []
            sysmsg = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
            mt, _ = S6.manifest_tables(sysmsg)
            if mt is None:
                continue
            gold = {g.lower() for g in S6.sql_tables(r.get("gold_sql") or "", S6.tables_of("MG", r["db_id"]))}
            cov = bool(gold) and gold <= {t.lower() for t in mt}
            src = meta[f"{fold}:{qid}"]["source_db"]
            per[src][0] += cov; per[src][1] += 1; tot[0] += cov; tot[1] += 1
            listed_all = len(set(re.findall(r"\btb\d{3}\b", sysmsg))) == 60     # every table code of the merged schema
            per["_prompt_lists_other_tables_by_name"][0] += listed_all; per["_prompt_lists_other_tables_by_name"][1] += 1
    res = {k: {"runs": v[1], "all_gold_tables_expanded_pct": round(100 * v[0] / v[1], 1)} for k, v in sorted(per.items())}
    res["_all"] = {"runs": tot[1], "all_gold_tables_expanded_pct": round(100 * tot[0] / max(1, tot[1]), 1)}
    k = "_prompt_lists_other_tables_by_name"
    res[k] = {"runs": res[k]["runs"], "prompts_listing_all_tables_pct": res[k].pop("all_gold_tables_expanded_pct")}
    return res


def turn_limit():
    out = {}
    for c in ("none", "evo", "dm", "doc"):
        at = with_sql = correct = n = 0
        for fold, rep, d, results, rescore in runs_mg(c):
            for r in results:
                n += 1
                if float(r.get("turns") or 0) >= 30:
                    at += 1
                    sql = (r.get("pred_sql") or "").strip()
                    with_sql += bool(sql)
                    correct += rescore.get(str(r["question_id"]), [0, 0])[1] if sql else 0
        out[c] = {"question_runs": n, "reached_30_turns": at, "reached_30_turns_pct": round(100 * at / max(1, n), 1),
                  "of_which_returned_sql": with_sql, "of_which_correct_tolerant": correct,
                  "submitted_before_limit_pct": round(100 * (n - at) / max(1, n), 1)}
    return out


def joins(meta):
    with_eq = same = unresolved_q = 0
    for k, q in meta.items():
        pairs = C.mg_join_pairs(q["SQL"])
        if not pairs:
            continue
        with_eq += 1
        same += all(a == b for a, b in pairs)
        unresolved_q += C.mg_join_pairs_qualified(q["SQL"])[1] > 0
    return {"questions_with_column_equalities": with_eq, "every_equality_between_equal_codes": same,
            "questions_with_an_unresolved_join_end": unresolved_q}


def rebuild_overlap():
    val = {str(q["question_id"]) for q in json.loads(Path(glob.glob(str(EXP / "variants/opaque_merged/questions/minidev/validation/*.json"))[0]).read_text())["questions"]}
    meta = mg_meta()
    a, b = per_question(V4, "MG", "dm"), per_question(V4, "MG", "dm93")
    keys = sorted(a)
    db = {k: meta[k]["source_db"] for k in keys}
    out = {}
    for name, ks in (("all_400", keys), ("without_61_validation", [k for k in keys if not (k.startswith("rev:") and k[4:] in val)]),
                     ("only_61_validation", [k for k in keys if k.startswith("rev:") and k[4:] in val])):
        g = lambda kk: mean(b, kk) - mean(a, kk)
        out[name] = {"n": len(ks), "before": round(100 * mean(a, ks), 1), "after": round(100 * mean(b, ks), 1),
                     "gain": round(100 * g(ks), 1), "ci": ci(g, ks, db)}
    out["validation_in_rev_test"] = sum(1 for k in keys if k.startswith("rev:") and k[4:] in val)
    return out


def main():
    meta = mg_meta()
    res = {"four_cell": four_cell(meta)}
    res["table_recall"], res["table_recall_split"] = table_recall(meta, ["none", "doc", "evo", "evodoc", "dm", "dmdoc", "M",
                                                                         "dm93", "evoA", "dmA"])
    res["fig10_by_source"] = fig10_by_source(meta)
    res["turn_limit"] = turn_limit()
    res["joins"] = joins(meta)
    res["rebuild_overlap"] = rebuild_overlap()
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

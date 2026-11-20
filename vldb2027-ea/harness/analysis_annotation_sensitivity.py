#!/usr/bin/env python3
"""BIRD annotation-error sensitivity (2026-11-20, reviewer request, zero API cost).

Corrected gold SQL: Arcwise-Plat-SQL (Jin et al., github.com/uiuc-kang-lab/text_to_sql_benchmarks,
data/arcwise_plat_sql_only_with_diff.json, CC-BY-SA-4.0; SQL corrected, and for some questions the question text or
evidence rewritten), stored in ext/arcwise_plat_20261120/. Questions used: Mini-Dev test questions whose id is in that
file and whose question text and evidence are identical (whitespace-normalised), so that the stored predictions were made
from the same inputs the corrected SQL assumes; corrected gold queries that fail to execute are dropped. On these, the stored predictions are rescored (strict and column-tolerant EX, rescore_v2 rules) against the
original and the corrected gold, and the paper's key differences are recomputed:
  table1_native     DataMind chain with its ontology (dm-w, dm) - ReAct with EvoOntology (evo-w, react), readable BIRD,
                    protocol v2 units, three repeats, both folds
  mg_evo_minus_none merged opaque schema, protocol v4: evo - none (the +16.1 of Section 5)
For MG the corrected SQL is rewritten to the opaque merged names with make_opaque.rewrite_sql and kept only if it returns
the same rows on the merged database as on the source database (the rule make_opaque_merged.py used).
CI: 95% bootstrap over questions within source database (stats_v4.boot). Output: runs/v4/annotation_sensitivity_20261120.json
"""
import json
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import make_opaque as mo
import rescore_v2 as R
import stats_v4 as SV
import analysis_review_20261120 as AR

EXP = Path(__file__).resolve().parent
AW = EXP / "ext/arcwise_plat_20261120/arcwise_plat_sql_only_with_diff.json"
OUT = EXP / "runs/v4/annotation_sensitivity_20261120.json"
MAP = json.loads((EXP / "variants/opaque_merged/mapping.json").read_text())


def corrected():
    norm = lambda s: re.sub(r"\s+", " ", s.strip())                               # noqa: E731
    return {str(a["question_id"]): a for a in json.loads(AW.read_text())}, norm


def rows_or_none(args):
    path, sql = args
    try:
        return R.execute(path, sql, 1800)     # fwd:701 needs about 3 min alone; 600 s can expire under load
    except Exception:                                                             # noqa: BLE001
        return None


def score_all(pool, items):
    """items: [(db_path, pred_sql, gold_rows)] -> [(strict, tolerant)]"""
    return list(pool.map(R.score_task, items, chunksize=4))


def mean_diff(a, b, keys):
    return sum(a[k] - b[k] for k in keys) / len(keys)


def table1(pool, aw, norm):
    units = {}
    plan = [j for j in json.loads((R.V2 / "plan.json").read_text()) if j["phase"] == "qa"]
    by_out = {j["out"]: j["id"].split("__", 1)[1] for j in plan}
    want = {("dm-w", "dm"): "dm", ("evo-w", "react"): "evo"}
    for j in plan:
        _, d, O, Q, tail = j["id"].split("__")
        S, rep = tail.rsplit("_r", 1)
        if S != "full" or (O, Q) not in want:
            continue
        path = Path(j.get("reuse_of") or j["out"])
        units[(want[(O, Q)], d, int(rep))] = path
    qs = {d: R.questions(d) for d in ("fwd", "rev")}
    keys = sorted(f"{d}:{i}" for d in qs for i, q in qs[d].items()
                  if i in aw and norm(aw[i]["question"]) == norm(q["question"])
                  and norm(aw[i].get("evidence") or "") == norm(q.get("evidence") or ""))
    db = {k: qs[k[:3]][k[4:]]["db_id"] for k in keys}
    gold = {}
    for which in ("original", "corrected"):
        items = [(k, str(R.db_path("full", db[k])), (qs[k[:3]][k[4:]]["SQL"] if which == "original" else aw[k[4:]]["SQL"])) for k in keys]
        res = list(pool.map(rows_or_none, [(p, s) for _, p, s in items]))
        gold[which] = dict(zip(keys, res))
    keys = [k for k in keys if gold["corrected"][k] is not None and gold["original"][k] is not None]
    db = {k: db[k] for k in keys}
    acc = {}
    for (sysname, d, rep), path in units.items():
        sqls, _ = R.unit_sqls("react" if sysname == "evo" else "dm", path)
        ks = [k for k in keys if k.startswith(d + ":")]
        for which in ("original", "corrected"):
            res = score_all(pool, [(str(R.db_path("full", db[k])), sqls.get(k[4:], ""), gold[which][k]) for k in ks])
            for k, (st, tol) in zip(ks, res):
                acc.setdefault((sysname, which, "strict"), {}).setdefault(k, []).append(st)
                acc.setdefault((sysname, which, "tolerant"), {}).setdefault(k, []).append(tol)
    m = {key: {k: sum(v) / len(v) for k, v in d.items()} for key, d in acc.items()}
    out = {"n_questions": len(keys), "gold_changed": sum(1 for k in keys if (gold["original"][k] or []) != (gold["corrected"][k] or [])
                                                          and set(gold["original"][k] or []) != set(gold["corrected"][k] or []))}
    for which in ("original", "corrected"):
        for metric in ("strict", "tolerant"):
            a, b = m[("dm", which, metric)], m[("evo", which, metric)]
            f = lambda ks, a=a, b=b: mean_diff(a, b, ks)                          # noqa: E731
            out[f"dm_minus_evo_{which}_{metric}"] = {"value": round(100 * f(keys), 1), "ci": AR.ci(f, keys, db),
                                                    "dm": round(100 * sum(a[k] for k in keys) / len(keys), 1),
                                                    "evo": round(100 * sum(b[k] for k in keys) / len(keys), 1)}
    return out


def mg(pool, aw, norm):
    meta = AR.mg_meta()
    orig = {i: q for d in ("fwd", "rev") for i, q in R.questions(d).items()}   # original BIRD text (MG evidence is renamed)
    dbpath = str(EXP / "variants/opaque_merged/dev_databases/bird_merged/bird_merged.sqlite")
    keys, gsql = [], {}
    for k, q in meta.items():
        a = aw.get(str(q["question_id"]))
        o0 = orig.get(str(q["question_id"]))
        if not a or not o0 or norm(a["question"]) != norm(o0["question"]) \
                or norm(a.get("evidence") or "") != norm(o0.get("evidence") or ""):
            continue
        src = q["source_db"]
        try:
            new = mo.rewrite_sql(a["SQL"], MAP[src]["tables"], MAP[src]["columns"])
        except Exception:                                                         # noqa: BLE001
            continue
        o, n = rows_or_none((str(mo.SRC / src / f"{src}.sqlite"), a["SQL"])), rows_or_none((dbpath, new))
        if o is None or n is None or Counter(o) != Counter(n):     # multiset, as make_opaque_merged.py
            continue
        keys.append(k); gsql[k] = new
    db = {k: meta[k]["source_db"] for k in keys}
    gold = {"original": dict(zip(keys, pool.map(rows_or_none, [(dbpath, meta[k]["SQL"]) for k in keys]))),
            "corrected": dict(zip(keys, pool.map(rows_or_none, [(dbpath, gsql[k]) for k in keys])))}
    acc = {}
    for cond in ("evo", "none"):
        for fold, rep, d, results, _ in AR.runs_mg(cond):
            sqls = {f"{fold}:{r['question_id']}": r.get("pred_sql") or "" for r in results}
            ks = [k for k in keys if k.startswith(fold + ":")]
            for which in ("original", "corrected"):
                res = score_all(pool, [(dbpath, sqls.get(k, ""), gold[which][k]) for k in ks])
                for k, (_st, tol) in zip(ks, res):
                    acc.setdefault((cond, which), {}).setdefault(k, []).append(tol)
    m = {key: {k: sum(v) / len(v) for k, v in d.items()} for key, d in acc.items()}
    out = {"n_questions": len(keys)}
    for which in ("original", "corrected"):
        a, b = m[("evo", which)], m[("none", which)]
        f = lambda ks, a=a, b=b: mean_diff(a, b, ks)                              # noqa: E731
        out[f"evo_minus_none_{which}_tolerant"] = {"value": round(100 * f(keys), 1), "ci": AR.ci(f, keys, db)}
    return out


def main():
    aw, norm = corrected()
    with ProcessPoolExecutor(8) as pool:
        res = {"source": "Arcwise-Plat-SQL (Jin et al.), data/arcwise_plat_sql_only_with_diff.json, CC-BY-SA-4.0",
               "table1_native": table1(pool, aw, norm), "mg_evo_minus_none": mg(pool, aw, norm)}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

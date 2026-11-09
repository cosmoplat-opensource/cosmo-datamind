#!/usr/bin/env python3
"""v5 re-analysis 2: sensitivity of the MG gains to possible builder prior knowledge (zero API cost).

Purpose: drop MG test questions whose gold SQL touches a code meaning that the prior-knowledge check flags as not
traceable to the construction inputs, and recompute evo-none (the +15.4 headline of the abstract, Section 1 finding 3,
Section 5 and Section 7) and dm-none on the remaining questions.
Background: runs/v4/prior_knowledge_audit.json stores only totals (141 meanings / 70 unsupported / 8 in BIRD docs) and
8 examples. Re-running the same check (functions of audit_prior_knowledge.py; construction workload text =
questions + evidence of variants/*workload*/<splitroot>/<db>.json; stored text values of the variant DB, read-only;
BIRD documentation = variants/*/dictionaries) shows those totals are from EvoOntology's BIRD-opaque (O, per-database)
ontologies, both folds; the MG ontologies are audited here with the same check.
Flagged column set of an unsupported meaning = cl/fld codes named in the constraint's description or scope, plus the
columns of the mappings of the constraint's target term. O-ontology flags are carried to MG codes through the
original column names (variants/opaque/mapping/<db>.json -> variants/opaque_merged/mapping.json).
Drop rules (each reported separately):
  mg_fold_column   : the fold's own MG ontology flags a column the gold SQL uses (the ontology the agent consumed);
  mg_fold_value    : as above and a gold string literal equals the flagged value (or contains it, if >= 4 chars);
  mg_fold_in_doc   : only flags whose meaning was also found in the BIRD documentation, column level;
  union_column     : any column flagged by either MG fold ontology or by any O ontology (mapped), both folds.
Intervals: the paper's MG method (stats_v4.boot, stratified by source db, seed 20261107, 10,000 resamples) on the kept set.
Inputs: runs/v2/evo/w_opaque{,_merged}_{fwd,rev}/<db>/versions/ontology_v0/{constraints,mappings}.json, variant DBs,
        workloads, dictionaries, mappings, runs/v4/qa/MG_* (via stats_v4.load).
Output: runs/v4/analysis_v5_prior.json
Usage: python3 -B analysis_v5_prior.py
Fixed seeds: 20261107. No API/model calls; SQLite opened read-only.
"""
import json
import re
import sqlite3
from pathlib import Path

import numpy as np

import analysis_v5_common as C
import audit_prior_knowledge as A
import stats_v4 as SV

EXP = C.EXP
OUT = C.V4 / "analysis_v5_prior.json"
MGMAP = json.load(open(EXP / "variants/opaque_merged/mapping.json"))


def audit(vdir, wl_path, db, doc):
    """Same rule as audit_prior_knowledge.main, returning the rows."""
    q = json.load(open(wl_path))["questions"]
    wl = A.norm(" ".join(x["question"] + " " + (x.get("evidence") or "") for x in q))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    vals = set()
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for r in con.execute(f'PRAGMA table_info("{t}")'):
            try:
                for (v,) in con.execute(f'SELECT DISTINCT "{r[1]}" FROM "{t}" WHERE typeof("{r[1]}")=\'text\' LIMIT 3000'):
                    vals.add(A.norm(v))
            except sqlite3.Error:
                pass
    con.close()
    doct = A.norm(Path(doc).read_text())
    cons = json.load(open(Path(vdir) / "constraints.json"))
    maps = json.load(open(Path(vdir) / "mappings.json"))
    term_cols = {}
    for m in maps:
        term_cols.setdefault(m.get("term_id"), set()).add(m.get("column"))
    rows = []
    for c in cons:
        if c.get("constraint_type") not in ("enum_semantics", "business_rule"):
            continue
        for v, meaning in A.pairs(c.get("description") or ""):
            w = A.bigrams(meaning)
            if not w:
                continue
            sup = sum(1 for x in w if x in wl or any(x in vv for vv in vals if len(vv) < 200)) / len(w)
            ind = sum(1 for x in w if x in doct) / len(w)
            txt = (c.get("description") or "") + " " + str(c.get("scope") or "")
            cols = set(re.findall(r"\b(?:cl\d{4}|fld\d{3})\b", txt)) | {x for x in term_cols.get(c.get("target"), ()) if x}
            rows.append({"constraint": c["id"], "value": v, "meaning": meaning.strip(), "supported": sup >= 0.5,
                         "in_doc": ind >= 0.5, "columns": sorted(cols)})
    return rows


def o_to_mg(db, fld_cols):
    """Carry BIRD-opaque column codes of database db to merged-schema codes via the original column names."""
    if db not in MGMAP:
        return set()
    inv = {v: k for k, v in json.load(open(EXP / f"variants/opaque/mapping/{db}.json"))["columns"].items()}
    mg = {k.lower(): v for k, v in MGMAP[db]["columns"].items()}
    return {mg[inv[f].lower()] for f in fld_cols if f in inv and inv[f].lower() in mg}


def literals(sql):
    return {m.group(1).replace("''", "'").lower() for m in re.finditer(r"'((?:[^']|'')*)'", sql)}


def main():
    audits = {}
    for S, fold in (("O", "fwd"), ("O", "rev"), ("MG", "fwd"), ("MG", "rev")):
        sr = C.SPLITROOT[fold]
        if S == "O":
            base = EXP / f"runs/v2/evo/w_opaque_{fold}"
            items = [(d.name, base / d.name / "versions/ontology_v0", EXP / f"variants/opaque_workload/{sr}/{d.name}.json",
                      EXP / f"variants/opaque/dev_databases/{d.name}/{d.name}.sqlite", EXP / f"variants/opaque/dictionaries/{d.name}.json")
                     for d in sorted(base.iterdir()) if (d / "versions/ontology_v0/constraints.json").exists()]
        else:
            d = "bird_merged"
            items = [(d, EXP / f"runs/v2/evo/w_opaque_merged_{fold}/{d}/versions/ontology_v0",
                      EXP / f"variants/opaque_merged/workload/{sr}/{d}.json",
                      EXP / f"variants/opaque_merged/dev_databases/{d}/{d}.sqlite", EXP / "variants/opaque_merged/dictionaries/bird_merged.json")]
        rows = []
        for db, *paths in items:
            for r in audit(*map(str, paths)):
                r["db"] = db
                rows.append(r)
        audits[(S, fold)] = rows
    summary = {f"{S}_{fold}": {"pairs": len(r), "unsupported": sum(not x["supported"] for x in r),
                               "unsupported_in_doc": sum((not x["supported"]) and x["in_doc"] for x in r)}
               for (S, fold), r in audits.items()}
    summary["O_both_folds"] = {k: summary["O_fwd"][k] + summary["O_rev"][k] for k in summary["O_fwd"]}
    summary["MG_both_folds"] = {k: summary["MG_fwd"][k] + summary["MG_rev"][k] for k in summary["MG_fwd"]}

    flags = {}
    for fold in ("fwd", "rev"):
        uns = [x for x in audits[("MG", fold)] if not x["supported"]]
        flags[f"mg_{fold}"] = uns
    o_cols = set()
    for fold in ("fwd", "rev"):
        for x in audits[("O", fold)]:
            if not x["supported"]:
                o_cols |= o_to_mg(x["db"], [c for c in x["columns"] if c.startswith("fld")])
    union_cols = o_cols | {c for f in ("fwd", "rev") for x in flags[f"mg_{f}"] for c in x["columns"]}

    acc, db, _ = SV.load("MG")
    gold = {}
    for fold in ("fwd", "rev"):
        for i, q in C.questions("MG", fold).items():
            gold[f"{fold}:{i}"] = q["SQL"]

    def drop(rule, k):
        fold = k.split(":")[0]
        sql = gold[k]
        cols = C.mg_columns(sql)
        if rule == "union_column":
            return bool(cols & union_cols)
        fl = flags[f"mg_{fold}"]
        if rule == "mg_fold_in_doc":
            fl = [x for x in fl if x["in_doc"]]
        hit = [x for x in fl if cols & set(x["columns"])]
        if rule == "mg_fold_value":
            lits = literals(sql)
            hit = [x for x in hit if any(l == x["value"].lower() or (len(x["value"]) >= 4 and x["value"].lower() in l)
                                         for l in lits)]
        return bool(hit)

    res = {"audit_totals": summary,
           "note_audit": "paper's 141/70/8 corresponds to O_both_folds (BIRD-opaque per-database ontologies); "
                         "this re-run uses questions+evidence as workload text",
           "mg_flagged_meanings": {f: [{k: x[k] for k in ("value", "meaning", "in_doc", "columns")} for x in v]
                                   for f, v in flags.items()},
           "o_flagged_columns_mapped_to_mg": sorted(o_cols), "union_columns": sorted(union_cols), "rules": {}}
    for rule in ("none_dropped", "mg_fold_column", "mg_fold_value", "mg_fold_in_doc", "union_column"):
        kept = [k for k in db if rule == "none_dropped" or not drop(rule, k)]
        dropped = [k for k in db if k not in set(kept)]
        sub = {k: db[k] for k in kept}
        idx = C.index_samples(sub, False)
        r = {"n_kept": len(kept), "n_dropped": len(dropped),
             "dropped_by_fold": {f: sum(k.startswith(f) for k in dropped) for f in ("fwd", "rev")}}
        for a, b in (("evo", "none"), ("dm", "none"), ("doc", "none"), ("evo", "dm")):
            for j, nm in ((1, "tolerant"), (0, "strict")):
                v = np.array([acc[a][k][j] - acc[b][k][j] for k in sub])
                r[f"{a}-{b}:{nm}"] = {"diff": round(100 * v.mean(), 1), "ci": C.ci(C.dist(v, idx))}
                if dropped and nm == "tolerant":
                    r[f"{a}-{b}:{nm}:dropped_only_point"] = round(100 * np.mean([acc[a][k][j] - acc[b][k][j] for k in dropped]), 1)
        res["rules"][rule] = r
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({"audit_totals": summary, "rules": res["rules"]}, ensure_ascii=False, indent=1))
    print("MG flagged:", json.dumps(res["mg_flagged_meanings"], ensure_ascii=False))
    print("O cols mapped:", len(o_cols), "union:", len(union_cols))


if __name__ == "__main__":
    main()

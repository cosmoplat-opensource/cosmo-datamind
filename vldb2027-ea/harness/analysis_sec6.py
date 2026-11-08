#!/usr/bin/env python3
"""Numbers of paper Sections 6.3-6.5 from the protocol-v4 test runs (runs/v4/qa), both folds, three repeats.

6.3 ontology tool use: per setting and condition, share of question-runs with >=1 ontology tool call, mean number of
    ontology tool calls, and column-tolerant accuracy of question-runs with and without a call (descriptive: the agent
    chooses when to call, so the two groups are not comparable samples).
6.4 compiled injection: for condition M, the tables named in the compiled excerpt (system prompt, <semantic_manifest>)
    against the tables of the gold query; share of question-runs whose excerpt contains every gold table, number of
    tables in the excerpt, and accuracy with and without full coverage.
6.5 interaction cost: mean turns per question-run, share of runs reaching the 30-turn limit, and mean length of the ontology text the agent receives (compiled
    excerpt, or the observations returned by ontology tools), in characters.
Writes runs/v4/analysis_sec6.json.
"""
import glob
import gzip
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

EXP = Path(__file__).resolve().parent
V4 = EXP / "runs/v4/qa"
CONDS = ["none", "doc", "evo", "evodoc", "dm", "dmdoc", "M"]
DBROOT = {"R": EXP / "EvoOntology/benchmarks/bird/data/mini_dev_data/dev_databases",
          "O": EXP / "variants/opaque/dev_databases", "MG": EXP / "variants/opaque_merged/dev_databases"}
_tables = {}


def tables_of(S, db):
    if (S, db) not in _tables:
        con = sqlite3.connect(f"file:{DBROOT[S] / db / (db + '.sqlite')}?mode=ro", uri=True)
        _tables[(S, db)] = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        con.close()
    return _tables[(S, db)]


def sql_tables(sql, names):
    s = sql.lower()
    return {t for t in names if re.search(r'(?<![\w])["`\[]?' + re.escape(t.lower()) + r'["`\]]?(?![\w])', s)}


def load_trace(f):
    return json.load(gzip.open(f) if f.endswith(".gz") else open(f))


def runs(S):
    for fold in ("fwd", "rev"):
        for c in CONDS:
            for rep in (1, 2, 3):
                d = V4 / f"{S}_{fold}" / f"{c}_r{rep}"
                res = sorted(glob.glob(str(d / "*/*/all_results.json")))[-1]
                yield fold, c, rep, d, json.load(open(res))["results"], json.load(open(d / "rescore.json")), Path(res).parent


def manifest_tables(text):
    m = re.search(r"<semantic_manifest>(.*?)</semantic_manifest>", text, re.S)
    if not m:
        return None, 0
    body = m.group(1)
    return set(re.findall(r"表 ([^\s(（:]+)[(（]", body)), len(body)


def ontology_obs_chars(msgs):
    n = 0
    for k, s in enumerate(msgs):
        tc = s.get("tool_call") or {}
        if tc.get("tool") in ("browse_semantics", "resolve_semantics") and k + 1 < len(msgs):
            n += len(str(msgs[k + 1].get("content") or ""))
    return n


def main():
    out = {}
    for S in ("R", "O", "MG"):
        agg = defaultdict(lambda: defaultdict(float))
        for fold, c, rep, d, results, rescore, rdir in runs(S):
            a = agg[c]
            for r in results:
                qid = str(r["question_id"])
                tol = rescore.get(qid, [0, 0])[1]
                calls = float(r.get("semantic_tool_calls") or 0)
                a["n"] += 1; a["turns"] += float(r.get("turns") or 0); a["acc"] += tol
                a["at_limit"] += float(r.get("turns") or 0) >= 30
                a["calls"] += calls
                if calls > 0:
                    a["n_call"] += 1; a["acc_call"] += tol
                else:
                    a["n_nocall"] += 1; a["acc_nocall"] += tol
                need_trace = c == "M" or c in ("evo", "dm", "evodoc", "dmdoc")
                if not need_trace:
                    continue
                tf = glob.glob(str(rdir / r["db_id"] / "traces" / f"{qid}.json*"))
                if not tf:
                    continue
                msgs = load_trace(tf[0]).get("messages") or []
                if c == "M":
                    sysmsg = next((m.get("content") or "" for m in msgs if m.get("role") == "system"), "")
                    mt, ln = manifest_tables(sysmsg)
                    if mt is None:
                        a["no_manifest"] += 1; continue
                    names = tables_of(S, r["db_id"])
                    gold = sql_tables(r.get("gold_sql") or "", names)
                    gold_l, mt_l = {g.lower() for g in gold}, {t.lower() for t in mt}
                    cov = bool(gold_l) and gold_l <= mt_l
                    a["m_n"] += 1; a["m_tables"] += len(mt); a["m_chars"] += ln
                    a["m_cov"] += cov; a["m_acc_cov"] += tol if cov else 0; a["m_acc_nocov"] += 0 if cov else tol
                else:
                    a["obs_n"] += 1; a["obs_chars"] += ontology_obs_chars(msgs)
        res = {}
        for c, a in agg.items():
            x = {"question_runs": int(a["n"]), "mean_turns": round(a["turns"] / a["n"], 1),
                 "turn_limit_pct": round(100 * a["at_limit"] / a["n"], 1),
                 "tolerant_acc": round(100 * a["acc"] / a["n"], 1)}
            if c in ("evo", "dm", "evodoc", "dmdoc"):
                x.update(tool_use_pct=round(100 * a["n_call"] / a["n"], 1), mean_tool_calls=round(a["calls"] / a["n"], 2),
                         acc_with_call=round(100 * a["acc_call"] / a["n_call"], 1) if a["n_call"] else None,
                         acc_without_call=round(100 * a["acc_nocall"] / a["n_nocall"], 1) if a["n_nocall"] else None,
                         mean_ontology_chars=round(a["obs_chars"] / a["obs_n"]) if a["obs_n"] else None)
            if c == "M" and a["m_n"]:
                cov_n = a["m_cov"]
                x.update(excerpt_question_runs=int(a["m_n"]), missing_excerpt=int(a["no_manifest"]),
                         mean_tables_in_excerpt=round(a["m_tables"] / a["m_n"], 1), mean_excerpt_chars=round(a["m_chars"] / a["m_n"]),
                         gold_tables_covered_pct=round(100 * cov_n / a["m_n"], 1),
                         acc_covered=round(100 * a["m_acc_cov"] / cov_n, 1) if cov_n else None,
                         acc_not_covered=round(100 * a["m_acc_nocov"] / (a["m_n"] - cov_n), 1) if a["m_n"] > cov_n else None)
            res[c] = x
        out[S] = res
        print(S, json.dumps(res, ensure_ascii=False))
    (EXP / "runs/v4/analysis_sec6.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

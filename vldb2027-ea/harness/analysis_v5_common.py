#!/usr/bin/env python3
"""Shared helpers for the v5 zero-API re-analyses (analysis_v5_strat.py, analysis_v5_prior.py, analysis_v5_robust.py).

Purpose: reuse the exact resampling of stats_v4.py (same seed 20261107, same 10,000 resamples, same database-cluster
bootstrap for R/O and source-database-stratified question bootstrap for MG, same percentile rule) and add vectorised
means, two-sided bootstrap p-values and loaders for the protocol-v4 question records, traces and SQL.
Inputs: runs/v4/qa/<S>_<fold>/<cond>_r<rep>/{rescore.json, */*/all_results.json, */*/<db>/traces/<qid>.json[.gz]},
        question files under EvoOntology/benchmarks/bird/data/<splitroot>/test and variants/*/questions/<splitroot>/test.
Outputs: none (library).
Usage: import analysis_v5_common as C
Fixed seeds: bootstrap seed 20261107 (taken from stats_v4.boot); B = 10,000.
No API or model calls; SQLite is only ever opened read-only (uri mode=ro).
"""
import ast
import glob
import gzip
import json
import re
import sqlite3
import time
from pathlib import Path

import numpy as np

import stats_v4 as SV

EXP = SV.EXP
V4 = SV.V4
QA = V4 / "qa"
B = SV.B
SEED = 20261107
CLUSTER = {"R": True, "O": True, "MG": False}
DBROOT = {"R": EXP / "EvoOntology/benchmarks/bird/data/mini_dev_data/dev_databases",
          "O": EXP / "variants/opaque/dev_databases", "MG": EXP / "variants/opaque_merged/dev_databases"}
SPLITROOT = {"fwd": "minidev", "rev": "minidev_rev"}
ONTO_TOOLS = ("browse_semantics", "resolve_semantics")


# ----------------------------------------------------------------------------- bootstrap
def index_samples(db, cluster):
    """Same resamples as stats_v4.boot (seed 20261107), returned as numpy index arrays into list(db)."""
    samples = SV.boot(None, db, cluster)
    pos = {k: i for i, k in enumerate(db)}
    return [np.fromiter((pos[k] for k in ks), dtype=np.int32) for ks in samples]


def dist(vec, idxs):
    """Bootstrap distribution (points) of the mean of a per-question vector (values in [0,1] or differences)."""
    return np.array([vec[ix].mean() * 100 for ix in idxs])


def ci(vals, level=0.95):
    """Percentile interval with the indexing rule of stats_v4.summarize.ci (level 0.95) or its 90% analogue."""
    v = np.sort(vals)
    lo = (1 - level) / 2
    return [round(float(v[int(lo * B)]), 1), round(float(v[int((1 - lo) * B) - 1]), 1)]


def pval(vals):
    """Two-sided bootstrap p-value for H0: difference = 0, from the paired bootstrap distribution:
    p = min(1, 2 * min((#{d*<=0}+1)/(B+1), (#{d*>=0}+1)/(B+1)))  (percentile / sign-inversion method)."""
    le = (np.sum(vals <= 0) + 1) / (B + 1)
    ge = (np.sum(vals >= 0) + 1) / (B + 1)
    return float(min(1.0, 2 * min(le, ge)))


def ratio_dist(num, den, idxs):
    """Bootstrap distribution (points) of sum(num)/sum(den) over resampled questions (question-run level ratios)."""
    out = np.empty(len(idxs))
    for i, ix in enumerate(idxs):
        d = den[ix].sum()
        out[i] = 100 * num[ix].sum() / d if d else np.nan
    return out


def ci_nan(vals, level=0.95):
    v = vals[~np.isnan(vals)]
    if len(v) < 0.9 * B:
        return None
    v = np.sort(v)
    lo = (1 - level) / 2
    return [round(float(v[int(lo * len(v))]), 1), round(float(v[int((1 - lo) * len(v)) - 1]), 1)]


# ----------------------------------------------------------------------------- runs
def run_dir(S, fold, cond, rep):
    return QA / f"{S}_{fold}" / f"{cond}_r{rep}"


def results_file(d):
    return Path(sorted(glob.glob(str(d / "*/*/all_results.json")))[-1])


def run_results(S, fold, cond, rep):
    """(results list keyed by qid, rescore dict qid -> [strict, tolerant], directory of all_results.json)."""
    d = run_dir(S, fold, cond, rep)
    f = results_file(d)
    res = {str(r["question_id"]): r for r in json.load(open(f))["results"]}
    return res, json.load(open(d / "rescore.json")), f.parent


def questions(S, fold):
    sr = SPLITROOT[fold]
    root = EXP / "EvoOntology/benchmarks/bird/data" / sr / "test" if S == "R" else \
        EXP / f"variants/{'opaque' if S == 'O' else 'opaque_merged'}/questions/{sr}/test"
    out = {}
    for f in root.glob("*.json"):
        for q in json.loads(f.read_text())["questions"]:
            out[str(q["question_id"])] = q
    return out


def load_trace(rdir, db_id, qid):
    tf = glob.glob(str(rdir / db_id / "traces" / f"{qid}.json*"))
    if not tf:
        return None
    f = tf[0]
    return json.load(gzip.open(f) if f.endswith(".gz") else open(f))


def parse_tool_call(tc):
    if isinstance(tc, dict):
        return tc
    try:
        return ast.literal_eval(tc)
    except Exception:  # noqa: BLE001
        return {}


def ontology_responses(msgs):
    """Ontology tool responses in a trace. The trace is a list of messages with role system/agent/environment;
    an agent message carries 'tool_call' (a Python-repr string {'tool': name, 'arguments': {...}}) and the next
    environment message carries the result ('content' = "Tool '<name>' result:\\n<json>", 'tool_result' = <json>).
    We take every environment message that immediately follows an agent call to browse_semantics/resolve_semantics,
    and also check that its content starts with "Tool '<that name>' result". Returns list of (tool, text)."""
    out = []
    for k, m in enumerate(msgs):
        if m.get("role") != "agent" or not m.get("tool_call"):
            continue
        tool = parse_tool_call(m["tool_call"]).get("tool")
        if tool not in ONTO_TOOLS or k + 1 >= len(msgs):
            continue
        nxt = msgs[k + 1]
        txt = str(nxt.get("tool_result") or nxt.get("content") or "")
        if nxt.get("role") == "environment" and str(nxt.get("content") or "").startswith(f"Tool '{tool}' result"):
            out.append((tool, txt))
    return out


# ----------------------------------------------------------------------------- SQL
CODE_COL = re.compile(r"\bcl\d{4}\b")
CODE_TAB = re.compile(r"\btb\d{3}\b")


def strip_literals(sql):
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def mg_tables(sql):
    return set(CODE_TAB.findall(strip_literals(sql)))


def mg_columns(sql):
    return set(CODE_COL.findall(strip_literals(sql)))


def mg_join_pairs(sql):
    """Column-equality join conditions between two column references: x.clA = y.clB (ON or WHERE)."""
    s = strip_literals(sql)
    pairs = set()
    for m in re.finditer(r"(?:\b\w+\.)?(cl\d{4})\s*=\s*(?:\b\w+\.)?(cl\d{4})\b", s):
        a, b = m.group(1), m.group(2)
        pairs.add(tuple(sorted((a, b))))
    return pairs


def has_join_kw(sql):
    return bool(re.search(r"\bjoin\b", strip_literals(sql), re.I))


def sql_width(path, sql, timeout=30):
    """Number of result columns of a query, read-only, via SELECT * FROM (<sql>) LIMIT 0 (no rows computed);
    falls back to executing the statement itself (first step only) under a progress-handler timeout."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    deadline = time.time() + timeout
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    try:
        for q in (f"SELECT * FROM ({sql}) LIMIT 0", sql):
            try:
                cur = con.execute(q)
                return len(cur.description) if cur.description else None
            except sqlite3.Error:
                continue
        return None
    finally:
        con.close()

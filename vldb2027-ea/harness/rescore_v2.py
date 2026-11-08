#!/usr/bin/env python3
"""Protocol v2 re-scoring: strict and column-tolerant execution accuracy for every QA unit in runs/v2/plan.json.

strict:   set(pred rows) == set(gold rows)                      (the original scorer's rule)
tolerant: some ordered choice of len(gold columns) distinct prediction columns, projected,
          gives set(projected rows) == set(gold rows)            (extra columns such as an aggregate next to
          the asked-for name are allowed; missing or wrong columns are not)

Only the first SQL of a DataMind answer is scored (ex_first), the ReAct agent's pred_sql otherwise.
Each unit is executed on the database variant it used (variants/<S>/dev_databases for S != full).
Writes runs/v2/rescore/<unit>.json ({qid: [strict, tolerant]}) and runs/v2/rescore/check.json, which compares
the recomputed strict score with the stored one per unit.

usage: python3 rescore_v2.py [--workers 8] [--only SUBSTRING]
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import pickle
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate_v2 import DATA, SPLITROOT, V2, read_dm, read_react  # noqa: E402

EXP = Path(__file__).resolve().parent
FULL = DATA / "mini_dev_data" / "dev_databases"
OUT = V2 / "rescore"
PRED_TIMEOUT, GOLD_TIMEOUT, MAX_PRED_COLS = 60, 600, 8


def db_path(S, db):
    root = FULL if S == "full" else EXP / "variants" / S / "dev_databases"
    return root / db / f"{db}.sqlite"


def execute(path, sql, timeout):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    deadline = time.time() + timeout
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)
    try:
        cur = con.execute(sql)
        return cur.fetchall()
    finally:
        con.close()


def width(rows):
    return len(rows[0]) if rows else None


def tolerant_match(pred, gold):
    gset = set(gold)
    if set(pred) == gset:
        return True
    gw, pw = width(gold), width(pred)
    if gw is None or pw is None or pw <= gw or pw > MAX_PRED_COLS:
        return False
    for cols in itertools.permutations(range(pw), gw):
        if {tuple(r[c] for c in cols) for r in pred} == gset:
            return True
    return False


def score_task(args):
    path, sql, gold = args
    if gold is None or not sql or not str(sql).strip():
        return 0, 0
    try:
        rows = execute(path, sql, PRED_TIMEOUT)
    except Exception:  # noqa: BLE001
        return 0, 0
    return int(set(rows) == set(gold)), int(tolerant_match(rows, gold))


def gold_task(args):
    key, path, sql = args
    try:
        return key, execute(path, sql, GOLD_TIMEOUT)
    except Exception:  # noqa: BLE001
        return key, None


def questions(direction):
    out = {}
    for f in (DATA / SPLITROOT[direction] / "test").glob("*.json"):
        for q in json.loads(f.read_text())["questions"]:
            out[str(q["question_id"])] = q
    return out


def unit_sqls(Q, path):
    """{qid: first SQL}, plus the stored strict score for the check."""
    sqls = {}
    if Q == "react":
        files = sorted(Path(path).glob("*/*/all_results.json"))
        for r in json.loads(files[-1].read_text())["results"]:
            sqls[str(r["question_id"])] = r.get("pred_sql") or ""
        stored, _ = read_react(path)
    else:
        for f in sorted(Path(path).glob("*.json")):
            if f.name in {"meta.json", "repair_log.json"} or f.stem.count(".") or any(x in f.stem for x in ("before_repair", "backup", "aborted")):
                continue
            rows = json.loads(f.read_text())
            if isinstance(rows, list):
                for r in rows:
                    s = r.get("sqls") or []
                    sqls[str(r["question_id"])] = s[0] if s else ""
        stored, _ = read_dm(path)
    return sqls, stored


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    plan = [j for j in json.loads((V2 / "plan.json").read_text()) if j["phase"] == "qa" and args.only in j["id"]]
    gold_cache_file = OUT / "_gold.pkl"
    gold = pickle.loads(gold_cache_file.read_bytes()) if gold_cache_file.exists() else {}
    check = json.loads((OUT / "check.json").read_text()) if (OUT / "check.json").exists() else {}
    qcache = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for j in plan:
            unit = j["id"].split("__", 1)[1]
            target = OUT / f"{unit}.json"
            if target.exists():
                continue
            _, d, O, Q, tail = j["id"].split("__")
            S, rep = tail.rsplit("_r", 1)
            path = Path(j.get("reuse_of") or j["out"])
            if not path.exists():
                print("missing", unit, flush=True); continue
            qs = qcache.setdefault(d, questions(d))
            sqls, stored = unit_sqls(Q, path)
            ids = sorted(qs)
            need = [(f"{S}|{qs[i]['db_id']}|{i}", str(db_path(S, qs[i]["db_id"])), qs[i]["SQL"]) for i in ids
                    if f"{S}|{qs[i]['db_id']}|{i}" not in gold]
            for key, rows in pool.map(gold_task, need):
                gold[key] = rows
            if need:
                tmp = gold_cache_file.with_suffix(".tmp"); tmp.write_bytes(pickle.dumps(gold)); tmp.replace(gold_cache_file)
            tasks = [(str(db_path(S, qs[i]["db_id"])), sqls.get(i, ""), gold.get(f"{S}|{qs[i]['db_id']}|{i}")) for i in ids]
            res = dict(zip(ids, pool.map(score_task, tasks, chunksize=4)))
            target.write_text(json.dumps(res))
            agree = sum(1 for i in ids if i in stored and stored[i] == res[i][0])
            check[unit] = {"n": len(ids), "stored_strict": sum(stored.get(i, 0) for i in ids), "recomputed_strict": sum(r[0] for r in res.values()),
                           "tolerant": sum(r[1] for r in res.values()), "agreement": agree, "missing_sql": sum(1 for i in ids if not sqls.get(i))}
            (OUT / "check.json").write_text(json.dumps(check, indent=1, ensure_ascii=False))
            print(unit, check[unit], flush=True)


if __name__ == "__main__":
    main()

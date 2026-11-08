#!/usr/bin/env python3
"""Protocol v3 re-scoring with the protocol-v2 rules (rescore_v2.py): every finished DataMind-chain unit under
runs/v3/qa/<dir>/<O>__dm__<S>__<cond>_r<rep>. Writes runs/v3/rescore_v3.json {"<dir>__<O>__dm__<S>__<cond>_r<rep>":
{qid: [strict, tolerant]}} (existing entries are kept) and prints stored vs recomputed strict per unit.
usage: python3 rescore_v3.py [--workers 8] [--only SUBSTRING]"""
import argparse, json, pickle
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import rescore_v2 as R

EXP = Path(__file__).resolve().parent
QA, OUTF = EXP / "runs/v3/qa", EXP / "runs/v3/rescore_v3.json"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workers", type=int, default=8); ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = json.loads(OUTF.read_text()) if OUTF.exists() else {}
    gold_f = R.OUT / "_gold.pkl"
    gold = pickle.loads(gold_f.read_bytes()) if gold_f.exists() else {}
    qcache = {}
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for path in sorted(QA.glob("*/*__dm__*_r[123]")):
            d, unit = path.parent.name, path.name
            key = f"{d}__{unit}"
            if key in out or a.only not in key:
                continue
            S = unit.split("__")[2]
            qs = qcache.setdefault(d, R.questions(d))
            sqls, stored = R.unit_sqls("dm", path)
            ids = sorted(qs)
            if sum(1 for i in ids if i in sqls) < len(ids):
                print("incomplete", key, flush=True); continue
            need = [(f"{S}|{qs[i]['db_id']}|{i}", str(R.db_path(S, qs[i]["db_id"])), qs[i]["SQL"]) for i in ids
                    if f"{S}|{qs[i]['db_id']}|{i}" not in gold]
            for k, rows in pool.map(R.gold_task, need):
                gold[k] = rows
            tasks = [(str(R.db_path(S, qs[i]["db_id"])), sqls.get(i, ""), gold.get(f"{S}|{qs[i]['db_id']}|{i}")) for i in ids]
            res = dict(zip(ids, pool.map(R.score_task, tasks, chunksize=4)))
            out[key] = res
            OUTF.write_text(json.dumps(out))
            print(key, "stored", sum(stored.get(i, 0) for i in ids), "recomputed", sum(r[0] for r in res.values()),
                  "tolerant", sum(r[1] for r in res.values()), flush=True)


if __name__ == "__main__":
    main()

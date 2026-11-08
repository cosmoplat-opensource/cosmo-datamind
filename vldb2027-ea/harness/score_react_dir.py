#!/usr/bin/env python3
"""Score ReAct output directories with the protocol rules (strict set EX and column-tolerant EX).
usage: score_react_dir.py <splitroot> <split> <variant S: full|nofk|bare> <out_dir> [<out_dir> ...]"""
import json, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import rescore_v2 as R


def main():
    sr, split, S, dirs = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
    qdir = (Path(sr) if sr.startswith("/") else R.DATA / sr) / split          # absolute path: a variant's rewritten questions
    qs = {str(q["question_id"]): q for f in qdir.glob("*.json") for q in json.loads(f.read_text())["questions"]}
    with ProcessPoolExecutor(8) as pool:
        gold = dict(pool.map(R.gold_task, [(i, str(R.db_path(S, q["db_id"])), q["SQL"]) for i, q in qs.items()]))
        for d in dirs:
            f = sorted(Path(d).glob("*/*/all_results.json"))[-1]
            rs = {str(r["question_id"]): r.get("pred_sql") or "" for r in json.loads(f.read_text())["results"]}
            ids = sorted(qs)
            res = list(pool.map(R.score_task, [(str(R.db_path(S, qs[i]["db_id"])), rs.get(i, ""), gold[i]) for i in ids]))
            out = dict(zip(ids, res))
            (Path(d) / "rescore.json").write_text(json.dumps(out))
            print(f"{Path(d).name:28s} n={len(ids)} answered={sum(1 for i in ids if rs.get(i))} strict={sum(r[0] for r in res)} tolerant={sum(r[1] for r in res)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Per-database breakdown of the protocol-v4 test results (Figure 6 of the PVLDB draft).

For R and O (11 BIRD Mini-Dev databases) and MG (the nine source databases of the merged schema): number of test
questions, number of tables of the source database, column-tolerant accuracy of the agent without ontology, with
documentation, with EvoOntology and with the DataMind ontology, and the three gains over the agent without ontology,
each with a 95% question-level bootstrap interval within the database (10,000 resamples). Accuracy per question is the
mean over the three repeats, both folds pooled, exactly as in stats_v4.py.
usage: python3 analysis_perdb.py  -> runs/v4/analysis_perdb.json
"""
import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

from stats_v4 import V4, load

EXP = Path(__file__).resolve().parent
DBS = EXP / "EvoOntology/benchmarks/bird/data/mini_dev_data/dev_databases"
B = 10000
CONDS = ("none", "doc", "evo", "dm")


def n_tables(db):
    p = DBS / db / f"{db}.sqlite"
    con = sqlite3.connect(p.absolute().as_uri() + "?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchone()[0]
    finally:
        con.close()


def per_db(S, seed=20261109):
    acc, db, _ = load(S)
    groups = defaultdict(list)
    for k, d in db.items():
        groups[d].append(k)
    rnd = random.Random(seed)
    out = {}
    for d in sorted(groups):
        ks = groups[d]
        mean = {c: 100 * sum(acc[c][k][1] for k in ks) / len(ks) for c in CONDS}
        samples = [[rnd.choice(ks) for _ in ks] for _ in range(B)]
        gains = {}
        for c in ("doc", "evo", "dm"):
            vals = sorted(100 * sum(acc[c][k][1] - acc["none"][k][1] for k in s) / len(s) for s in samples)
            gains[c] = {"diff": round(mean[c] - mean["none"], 1),
                        "ci": [round(vals[int(0.025 * B)], 1), round(vals[int(0.975 * B) - 1], 1)]}
        out[d] = {"n": len(ks), "tables": n_tables(d), "acc": {c: round(v, 1) for c, v in mean.items()}, "gain": gains}
    return out


def main():
    res = {S: per_db(S) for S in ("R", "O", "MG")}
    (V4 / "analysis_perdb.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    for S, rows in res.items():
        print(S)
        for d, r in sorted(rows.items(), key=lambda x: x[1]["tables"]):
            g = r["gain"]
            print(f"  {d:26s} n={r['n']:3d} tables={r['tables']:2d} none={r['acc']['none']:5.1f}"
                  f" doc{g['doc']['diff']:+6.1f} evo{g['evo']['diff']:+6.1f} {g['evo']['ci']} dm{g['dm']['diff']:+6.1f}")


if __name__ == "__main__":
    main()

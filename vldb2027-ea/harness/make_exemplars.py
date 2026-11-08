#!/usr/bin/env python3
"""从冻结划分的构建侧题目(问题 + evidence + 标准 SQL)生成 DataMind 已验证问答示例(DR-084)。
开发用只取 construction;测试用取 construction + validation(与 DAIL-SQL 的示例池相同,均不含测试题)。
用法: python3 make_exemplars.py <splitroot> <construction|construction+validation> <out_dir>
"""
import json
import os
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
BIRD = EXP / "EvoOntology" / "benchmarks" / "bird"
DBDIR = BIRD / "data" / "mini_dev_data" / "dev_databases"

root, parts, out = sys.argv[1], sys.argv[2].split("+"), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
by_db = {}
for part in parts:
    for f in (BIRD / "data" / root / part).glob("*.json"):
        for q in json.load(open(f))["questions"]:
            text = q["question"] + (f"\nEvidence: {q['evidence']}" if q.get("evidence") else "")
            by_db.setdefault(q["db_id"], []).append({"question": text, "sql": q["SQL"], "qid": q["question_id"]})
idx = {}
for db, rows in by_db.items():
    (out / f"{db}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    idx[os.path.realpath(DBDIR / db / f"{db}.sqlite")] = f"{db}.jsonl"
(out / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1))
print(root, parts, {d: len(r) for d, r in by_db.items()})

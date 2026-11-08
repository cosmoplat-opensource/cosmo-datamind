#!/usr/bin/env python3
"""把 BIRD 各库的 database_description(每表一个 CSV:原列名、列说明、数据格式、取值说明)转成 DataMind 外部数据字典(DR-083),
登记到实验 workdir 的 dictionaries/index.json。数据库文件本身不改。
用法: python3 bird_dict.py [workdir]
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
from pathlib import Path

EXP = Path(__file__).resolve().parent
DESC = EXP / "data" / "dev_20240627" / "dev_databases"
DBDIR = EXP / "EvoOntology" / "benchmarks" / "bird" / "data" / "mini_dev_data" / "dev_databases"


def read_csv(p: Path):
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(enc))))
        except UnicodeDecodeError:
            continue
    return []


def main():
    work = Path(sys.argv[1]) if len(sys.argv) > 1 else EXP / "runs" / "datamind" / "workdir"
    out_dir = work / "dictionaries"
    out_dir.mkdir(parents=True, exist_ok=True)
    idx_f = out_dir / "index.json"
    idx = json.loads(idx_f.read_text()) if idx_f.exists() else {}
    for ddir in sorted(DESC.iterdir()):
        dd = ddir / "database_description"
        if not dd.is_dir():
            continue
        tables = {}
        for f in dd.glob("*.csv"):
            cols = {}
            for r in read_csv(f):
                r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
                col = r.get("original_column_name")
                if not col:
                    continue
                desc = r.get("column_description") or r.get("column_name") or ""
                if desc.lower() == col.lower():
                    desc = r.get("column_name") if (r.get("column_name") or "").lower() != col.lower() else ""
                vals = r.get("value_description") or ""
                if desc or vals:
                    cols[col] = {"desc": desc, "values": vals}
            tables[f.stem.lower()] = cols
        name = f"{ddir.name}.json"
        (out_dir / name).write_text(json.dumps({"source": "BIRD database_description", "tables": tables},
                                               ensure_ascii=False, indent=1))
        db = DBDIR / ddir.name / f"{ddir.name}.sqlite"
        if db.exists():
            idx[os.path.realpath(db)] = name
        print(ddir.name, len(tables), sum(len(c) for c in tables.values()))
    idx_f.write_text(json.dumps(idx, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

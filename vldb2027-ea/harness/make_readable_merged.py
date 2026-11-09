#!/usr/bin/env python3
"""BIRD-readable-merged (MG-R, protocol v5): the nine BIRD Mini-Dev databases of BIRD-opaque-merged in ONE SQLite schema
with their original table and column names, to separate the size of the merged schema from the opacity of its names.

The nine databases have no table name in common, so tables and columns keep their original names and the gold SQL is
unchanged; every question is pointed at db_id "bird_merged" and its gold SQL must return the same multiset of rows on the
merged database as on the original database. The question sets are those kept in BIRD-opaque-merged
(variants/opaque_merged/questions), so the two merged settings have identical questions. Keys, types and data are copied
unchanged. Outputs under variants/readable_merged/: dev_databases/bird_merged/bird_merged.sqlite,
dictionaries/bird_merged.json (BIRD documentation, keys unchanged), questions/<sr>/<split>/bird_merged.json,
workload/<sr>/bird_merged.json (construction questions with evidence, no SQL), verify.json.
usage: python3 make_readable_merged.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import make_opaque as mo
from make_opaque_merged import DBS, NAME

OUT = mo.EXP / "variants" / "readable_merged"
OPQ = mo.EXP / "variants" / "opaque_merged"


def main():
    dest = OUT / "dev_databases" / NAME / f"{NAME}.sqlite"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    con = sqlite3.connect(dest)
    dictionary, seen = {"source": "BIRD database_description, keys unchanged", "tables": {}}, set()
    for db in DBS:
        src = mo.SRC / db / f"{db}.sqlite"
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        tables = [r[0] for r in s.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
        ddls = {t: s.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0] for t in tables}
        s.close()
        assert not (seen & {t.lower() for t in tables}), "table name shared by two source databases"
        seen |= {t.lower() for t in tables}
        con.execute("ATTACH DATABASE ? AS src", (str(src),))
        for t in tables:
            con.execute(ddls[t])
            con.execute(f"INSERT INTO {mo.q(t)} SELECT * FROM src.{mo.q(t)}")
        con.commit()
        con.execute("DETACH DATABASE src")
        dfile = mo.DICT / f"{db}.json"
        if dfile.exists():
            for t, cols in (json.loads(dfile.read_text()).get("tables") or {}).items():
                dictionary["tables"][t] = cols
        print(db, len(tables), "tables", flush=True)
    con.execute("VACUUM")
    con.close()
    (OUT / "dictionaries").mkdir(parents=True, exist_ok=True)
    (OUT / "dictionaries" / f"{NAME}.json").write_text(json.dumps(dictionary, ensure_ascii=False, indent=1))
    report = {}
    for sr, splits in mo.SPLITS.items():
        for sp in splits:
            kept, dropped = [], []
            if sp == "construction":
                for qq in json.loads((OPQ / "workload" / sr / f"{NAME}.json").read_text())["questions"]:
                    orig = json.loads((mo.DATA / sr / sp / f"{qq['source_db']}.json").read_text())["questions"]
                    o = next(x for x in orig if x["question_id"] == qq["question_id"])
                    kept.append({k: v for k, v in dict(o, db_id=NAME, source_db=qq["source_db"]).items() if k != "SQL"})
            else:
                for qq in json.loads((OPQ / "questions" / sr / sp / f"{NAME}.json").read_text())["questions"]:
                    orig = json.loads((mo.DATA / sr / sp / f"{qq['source_db']}.json").read_text())["questions"]
                    o = dict(next(x for x in orig if x["question_id"] == qq["question_id"]), db_id=NAME, source_db=qq["source_db"])
                    if mo.rows(dest, o["SQL"]) != mo.rows(mo.SRC / qq["source_db"] / f"{qq['source_db']}.sqlite", o["SQL"]):
                        dropped.append({"question_id": o["question_id"], "db": qq["source_db"], "reason": "result differs"})
                        continue
                    kept.append(o)
            target = (OUT / "workload" / sr / f"{NAME}.json") if sp == "construction" else (OUT / "questions" / sr / sp / f"{NAME}.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"db_id": NAME, "questions": kept}, ensure_ascii=False, indent=1))
            report[f"{sr}/{sp}"] = {"kept": len(kept), "dropped": dropped}
    (OUT / "verify.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print({k: (v["kept"], len(v["dropped"])) for k, v in report.items()})


if __name__ == "__main__":
    main()

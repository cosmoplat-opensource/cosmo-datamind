#!/usr/bin/env python3
"""BIRD-opaque-merged: several BIRD Mini-Dev databases in ONE SQLite schema with opaque, globally unique names, to test
whether ontology layers help when an agent can no longer inspect the whole schema cheaply.

Tables are renamed tb001, tb002, ... and columns cl0001, ... with one code per (source database, original column name),
so names never coincide across source databases (no spurious same-name joins) while shared key names inside one source
database stay shared. Keys, types and data are copied unchanged. Every question of the included databases is pointed at
db_id "bird_merged"; its gold SQL is rewritten with the source database's mapping and must return the same multiset of
rows on the merged database as the original gold on the original database. Outputs under variants/opaque_merged/:
dev_databases/bird_merged/bird_merged.sqlite, mapping.json, dictionaries/bird_merged.json, questions/<sr>/<split>/
bird_merged.json, workload/<sr>/bird_merged.json (construction questions with rewritten evidence, no SQL), verify.json.
usage: python3 make_opaque_merged.py
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import exp

import make_opaque as mo

DBS = ["california_schools", "card_games", "debit_card_specializing", "financial", "formula_1", "student_club",
       "superhero", "thrombosis_prediction", "toxicology"]          # the two largest databases are left out (disk space)
OUT = mo.EXP / "variants" / "opaque_merged"
NAME = "bird_merged"


def rename_ddl(ddl, tmap, cmap):
    tree = sqlglot.parse_one(ddl, read="sqlite")
    tl = {k.lower(): v for k, v in tmap.items()}
    for t in tree.find_all(exp.Table):
        if t.name.lower() in tl:
            t.set("this", exp.to_identifier(tl[t.name.lower()]))
    for node in tree.find_all(exp.Identifier):
        par = node.parent
        if isinstance(par, exp.Table):
            continue
        n = node.name.lower()
        if n in cmap and not isinstance(par, exp.Table):
            node.set("this", cmap[n])
            node.set("quoted", True)
    return tree.sql(dialect="sqlite")


def main():
    dest = OUT / "dev_databases" / NAME / f"{NAME}.sqlite"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    con = sqlite3.connect(dest)
    mapping, dictionary, tcount, ccount = {}, {"source": "BIRD database_description re-keyed to opaque names", "tables": {}}, 0, 0
    for db in DBS:
        src = mo.SRC / db / f"{db}.sqlite"
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        tables = [r[0] for r in s.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
        tmap, cmap = {}, {}
        for t in tables:
            tcount += 1
            tmap[t] = f"tb{tcount:03d}"
            for row in s.execute(f"PRAGMA table_info({mo.q(t)})"):
                if row[1].lower() not in cmap:
                    ccount += 1
                    cmap[row[1].lower()] = f"cl{ccount:04d}"
        ddls = {t: s.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0] for t in tables}
        s.close()
        con.execute("ATTACH DATABASE ? AS src", (str(src),))
        for t in tables:
            con.execute(rename_ddl(ddls[t], tmap, cmap))
            con.execute(f"INSERT INTO {mo.q(tmap[t])} SELECT * FROM src.{mo.q(t)}")
        con.commit()
        con.execute("DETACH DATABASE src")
        mapping[db] = {"tables": tmap, "columns": cmap}
        dfile = mo.DICT / f"{db}.json"
        if dfile.exists():
            tl = {k.lower(): v for k, v in tmap.items()}
            for t, cols in (json.loads(dfile.read_text()).get("tables") or {}).items():
                if t.lower() in tl:
                    dictionary["tables"][tl[t.lower()]] = {cmap.get(c.lower(), c): v for c, v in cols.items()}
        print(db, len(tables), "tables", flush=True)
    con.execute("VACUUM")
    con.close()
    (OUT / "mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1))
    (OUT / "dictionaries").mkdir(exist_ok=True)
    (OUT / "dictionaries" / f"{NAME}.json").write_text(json.dumps(dictionary, ensure_ascii=False, indent=1))
    report = {}
    for sr, splits in mo.SPLITS.items():
        for sp in splits:
            kept, dropped = [], []
            for db in DBS:
                f = mo.DATA / sr / sp / f"{db}.json"
                if not f.exists():
                    continue
                tmap, cmap = mapping[db]["tables"], mapping[db]["columns"]
                for qq in json.loads(f.read_text())["questions"]:
                    qq = dict(qq, db_id=NAME, source_db=db)
                    qq["evidence"] = mo.rewrite_text(qq.get("evidence", ""), tmap, cmap)
                    if sp != "construction":
                        try:
                            new = mo.rewrite_sql(qq["SQL"], tmap, cmap)
                            if mo.rows(dest, new) != mo.rows(mo.SRC / db / f"{db}.sqlite", qq["SQL"]):
                                raise ValueError("result differs")
                            qq["SQL_original"], qq["SQL"] = qq["SQL"], new
                        except Exception as e:  # noqa: BLE001
                            dropped.append({"question_id": qq["question_id"], "db": db, "reason": str(e)[:160]})
                            continue
                    else:
                        qq.pop("SQL", None)
                    kept.append(qq)
            target = (OUT / "workload" / sr / f"{NAME}.json") if sp == "construction" else (OUT / "questions" / sr / sp / f"{NAME}.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"db_id": NAME, "questions": kept}, ensure_ascii=False, indent=1))
            report[f"{sr}/{sp}"] = {"kept": len(kept), "dropped": dropped}
    (OUT / "verify.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print({k: (v["kept"], len(v["dropped"])) for k, v in report.items()})


if __name__ == "__main__":
    main()

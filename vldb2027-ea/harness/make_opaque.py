#!/usr/bin/env python3
"""BIRD-opaque: BIRD Mini-Dev databases with opaque table and column names, for evaluating ontology layers on
semantically poor schemas.

For each database: tables are renamed tab01, tab02, ... (schema order) and columns fld001, fld002, ... where one
original column name maps to one code within a database (shared key names stay shared, as in legacy systems where the
same field name recurs across tables). Data, types and declared keys are unchanged (SQLite ALTER TABLE RENAME keeps
foreign-key references consistent). Outputs, under variants/opaque/:
  dev_databases/<db>/<db>.sqlite          renamed copy
  mapping/<db>.json                       {"tables": {orig: code}, "columns": {orig: code}}
  dictionaries/<db>.json                  the BIRD column documentation re-keyed to the codes
  questions/<split>/<db>.json             questions with gold SQL rewritten to the codes, evidence rewritten
                                          (identifier tokens only); each rewritten gold SQL is executed on the
                                          opaque database and must return the same multiset of rows as the original
  construction_workload/<db>.json         construction questions with rewritten evidence (no SQL)
  verify.json                             per-split counts; questions whose rewrite does not reproduce the result
                                          are listed and dropped
usage: python3 make_opaque.py [db ...]
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import sqlglot
from sqlglot import exp

EXP = Path(__file__).resolve().parent
DATA = EXP / "EvoOntology/benchmarks/bird/data"
SRC = DATA / "mini_dev_data/dev_databases"
OUT = EXP / "variants/opaque"
DICT = EXP / "runs/datamind/workdir/dictionaries"
DBS = ["california_schools", "card_games", "codebase_community", "debit_card_specializing", "european_football_2",
       "financial", "formula_1", "student_club", "superhero", "thrombosis_prediction", "toxicology"]
SPLITS = {"minidev": ("construction", "validation", "test"), "minidev_rev": ("construction", "validation", "test")}


def q(name):
    return '"' + name.replace('"', '""') + '"'


def make_mapping(con):
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid")]
    tmap = {t: f"tab{i + 1:02d}" for i, t in enumerate(tables)}
    cmap = {}
    for t in tables:
        for row in con.execute(f"PRAGMA table_info({q(t)})"):
            c = row[1]
            key = c.lower()
            if key not in cmap:
                cmap[key] = f"fld{len(cmap) + 1:03d}"
    return tmap, cmap


def build_db(db, tmap, cmap):
    dest = OUT / "dev_databases" / db / f"{db}.sqlite"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SRC / db / f"{db}.sqlite", dest)
    con = sqlite3.connect(dest)
    con.execute("PRAGMA legacy_alter_table=OFF")
    for t in tmap:
        for row in list(con.execute(f"PRAGMA table_info({q(t)})")):
            c = row[1]
            con.execute(f"ALTER TABLE {q(t)} RENAME COLUMN {q(c)} TO {q(cmap[c.lower()])}")
    for t, code in tmap.items():
        con.execute(f"ALTER TABLE {q(t)} RENAME TO {q(code)}")
    con.commit()
    con.execute("VACUUM")
    con.close()
    return dest


def rewrite_sql(sql, tmap, cmap):
    tl = {k.lower(): v for k, v in tmap.items()}
    tree = sqlglot.parse_one(sql, read="sqlite")
    aliases = {a.name.lower() for a in tree.find_all(exp.TableAlias) if a.name}
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        n = t.name.lower()
        if n in tl and n not in ctes:
            t.set("this", exp.to_identifier(tl[n]))
    select_aliases = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    for c in tree.find_all(exp.Column):
        n = c.name.lower()
        if n in cmap:
            c.set("this", exp.to_identifier(cmap[n]))
        elif n and n not in select_aliases and c.this is not None and not isinstance(c.this, exp.Star):
            raise ValueError("unknown column " + c.name)
        qual = (c.table or "").lower()
        if qual and qual not in aliases and qual in tl:
            c.set("table", exp.to_identifier(tl[qual]))
    return tree.sql(dialect="sqlite")


def rewrite_text(text, tmap, cmap):
    """Replace identifier tokens in evidence text: backticked or double-quoted names, qualified names (T1.col), and
    bare names used in an expression position (followed by an operator or closing parenthesis, or preceded by an
    opening parenthesis or comma inside one). Prose words that merely equal a column name are left as they are."""
    names = {k.lower(): v for k, v in tmap.items()} | dict(cmap)

    def sub_quoted(m):
        n = m.group(2).lower()
        return names[n] if n in names else m.group(0)
    text = re.sub(r"([`\"])([^`\"]+)\1", sub_quoted, text)
    text = re.sub(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b",
                  lambda m: names.get(m.group(1).lower(), m.group(1)) + "." + names.get(m.group(2).lower(), m.group(2)), text)
    op = r"(?=\s*(?:=|<|>|!=|\bLIKE\b|\bIN\b|\bIS\b|\bBETWEEN\b|\)|,))"
    text = re.sub(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)" + op,
                  lambda m: names.get(m.group(1).lower(), m.group(1)), text, flags=re.I)
    text = re.sub(r"(?<=\()\s*([A-Za-z_][A-Za-z0-9_]*)\b", lambda m: names.get(m.group(1).lower(), m.group(1)), text)
    # distinctive identifiers (containing a digit or underscore, or CamelCase) are replaced wherever they occur
    text = re.sub(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)(?![\w])",
                  lambda m: names.get(m.group(1).lower(), m.group(1))
                  if re.search(r"[0-9_]|[a-z][A-Z]", m.group(1)) else m.group(1), text)
    return text


def rows(path, sql):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return Counter(con.execute(sql).fetchall())
    finally:
        con.close()


def main():
    dbs = sys.argv[1:] or DBS
    report = json.loads((OUT / "verify.json").read_text()) if (OUT / "verify.json").exists() else {}
    for db in dbs:
        con = sqlite3.connect(f"file:{SRC / db / (db + '.sqlite')}?mode=ro", uri=True)
        tmap, cmap = make_mapping(con)
        con.close()
        (OUT / "mapping").mkdir(parents=True, exist_ok=True)
        (OUT / "mapping" / f"{db}.json").write_text(json.dumps({"tables": tmap, "columns": cmap}, ensure_ascii=False, indent=1))
        dest = build_db(db, tmap, cmap)
        if (DICT / f"{db}.json").exists():
            d = json.loads((DICT / f"{db}.json").read_text())
            tl = {k.lower(): v for k, v in tmap.items()}
            d["tables"] = {tl.get(t.lower(), t): {cmap.get(c.lower(), c): v for c, v in cols.items()} for t, cols in (d.get("tables") or {}).items()}
            d["source"] = "BIRD database_description re-keyed to opaque names"
            (OUT / "dictionaries").mkdir(parents=True, exist_ok=True)
            (OUT / "dictionaries" / f"{db}.json").write_text(json.dumps(d, ensure_ascii=False, indent=1))
        rep = report.setdefault(db, {})
        for sr, splits in SPLITS.items():
            for sp in splits:
                f = DATA / sr / sp / f"{db}.json"
                if not f.exists():
                    continue
                doc = json.loads(f.read_text())
                kept, dropped = [], []
                for qq in doc["questions"]:
                    qq = dict(qq)
                    qq["evidence"] = rewrite_text(qq.get("evidence", ""), tmap, cmap)
                    if sp != "construction":
                        try:
                            new = rewrite_sql(qq["SQL"], tmap, cmap)
                            if rows(dest, new) != rows(SRC / db / f"{db}.sqlite", qq["SQL"]):
                                raise ValueError("result differs")
                            qq["SQL_original"], qq["SQL"] = qq["SQL"], new
                        except Exception as e:  # noqa: BLE001
                            dropped.append({"question_id": qq["question_id"], "reason": str(e)[:160]})
                            continue
                    else:
                        qq.pop("SQL", None)
                    kept.append(qq)
                target = OUT / "questions" / sr / sp / f"{db}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({**doc, "questions": kept}, ensure_ascii=False, indent=1))
                rep[f"{sr}/{sp}"] = {"kept": len(kept), "dropped": dropped}
        print(db, {k: (v["kept"], len(v["dropped"])) for k, v in rep.items()}, flush=True)
        (OUT / "verify.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

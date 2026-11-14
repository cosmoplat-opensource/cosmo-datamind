"""Read-only field documentation and name/value catalogs (DR-093).

Directory coverage is data evidence; it does not certify business meaning.
All dynamic identifiers are checked against the schema and quoted.
"""
from __future__ import annotations

import re
import sqlite3

DOC_TEXT_COLS = ("documentation", "description", "column_description", "comment", "comments", "remark", "desc")
NAME_COL = re.compile(r"(^|_)(name|code|item|param|parameter|metric|indicator|attr|attribute|tag|concept|key)$")
VALUE_COL = re.compile(r"(^|_)(value|val|amount|qty|quantity|reading|measure)$")
UNIT_COL = re.compile(r"(^|_)(unit|uom|units)$")
NUMERIC = re.compile(r"REAL|INT|NUM|DEC|FLOAT|DOUBLE", re.I)


def qi(name):
    return '"' + str(name).replace('"', '""') + '"'


def schema(con):
    return {t: [(r[1], r[2]) for r in con.execute(f"PRAGMA table_info({qi(t)})")]
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def doc_dictionary(con: sqlite3.Connection) -> dict:
    tables = schema(con)
    existing = {t.casefold(): {c.casefold() for c, _ in cols} for t, cols in tables.items()}
    out: dict = {}
    for t, cols in tables.items():
        low = {c.casefold(): c for c, _ in cols}
        text = next((low[c] for c in DOC_TEXT_COLS if c in low), None)
        if not text or not {"table_name", "column_name"} <= low.keys():
            continue
        try:
            rows = con.execute(f"SELECT {qi(low['table_name'])},{qi(low['column_name'])},{qi(text)} FROM {qi(t)} LIMIT 20000")
            for tn, cn, doc in rows:
                tn, cn = str(tn or '').casefold(), str(cn or '').casefold()
                if doc and cn in existing.get(tn, set()):
                    out.setdefault(tn, {}).setdefault(cn, str(doc).strip()[:2000])
        except sqlite3.Error:
            continue
    return out


def value_catalog(con, table, cols, entity_col=None, top=150, min_distinct=20):
    actual = {r[1]: r[2] for r in con.execute(f"PRAGMA table_info({qi(table)})")}
    cols = [(c, ty) for c, ty in cols if c in actual]
    names = [c for c, ty in cols if NAME_COL.search(c.lower()) and not NUMERIC.search(ty or '')]
    values = [c for c, ty in cols if VALUE_COL.search(c.lower()) and NUMERIC.search(ty or '')]
    units = [c for c, _ in cols if UNIT_COL.search(c.lower())]
    if not names or not values:
        return None
    entity_col = next((c for c in actual if c.casefold() == str(entity_col or '').casefold()), None)
    top = max(1, min(int(top), 500))
    t = qi(table)
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    choices = [(con.execute(f"SELECT COUNT(DISTINCT {qi(c)}) FROM {t}").fetchone()[0], c) for c in names]
    choices = [(k, c) for k, c in choices if min_distinct <= k <= 200000 and n >= 5 * k]
    if not choices:
        return None
    k, nc = max(choices)
    ent = f"COUNT(DISTINCT {qi(entity_col)})" if entity_col else "0"
    query = (f"SELECT {qi(nc)},COUNT(*),{ent} FROM {t} WHERE {qi(nc)} IS NOT NULL "
             f"GROUP BY {qi(nc)} ORDER BY 3 DESC,2 DESC,1 LIMIT {top}")
    rows = con.execute(query).fetchall()
    unit_of: dict = {}
    if units and rows:
        query_u = (f"SELECT {qi(nc)},{qi(units[0])},COUNT(*) FROM {t} "
                   f"WHERE {qi(nc)} IN ({','.join('?' for _ in rows)}) GROUP BY 1,2 ORDER BY 3 DESC,2")
        for name, unit, count in con.execute(query_u, [r[0] for r in rows]):
            unit_of.setdefault(name, []).append({"unit": '' if unit is None else str(unit), "rows": count})
    per_entity = None
    if entity_col:
        ks = sorted(r[0] for r in con.execute(f"SELECT COUNT(DISTINCT {qi(nc)}) FROM {t} "
                                             f"WHERE {qi(entity_col)} IS NOT NULL GROUP BY {qi(entity_col)}"))
        if ks:
            mid = len(ks) // 2
            median = ks[mid] if len(ks) % 2 else (ks[mid - 1] + ks[mid]) / 2
            per_entity = {"min": ks[0], "median": median, "max": ks[-1], "entities": len(ks)}
    entries = []
    for value, count, entities in rows:
        choices_u = [x["unit"] for x in unit_of.get(value, [])]
        entries.append({"value": str(value), "rows": count, "entities": entities,
                        "units": choices_u, "unit": choices_u[0] if len(choices_u) == 1 else '',
                        "unit_counts": unit_of.get(value, [])})
    return {"name_col": nc, "value_col": values[0], "value_cols": values, "unit_col": units[0] if units else '',
            "entity_col": entity_col or '', "distinct": k, "rows": n, "entries": entries,
            "truncated": k > len(entries), "per_entity": per_entity,
            "evidence": {"method": "sql_group_by", "sql": query, "source_scan_complete": True,
                         "coverage_complete": k == len(entries)}}


def null_profile(con, table, column, sample=20000):
    columns = {r[1].casefold(): r[1] for r in con.execute(f"PRAGMA table_info({qi(table)})")}
    real = columns.get(str(column).casefold())
    if not real:
        raise ValueError("取证列不存在")
    limit = max(1, min(int(sample), 20000))
    sql = (f"SELECT COUNT(*),SUM(CASE WHEN value IS NULL OR TRIM(CAST(value AS TEXT))='' THEN 1 ELSE 0 END) "
           f"FROM (SELECT t.{qi(real)} AS value FROM {qi(table)} t LIMIT {limit})")
    rows, empty = con.execute(sql).fetchone()
    return {"ratio": empty / rows if rows else None, "sample_rows": rows, "sample_limit": limit, "sql": sql}


def enrich(con, ir):
    docs, tables = doc_dictionary(con), schema(con)
    lower = {t.casefold(): t for t in tables}
    entity_of: dict = {}
    for r in ir.get("relations") or []:
        ev = r.get("evidence") or {}
        key = ev.get("child_key")
        if r.get("status") in ("verified", "asserted") and key and ',' not in key:
            entity_of.setdefault(r.get("source_concept"), key)
    catalogs = documented = 0
    for o in ir.get("objects") or []:
        t = lower.get(str(o.get("table") or '').casefold())
        if not t or o.get("kind") == "action":
            continue
        d = docs.get(t.casefold()) or {}
        if d:
            documented += 1
            for a in o.get("attrs") or []:
                if d.get(str(a.get("col") or '').casefold()):
                    a["doc"] = d[a["col"].casefold()]
        cat = value_catalog(con, t, tables[t], entity_col=entity_of.get(o.get("name")))
        o.pop("catalog", None)  # A fresh build must not preserve a stale machine directory.
        if cat:
            o["catalog"] = cat
            catalogs += 1
    return catalogs, documented

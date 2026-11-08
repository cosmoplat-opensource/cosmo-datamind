"""Data-grounded naming and term grounding for semantically poor schemas (DR-093).

When table and column names carry no meaning (legacy codes such as tb036 / cl0301) and no data dictionary exists, the
name-based steps of the build (token dictionary, model naming from column names) have nothing to work with. This module
looks at the data instead, as an analyst would, and keeps DataMind's rule that every claim is adjudicated on the data:

1. profile_db       per column: type, row count, distinct count, null share, most common values, numeric/text range.
2. evidence_groundings
                    the build questions' evidence often states a term and the column that records it
                    ("State Special School means cl0069 = 'SSS'"; "Excellence rate = cl0040 / cl0036"). Each such clause
                    becomes a term -> column (+ filter) grounding, kept only if the column exists and the filter hits rows;
                    the hit count and sample values are the evidence.
3. value_groundings literal values in the questions and evidence ('Alameda', "Fremont") are looked up in the data; a value
                    found in one or a few columns grounds that value to those columns (evidence: row count).
4. name_objects     the model names tables and columns from the profile and the groundings; a name is kept only if its
                    stated basis cites values or phrases that actually occur for that table (zero fabrication). Kept
                    names have status "candidate" and carry their basis; dictionary names are never overwritten.
5. profile_brief    the schema summary with profiles, for the concept step and for its repair rounds.
"""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Callable

SAMPLE = 20000          # rows sampled per table for distinct counts and common values
TOPK = 5
VALUE_INDEX_MAX_DISTINCT = 20000
GENERIC_CN = re.compile(r"^(主?业务|业务实体|主业务实体|实体)|业务.*记录$|^业务")


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _short(v, n=30) -> str:
    s = str(v)
    return s if len(s) <= n else s[:n - 1] + "…"


def _timed(con, seconds=20.0):
    deadline = time.time() + seconds
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 20000)


def _untimed(con):
    con.set_progress_handler(None, 0)


# ---------------------------------------------------------------- 1. profile

def profile_db(con: sqlite3.Connection, tables: list[str] | None = None) -> dict:
    names = tables or [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out = {}
    for t in names:
        try:
            cols = [(r[1], (r[2] or "").upper()) for r in con.execute(f"PRAGMA table_info({_q(t)})")]
            n = con.execute(f"SELECT COUNT(*) FROM {_q(t)}").fetchone()[0]
        except sqlite3.Error:
            continue
        src = f"(SELECT * FROM {_q(t)} LIMIT {SAMPLE})"
        prof = {}
        for c, typ in cols:
            p = {"type": typ or "?"}
            try:
                _timed(con)
                row = con.execute(f"SELECT COUNT(DISTINCT {_q(c)}), SUM({_q(c)} IS NULL), MIN({_q(c)}), MAX({_q(c)}), "
                                  f"COUNT(*) FROM {src}").fetchone()
                p.update(distinct=row[0], null_pct=round(100 * (row[1] or 0) / max(row[4], 1)), min=row[2], max=row[3])
                top = con.execute(f"SELECT {_q(c)}, COUNT(*) k FROM {src} WHERE {_q(c)} IS NOT NULL AND {_q(c)} <> '' "
                                  f"GROUP BY 1 ORDER BY k DESC LIMIT {TOPK}").fetchall()
                p["top"] = [[_short(v, 40), k] for v, k in top]
            except sqlite3.Error:
                pass
            finally:
                _untimed(con)
            prof[c] = p
        out[t] = {"rows": n, "sampled": n > SAMPLE, "cols": prof}
    return out


def _col_text(c: str, p: dict, label: str = "") -> str:
    bits = [p.get("type", "?").lower() or "?"]
    if p.get("distinct") is not None:
        bits.append(f"{p['distinct']} distinct")
    if p.get("null_pct"):
        bits.append(f"{p['null_pct']}% null")
    top = p.get("top") or []
    numeric = p.get("type", "").startswith(("INT", "REAL", "NUM", "FLOAT", "DOUBLE", "DEC"))
    if numeric and p.get("min") is not None:
        bits.append(f"range {_short(p['min'], 14)}..{_short(p['max'], 14)}")
    if top and (not numeric or (p.get("distinct") or 99) <= 12):
        bits.append("e.g. " + ", ".join(_short(v, 24) for v, _ in top[:TOPK]))
    return f"{c}{'〔' + label + '〕' if label else ''}[{'; '.join(bits)}]"


def profile_brief(prof: dict, labels: dict | None = None, tables: list[str] | None = None, max_cols: int = 80) -> str:
    """Schema summary with profiles. labels: {table: {"_table": name, col: name}} (candidate names, shown in 〔〕)."""
    labels = labels or {}
    lines = []
    for t in tables or sorted(prof):
        if t not in prof:
            continue
        lab = labels.get(t) or {}
        head = f"- {t}{'〔' + lab['_table'] + '〕' if lab.get('_table') else ''} ({prof[t]['rows']} rows): "
        cols = list(prof[t]["cols"].items())[:max_cols]
        lines.append(head + ", ".join(_col_text(c, p, lab.get(c, "")) for c, p in cols))
    return "\n".join(lines)


# ---------------------------------------------------------------- 2. evidence groundings

CLAUSE_SPLIT = re.compile(r";|\n|(?<=[a-z0-9'\"])\.\s+(?=[A-Z])")
DEF = re.compile(r"^(?P<lhs>.+?)\s+(?:means|refers? to|stands? for|indicates|is defined as|denotes|is|are|=)\s+(?P<rhs>.+)$", re.I)
FILTER = re.compile(r"(?P<col>`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][\w.]*)\s*(?P<op>=|==|<>|!=|>=|<=|>|<|\bLIKE\b|\bIN\b)\s*"
                    r"(?P<val>'(?:[^']|'')*'|\"[^\"]*\"|-?\d+(?:\.\d+)?|\([^)]*\))", re.I)


def _evidence_part(item: str) -> tuple[str, str]:
    s = str(item or "")
    m = re.search(r"\n?Evidence:\s*", s)
    return (s[:m.start()].strip(), s[m.end():].strip()) if m else (s.strip(), "")


def _columns_index(con: sqlite3.Connection) -> dict:
    """column name (lower) -> [(table, real column name)]"""
    idx = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        for r in con.execute(f"PRAGMA table_info({_q(t)})"):
            idx.setdefault(r[1].lower(), []).append((t, r[1]))
    return idx


def _strip_ident(s: str) -> str:
    s = s.strip()
    if s[:1] in "`\"[" and s[-1:] in "`\"]":
        s = s[1:-1]
    return s.split(".")[-1] if "." in s and not s.startswith("'") else s


def _clean_term(lhs: str) -> str:
    t = re.sub(r"^(the|a|an)\s+", "", lhs.strip(" '\"`,.:"), flags=re.I)
    t = re.sub(r"\s+", " ", t)
    return t[:60]


def _mentions(rhs: str, colidx: dict) -> list[str]:
    """column names mentioned in a clause: quoted identifiers, table.column, or bare tokens that are column names and
    look like identifiers (contain a digit or underscore, or are quoted), so common words are not taken for columns."""
    found = []
    for m in re.finditer(r"`([^`]+)`|\"([^\"]+)\"|\[([^\]]+)\]|\b([A-Za-z_][\w]*\.[A-Za-z_][\w]*)\b|\b([A-Za-z_]*\d[\w]*|[A-Za-z]+_[\w]+)\b", rhs):
        raw = next(g for g in m.groups() if g)
        name = raw.split(".")[-1].lower()
        if name in colidx and name not in found:
            found.append(name)
    return found


def _count(con, t, flt):
    try:
        _timed(con, 15)
        return con.execute(f"SELECT COUNT(*) FROM {_q(t)}" + (f" WHERE {flt}" if flt else "")).fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        _untimed(con)


def evidence_groundings(con: sqlite3.Connection, workload: list, prof: dict) -> dict:
    colidx = _columns_index(con)
    kept, rejected, seen = [], [], set()
    for qi, item in enumerate(workload or []):
        _, ev = _evidence_part(item)
        if not ev:
            continue
        for clause in CLAUSE_SPLIT.split(ev):
            clause = clause.strip(" .")
            m = DEF.match(clause)
            if not m:
                continue
            term, rhs = _clean_term(m.group("lhs")), m.group("rhs")
            if len(term) < 2 or not re.search(r"[A-Za-z一-鿿]", term):
                continue
            cols = _mentions(rhs, colidx)
            if not cols:
                continue
            filters = {}
            for f in FILTER.finditer(rhs):
                c = _strip_ident(f.group("col")).lower()
                if c in colidx:
                    filters.setdefault(c, []).append(f"{f.group('op').upper()} {f.group('val')}")
            kind = "formula" if len(cols) > 1 and not filters and re.search(r"[/*+\-]|divided|ratio|sum|minus", rhs, re.I) else (
                "value" if filters else "attribute")
            for c in cols:
                for t, real in colidx[c]:
                    flt = " AND ".join(f"{_q(real)} {x}" for x in filters.get(c, []))
                    n = _count(con, t, flt)
                    key = (term.lower(), t, real, flt)
                    if key in seen:
                        continue
                    seen.add(key)
                    rec = {"term": term, "table": t, "column": real, "filter": flt, "kind": kind,
                           "formula": rhs[:200] if kind == "formula" else "", "source": "workload_evidence",
                           "clause": clause[:240], "question_ref": qi}
                    if n is None:
                        rejected.append(dict(rec, why="filter not executable"))
                    elif n == 0 and (flt or (prof.get(t) or {}).get("rows")):
                        rejected.append(dict(rec, why="0 rows"))
                    else:
                        p = ((prof.get(t) or {}).get("cols") or {}).get(real) or {}
                        rec.update(rows=n, samples=[v for v, _ in (p.get("top") or [])[:3]], status="verified")
                        kept.append(rec)
    # a code shared by several tables (same key inside one source database): keep all, ranked by hits
    return {"groundings": kept, "rejected": rejected}


# ---------------------------------------------------------------- 3. value groundings

QUOTED = re.compile(r"'([^']{3,60})'|\"([^\"]{3,60})\"")
CAPS = re.compile(r"\b([A-Z][a-zA-Z.&'-]+(?:\s+(?:of|de|la|and|the|[A-Z][a-zA-Z.&'-]+)){0,3})\b")
STOP = {"What", "Which", "How", "List", "Please", "Give", "Name", "Among", "State", "Show", "Find", "Calculate", "Who",
        "When", "Where", "The", "In", "For", "Of", "Is", "Are", "Indicate", "Provide", "Identify", "Tell", "Mention",
        "Evidence", "Of the", "From", "Between", "Do", "Does", "Did", "If", "And", "Count", "Describe", "Write"}


def value_index(con: sqlite3.Connection, prof: dict) -> dict:
    idx = {}
    for t, tp in prof.items():
        for c, p in tp["cols"].items():
            if not str(p.get("type", "")).startswith(("TEXT", "VARCHAR", "CHAR", "")) or str(p.get("type", "")).startswith(("INT", "REAL")):
                continue
            if (p.get("distinct") or 0) == 0 or (p.get("distinct") or 0) > VALUE_INDEX_MAX_DISTINCT:
                continue
            try:
                _timed(con, 20)
                for (v,) in con.execute(f"SELECT DISTINCT {_q(c)} FROM {_q(t)} WHERE typeof({_q(c)})='text' LIMIT {VALUE_INDEX_MAX_DISTINCT + 1}"):
                    if v and 3 <= len(v) <= 60:
                        idx.setdefault(v.lower(), set()).add((t, c))
            except sqlite3.Error:
                pass
            finally:
                _untimed(con)
    return idx


def value_groundings(con: sqlite3.Connection, workload: list, idx: dict, max_cols: int = 3) -> list:
    out, seen = [], set()
    for qi, item in enumerate(workload or []):
        q, ev = _evidence_part(item)
        cands = [a or b for a, b in QUOTED.findall(q + " " + ev)] + [m for m in CAPS.findall(q) if m not in STOP]
        for v in dict.fromkeys(x.strip() for x in cands):
            hits = idx.get(v.lower())
            if not hits or len(hits) > max_cols:
                continue
            for t, c in sorted(hits):
                if (v.lower(), t, c) in seen:
                    continue
                seen.add((v.lower(), t, c))
                n = _count(con, t, f"{_q(c)} = '" + v.replace("'", "''") + "'")
                if n:
                    out.append({"value": v, "table": t, "column": c, "rows": n, "source": "workload_value_lookup",
                                "question_ref": qi, "status": "verified"})
    return out


# ---------------------------------------------------------------- 4. naming

NAME_PROMPT = """You are naming the tables and columns of a database whose names are opaque codes. Look at the data profile
(types, distinct counts, common values, ranges), the verified links, and the business phrases that the users' questions
attach to particular columns. Name what the data shows; do not guess beyond it.

Output one JSON object:
{{"tables":[{{"table":"code","name_en":"business name of one row's entity, e.g. School","name_cn":"中文业务名",
  "basis":"quote the values or phrases that show this, e.g. values 'Alameda','Fresno' in cl0049; phrase 'State Special School'",
  "columns":[{{"column":"code","name_en":"e.g. County","basis":"quoted values or phrase"}}]}}]}}
Rules: every basis must quote at least one value or phrase that appears below for that table; leave out tables or columns
you cannot name from what is shown (identifiers and keys may be named "<entity> ID" when a verified link shows it);
use the users' phrase as the name when a column carries one.

Tables:
{tables}
"""


def _table_block(t: str, prof: dict, groundings: list, values: list, links: list) -> tuple[str, set]:
    """prompt block for one table and the set of strings a basis may quote."""
    tp = prof[t]
    allowed = set()
    lines = [f"## {t} ({tp['rows']} rows)"]
    for c, p in tp["cols"].items():
        lines.append("  " + _col_text(c, p))
        allowed |= {str(v).lower() for v, _ in p.get("top") or []}
        if p.get("min") is not None:
            allowed |= {str(p["min"]).lower(), str(p["max"]).lower()}
    g = [x for x in groundings if x["table"] == t]
    if g:
        lines.append("  phrases from users' questions: " + "; ".join(
            f"'{x['term']}' -> {x['column']}{' ' + x['filter'] if x['filter'] else ''}" for x in g[:25]))
        allowed |= {x["term"].lower() for x in g}
    v = [x for x in values if x["table"] == t]
    if v:
        lines.append("  values users asked about: " + "; ".join(f"'{x['value']}' in {x['column']}" for x in v[:20]))
        allowed |= {x["value"].lower() for x in v}
    lk = [x for x in links if x[0] == t or x[2] == t]
    if lk:
        lines.append("  verified links: " + "; ".join(f"{a}.{b} -> {c}.{d}" for a, b, c, d in lk[:12]))
        allowed |= {a.lower() for a, _, c, _ in lk} | {c.lower() for _, _, c, _ in lk}
    return "\n".join(lines), allowed


def _basis_ok(basis: str, allowed: set) -> bool:
    b = str(basis or "").lower()
    if not b.strip():
        return False
    quoted = [x.strip().lower() for x in re.findall(r"'([^']+)'|\"([^\"]+)\"", basis) for x in x if x]
    if any(q in allowed or any(q in a or a in q for a in allowed if len(a) >= 3 and len(q) >= 3) for q in quoted):
        return True
    return any(a in b for a in allowed if len(a) >= 4)


def needs_name(o: dict) -> bool:
    cn = str(o.get("cn") or "").strip()
    return bool(o.get("table")) and o.get("kind") != "action" and (not cn or cn == o.get("table") or bool(GENERIC_CN.search(cn)))


def name_objects(ir: dict, prof: dict, groundings: list, values: list, ask: Callable[[str], str],
                 extract: Callable[[str], dict | None], batch: int = 6) -> dict:
    links = []
    for r in ir.get("relations") or []:
        e = r.get("evidence") or {}
        if r.get("status") == "verified" and e.get("child_key"):
            links.append((r.get("source_table") or r.get("source_concept"), e.get("child_key"),
                          r.get("target_table") or r.get("target_concept"), e.get("parent_key") or ""))
    objs = [o for o in ir.get("objects") or [] if needs_name(o) and o["table"] in prof]
    named_t = named_c = 0
    rejected = []
    for i in range(0, len(objs), batch):
        part = objs[i:i + batch]
        blocks = {o["table"]: _table_block(o["table"], prof, groundings, values, links) for o in part}
        reply = ask(NAME_PROMPT.format(tables="\n\n".join(b for b, _ in blocks.values())))
        d = extract(reply or "") or {}
        by_t = {str(x.get("table") or ""): x for x in d.get("tables") or [] if isinstance(x, dict)}
        for o in part:
            x = by_t.get(o["table"])
            if not x:
                continue
            allowed = blocks[o["table"]][1]
            if x.get("name_en") and _basis_ok(x.get("basis"), allowed):
                o["label_candidate"] = {"en": str(x["name_en"])[:60], "cn": str(x.get("name_cn") or "")[:40],
                                        "basis": str(x["basis"])[:300], "status": "candidate", "source": "data_profile"}
                o.setdefault("cn_prev", o.get("cn"))
                o["cn"] = o["label_candidate"]["cn"] or o["label_candidate"]["en"]
                o["cn_status"] = "candidate"
                named_t += 1
            elif x.get("name_en"):
                rejected.append({"table": o["table"], "name": x.get("name_en"), "basis": x.get("basis"), "why": "basis not found in data"})
            cols = {str(c.get("column") or "").lower(): c for c in x.get("columns") or [] if isinstance(c, dict)}
            for a in o.get("attrs") or []:
                c = cols.get(str(a.get("col") or "").lower())
                if not c or not c.get("name_en") or a.get("cn"):
                    continue
                if _basis_ok(c.get("basis"), allowed):
                    a["label_candidate"] = {"en": str(c["name_en"])[:60], "basis": str(c.get("basis"))[:200],
                                            "status": "candidate", "source": "data_profile"}
                    named_c += 1
                else:
                    rejected.append({"table": o["table"], "column": a.get("col"), "name": c.get("name_en"), "why": "basis not found in data"})
    return {"tables_named": named_t, "columns_named": named_c, "tables_considered": len(objs), "rejected": rejected}


def labels_of(ir: dict) -> dict:
    out = {}
    for o in ir.get("objects") or []:
        if not o.get("table"):
            continue
        lab = {}
        if o.get("cn") and o.get("cn") != o.get("table") and not GENERIC_CN.search(str(o.get("cn"))):
            lab["_table"] = o["cn"]
        for a in o.get("attrs") or []:
            nm = a.get("cn") or (a.get("label_candidate") or {}).get("en")
            if nm:
                lab[a["col"]] = nm
        out[o["table"]] = lab
    return out


def groundings_as_concepts(groundings: list) -> list:
    """verified evidence groundings as concept records (same shape as workload_concepts.verify_concept output)."""
    out, seen = [], set()
    for g in sorted(groundings, key=lambda x: -(x.get("rows") or 0)):
        if g["kind"] == "formula":
            continue
        k = (g["term"].lower(), g["table"], g["column"], g["filter"])
        if k in seen:
            continue
        seen.add(k)
        out.append({"name": g["term"], "definition": f"From the users' evidence: {g['clause']}", "aliases": [g["term"]],
                    "table": g["table"], "column": g["column"], "filter": g["filter"],
                    "aggregation": "COUNT" if g["filter"] else "attribute",
                    "evidence": {"rows": g.get("rows"), "samples": g.get("samples"), "source": "workload_evidence"},
                    "origin": "evidence_grounding", "status": "verified"})
    return out

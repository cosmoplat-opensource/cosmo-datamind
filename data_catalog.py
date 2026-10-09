"""数据目录补充(DR-087):库内文档表作数据字典;名称-取值型表的取值目录。

- 库内文档表:有 table_name、column_name 与一列说明文字(documentation / description / comment 等)的表,
  就是库自带的数据字典(DDR-Bench 10-K 的 column_documentation、企业库的字段说明表)。
- 取值目录:一张表用「名称列 + 数值列」记录很多种指标(fact_name / fact_value、param_name / param_value),
  指标本身藏在名称列的取值里。按覆盖的实体数取最常见的若干种,连同行数与常用单位记入本体,
  问数与导出时把它们当作可直接引用的指标概念。
只读,不改库。
"""
from __future__ import annotations

import re
import sqlite3

DOC_TEXT_COLS = ("documentation", "description", "column_description", "comment", "comments", "remark", "desc")
NAME_COL = re.compile(r"(^|_)(name|code|item|param|parameter|metric|indicator|attr|attribute|tag|concept|key)$")
VALUE_COL = re.compile(r"(^|_)(value|val|amount|qty|quantity|reading|measure)$")
UNIT_COL = re.compile(r"(^|_)(unit|uom|units)$")
NUMERIC = re.compile(r"REAL|INT|NUM|DEC|FLOAT|DOUBLE", re.I)


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def doc_dictionary(con: sqlite3.Connection) -> dict:
    """库内文档表 → {表名小写: {列名小写: 说明}};没有文档表时为空。"""
    out: dict = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({_q(t)})")]
        low = {c.lower(): c for c in cols}
        text = next((low[c] for c in DOC_TEXT_COLS if c in low), None)
        if "table_name" not in low or "column_name" not in low or not text:
            continue
        try:
            for tn, cn, doc in con.execute(
                    f"SELECT {_q(low['table_name'])}, {_q(low['column_name'])}, {_q(text)} FROM {_q(t)} LIMIT 20000"):
                if tn and cn and doc:
                    out.setdefault(str(tn).lower(), {}).setdefault(str(cn).lower(), str(doc).strip())
        except sqlite3.Error:
            continue
    return out


def value_catalog(con: sqlite3.Connection, table: str, cols: list, entity_col: str | None = None,
                  top: int = 150, min_distinct: int = 20) -> dict | None:
    """名称-取值型表的取值目录;不是这类表时返回 None。cols 为 [(列名, 类型)]。"""
    names = [c for c, ty in cols if NAME_COL.search(c.lower()) and not NUMERIC.search(ty or "")]
    values = [c for c, ty in cols if VALUE_COL.search(c.lower()) and NUMERIC.search(ty or "")]
    units = [c for c, _ in cols if UNIT_COL.search(c.lower())]
    if not names or not values:
        return None
    t = _q(table)
    try:
        n_rows = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        best = None
        for nc in names:
            k = con.execute(f"SELECT COUNT(DISTINCT {_q(nc)}) FROM {t}").fetchone()[0]
            if min_distinct <= k <= 200000 and n_rows >= 5 * k and (best is None or k > best[1]):
                best = (nc, k)
        if not best:
            return None
        nc, k = best
        ent = f"COUNT(DISTINCT {_q(entity_col)})" if entity_col else "0"
        rows = con.execute(f"SELECT {_q(nc)}, COUNT(*), {ent} FROM {t} WHERE {_q(nc)} IS NOT NULL "
                           f"GROUP BY {_q(nc)} ORDER BY 3 DESC, 2 DESC LIMIT {int(top)}").fetchall()
        unit_of = {}
        if units:
            uc = _q(units[0])
            for name, unit, _n in con.execute(
                    f"SELECT {_q(nc)}, {uc}, COUNT(*) FROM {t} WHERE {_q(nc)} IN ({','.join('?' * len(rows))}) "
                    f"GROUP BY {_q(nc)}, {uc} ORDER BY 3", [r[0] for r in rows]):
                unit_of[name] = unit                      # 按行数升序覆盖,最后留下最常用的单位
    except sqlite3.Error:
        return None
    per_entity = None
    if entity_col:                                    # 每个实体记录了多少种代码:目录只列常见的,其余要按实体自行列出
        try:
            ks = sorted(r[0] for r in con.execute(
                f"SELECT COUNT(DISTINCT {_q(nc)}) FROM {t} GROUP BY {_q(entity_col)}"))
            if ks:
                per_entity = {"min": ks[0], "median": ks[len(ks) // 2], "max": ks[-1], "entities": len(ks)}
        except sqlite3.Error:
            per_entity = None
    return {"name_col": nc, "value_col": values[0], "unit_col": units[0] if units else "", "per_entity": per_entity,
            "entity_col": entity_col or "", "distinct": k, "rows": n_rows,
            "entries": [{"value": str(v), "rows": r, "entities": e, "unit": str(unit_of.get(v) or "")}
                        for v, r, e in rows]}

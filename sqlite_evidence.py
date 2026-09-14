"""Complete SQLite evidence for one-column and compound joins.

Only aggregate counts leave SQLite. No LIMIT is applied to the value domains:
sampling a prefix cannot justify a verified relationship. The caller owns the
read transaction and progress handler, so its query budget remains in force.
"""
from __future__ import annotations


def _qi(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'


def _columns(connection, table, columns):
    if isinstance(columns, str):
        columns = tuple(x.strip() for x in columns.split(","))
    else:
        columns = tuple(columns)
    if (not 1 <= len(columns) <= 32 or any(not isinstance(c, str) or not c for c in columns)
            or len({c.casefold() for c in columns}) != len(columns)):
        raise ValueError("连接键必须为 1～32 个不重复的真实列")
    # table_xinfo includes generated columns and uses quoted identifiers safely.
    known = {r[1].casefold(): r[1] for r in connection.execute(f"PRAGMA table_xinfo({_qi(table)})")}
    if not known or any(c.casefold() not in known for c in columns):
        raise ValueError("取证表或连接键不存在")
    return tuple(known[c.casefold()] for c in columns)


def _values_sql(table, columns):
    # Qualifying identifiers also disables SQLite's double-quoted string fallback.
    refs = [f"q.{_qi(c)}" for c in columns]
    select = ", ".join(refs)
    nonempty = " AND ".join(f"{ref} IS NOT NULL AND {ref} != ''" for ref in refs)
    return f"SELECT DISTINCT {select} FROM {_qi(table)} AS q WHERE {nonempty}"


def relation_signals(connection, child_table, child_columns, parent_table, parent_columns):
    """Return exact overlap and uniqueness evidence, or raise on failed extraction.

    Tuple values are compared jointly, never concatenated or checked one column
    at a time. NULL/empty components do not contribute to overlap; uniqueness
    includes all table rows, so missing key values cannot prove a candidate key.
    """
    child_columns = _columns(connection, child_table, child_columns)
    parent_columns = _columns(connection, parent_table, parent_columns)
    if len(child_columns) != len(parent_columns):
        raise ValueError("子键与父键的列数必须相同")
    child_values = _values_sql(child_table, child_columns)
    parent_values = _values_sql(parent_table, parent_columns)
    row = connection.execute(
        f"WITH child_values AS ({child_values}), parent_values AS ({parent_values}) "
        "SELECT (SELECT COUNT(*) FROM child_values), "
        "(SELECT COUNT(*) FROM parent_values), "
        "(SELECT COUNT(*) FROM (SELECT * FROM child_values INTERSECT SELECT * FROM parent_values)), "
        f"(SELECT COUNT(*) FROM {_qi(child_table)}), (SELECT COUNT(*) FROM {_qi(parent_table)})"
    ).fetchone()
    child_distinct, parent_distinct, matched_distinct, child_rows, parent_rows = row
    return {
        "overlap": 100.0 * matched_distinct / child_distinct if child_distinct else -1.0,
        "child_distinct": child_distinct, "parent_distinct": parent_distinct,
        "matched_distinct": matched_distinct, "child_rows": child_rows, "parent_rows": parent_rows,
        "child_unique": bool(child_rows and child_rows == child_distinct),
        "parent_unique": bool(parent_rows and parent_rows == parent_distinct),
        "evidence_complete": True, "evidence_method": "sql_distinct_intersect",
    }

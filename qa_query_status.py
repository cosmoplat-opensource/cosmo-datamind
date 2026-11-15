# -*- coding: utf-8 -*-
"""查数结果的状态与实际取数日期(DR-130)。

一条查询有三种结局,含义不同,不能混在一起:
  data    取到了行;
  empty   执行成功但 0 行 —— 可能是表里本来没有数据,也可能只是筛选条件没命中,都不等于「业务上没有」;
  failed  没执行成(出错、被校验拦下)—— 这部分什么都不知道。
此前失败的分析直接从答案里消失,空结果不加说明。本模块只读数据库做核对,核对不了就不说。
"""
from __future__ import annotations

import re
import sqlite3
import time

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TIME_NAME = re.compile(r"(date|time|_at$|_dt$|_ts$|日期|时间)", re.I)
_DATE_VALUE = re.compile(r"^\d{4}[-/]?\d{2}")
_BUDGET_S = 3.0


def _guard(con):
    deadline = time.time() + _BUDGET_S
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10000)


def tables_read(sql, connect):
    """这条查询实际读到的基础表(由数据库自己解析:CTE、嵌套查询、带引号的名字都认得出)。打不开或解析不了返回 []。"""
    seen: set[str] = set()
    con = None
    try:
        con = connect()

        def authorize(action, table, _column, database, _source):
            if action == sqlite3.SQLITE_READ and table and database in (None, "main") and not str(table).startswith("sqlite_"):
                seen.add(str(table))
            return sqlite3.SQLITE_OK

        con.set_authorizer(authorize)
        con.execute("EXPLAIN " + sql).fetchone()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return []
    finally:
        if con is not None:
            con.close()
    return sorted(seen)


def _scalar(connect, sql):
    con = None
    try:
        con = connect()
        _guard(con)
        row = con.execute(sql).fetchone()
        return row[0] if row else None
    except (sqlite3.Error, OSError):
        return None
    finally:
        if con is not None:
            con.close()


def _columns(connect, table):
    con = None
    try:
        con = connect()
        return [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
    except (sqlite3.Error, OSError):
        return []
    finally:
        if con is not None:
            con.close()


def _declared_time(ir, table):
    for o in (ir or {}).get("objects") or []:
        if isinstance(o, dict) and o.get("table") == table:
            raw = o.get("time")
            declared: dict = raw if isinstance(raw, dict) else {}
            col = o.get("time_col") or o.get("time_column") or declared.get("col")
            if isinstance(col, str) and _IDENT.match(col):
                return col
    return None


def as_of(sql, tables, connect, ir=None):
    """这条查询读到的数据新到哪一天 → [{"table", "column", "latest"}]。
    时间列取查询里用到的那一列;查询没用到时间列时取对象上声明的时间列;都没有就不给日期。"""
    out = []
    for t in tables[:6]:
        if not _IDENT.match(t):
            continue
        cols = [c for c in _columns(connect, t) if _IDENT.match(c)]
        used = [c for c in cols if _TIME_NAME.search(c) and re.search(r"(?<![A-Za-z0-9_])" + re.escape(c) + r"(?![A-Za-z0-9_])", sql)]
        declared = _declared_time(ir, t)
        col = used[0] if used else (declared if declared in cols else None)
        if not col:
            continue
        latest = _scalar(connect, f'SELECT MAX("{col}") FROM "{t}"')
        if latest is not None and _DATE_VALUE.match(str(latest)):
            out.append({"table": t, "column": col, "latest": str(latest)[:10]})
    return out[:3]


def _failed(steps):
    out = []
    for s in steps or []:
        if not isinstance(s, dict) or s.get("ok") is not False:
            continue
        info = str(s.get("info") or "")
        if s.get("step") == "exec_sql":
            title, _sep, err = info.partition(": ")
            out.append({"title": title if _sep else "", "kind": "error", "error": (err if _sep else info)[:160]})
        elif s.get("step") == "ontology_gate" and not info.endswith("(按原因修正一次)"):
            out.append({"title": "", "kind": "blocked", "error": info[:160]})
    return out


def annotate(results, steps, connect, ir=None):
    """给每个结果标上状态(及空结果的核对、实际取数日期),汇总失败的查询。
    返回 {"status": {data, empty, failed}, "failed": [...], "flags": [...], "kv": [(名称, 值)]}。"""
    flags, kv = [], []
    n_data = n_empty = 0
    for r in results or []:
        if not isinstance(r, dict):
            continue
        rows = ((r.get("data") or {}).get("rows")) or []
        r["status"] = "data" if rows else "empty"
        n_data += bool(rows)
        n_empty += not rows
        sql = str(r.get("sql") or "")
        if not sql:
            continue
        tables = tables_read(sql, connect)
        if rows:
            when = as_of(sql, tables, connect, ir)
            if when:
                r["data_as_of"] = when
                for w in when:
                    item = ("实际取数截至", f"{w['table']}.{w['column']} 最新 {w['latest']}")
                    if item not in kv:
                        kv.append(item)
            continue
        cov = [{"table": t, "rows": n} for t in tables[:6] if _IDENT.match(t)
               for n in [_scalar(connect, f'SELECT COUNT(*) FROM "{t}"')] if n is not None]
        if not cov:
            continue
        r["coverage"] = cov
        parts = "、".join(f"{c['table']} 共 {c['rows']} 行" if c["rows"] else f"{c['table']} 表里没有数据" for c in cov)
        why = "是筛选条件没有命中,请核对时间范围和取值" if all(c["rows"] for c in cov) else "数据可能尚未接入"
        flags.append(f"「{r.get('title') or '查询'}」返回 0 行:所查的 {parts},{why};不能据此认为业务上不存在")
    failed = _failed(steps)
    if failed:
        detail = ";".join((f["title"] + ":" if f["title"] else "") + f["error"] for f in failed[:3])
        flags.append(f"有 {len(failed)} 条查询没有取到数据({detail}),这部分结论缺少依据,不代表业务上没有发生")
    return {"status": {"data": n_data, "empty": n_empty, "failed": len(failed)}, "failed": failed[:8], "flags": flags, "kv": kv[:3]}

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
# 时间列按名字里的整词认:order_date、created_time、stat_month 是;downtime_minutes、update_count 不是
_TIME_NAME = re.compile(r"((^|_)(date|time|datetime|timestamp|day|dt|ts|month|period|ym|yyyymm)($|_)|_at$|日期|时间|月份|年月|期间|账期)", re.I)
# 取值要像日期:2026-11-08、2026/11、202611、20261108;纯数字的计数、时间戳不算
_DATE_VALUE = re.compile(r"^(?:(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])(?:[-/]\d{1,2})?(?:[ T].*)?|(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:[0-3]\d)?)$")
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
    时间列取查询里用到的那一列;查询没用到时取对象上声明的时间列;再没有,表里只有一个时间列时取它;否则不给日期。"""
    out = []
    for t in tables[:6]:
        if not _IDENT.match(t):
            continue
        cols = [c for c in _columns(connect, t) if _IDENT.match(c)]
        used = [c for c in cols if _TIME_NAME.search(c) and re.search(r"(?<![A-Za-z0-9_])" + re.escape(c) + r"(?![A-Za-z0-9_])", sql)]
        declared = _declared_time(ir, t)
        timelike = [c for c in cols if _TIME_NAME.search(c)]
        candidates = used or ([declared] if declared in cols else (timelike if len(timelike) == 1 else []))
        for col in candidates[:3]:               # 名字像时间列但存的不是日期(时间戳、时长)时,换下一列
            latest = _scalar(connect, f'SELECT MAX("{col}") FROM "{t}"')
            if latest is not None and _DATE_VALUE.match(str(latest).strip()):
                out.append({"table": t, "column": col, "latest": str(latest).strip()[:10]})
                break
    return out[:3]


def _failed(steps):
    """没有产出结果的查询。执行出错后经核对改写成功的、被拦下后仍按精确作答展示的,结果都在答案里,不算。"""
    out: list[dict] = []
    last = None                                  # 上一步若是执行出错,记下它在 out 里的位置
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        step, ok, info = s.get("step"), s.get("ok"), str(s.get("info") or "")
        if step == "self_check" and ok is True and info.startswith("按结果核对后改写") and last is not None:
            out.pop(last)
        elif step == "exact_fallback" and info.startswith("精确作答:采用"):
            first = next((i for i, f in enumerate(out) if f["kind"] == "blocked"), None)
            if first is not None:
                out.pop(first)
        last = None
        if ok is not False:
            continue
        if step == "exec_sql":
            title, _sep, err = info.partition(": ")
            out.append({"title": title if _sep else "", "kind": "error", "error": (err if _sep else info)[:160]})
            last = len(out) - 1
        elif step == "ontology_gate" and not info.endswith("(按原因修正一次)"):
            out.append({"title": "", "kind": "blocked", "error": info[:160]})
    return out


def _no_value(rows):
    """没有行,或只有一行且各列都是空值(SUM / AVG 在没有命中的行时返回的就是这样一行)。"""
    return not rows or (len(rows) == 1 and isinstance(rows[0], dict) and all(v is None for v in rows[0].values()))


def annotate(results, steps, connect, ir=None, explain_empty=True):
    """给每个结果标上状态(及空结果的核对、实际取数日期),汇总失败的查询。
    explain_empty=False 时不对空结果加说明:按规则判定、汇总异常的答案里,0 条命中本身就是结论。
    返回 {"status": {data, empty, failed}, "failed": [...], "flags": [...], "kv": [(名称, 值)]}。"""
    flags, kv = [], []
    n_data = n_empty = 0
    for r in results or []:
        if not isinstance(r, dict):
            continue
        rows = ((r.get("data") or {}).get("rows")) or []
        empty = _no_value(rows)
        r["status"] = "empty" if empty else "data"
        n_data += not empty
        n_empty += empty
        sql = str(r.get("sql") or "")
        if not sql or (empty and not explain_empty):
            continue
        tables = tables_read(sql, connect)
        if not empty:
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
        what = "返回 0 行" if not rows else "没有取到值(汇总结果为空)"
        flags.append(f"「{r.get('title') or '查询'}」{what}:所查的 {parts},{why};不能据此认为业务上不存在")
    failed = _failed(steps)
    if failed:
        detail = ";".join((f["title"] + ":" if f["title"] else "") + f["error"] for f in failed[:3])
        flags.append(f"有 {len(failed)} 条查询没有取到数据({detail}),这部分结论缺少依据,不代表业务上没有发生")
    return {"status": {"data": n_data, "empty": n_empty, "failed": len(failed)}, "failed": failed[:8], "flags": flags, "kv": kv[:3]}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""外部 MySQL/Doris 只读抽样到本地 SQLite:让外部库也能进入半自动构建的数据验证。

背景:构建取证与关系裁决只读 SQLite。外部库登记后只能浏览,不能构建;而关系能否从
「待取证」升到「已验证」,取决于有没有真实数据——只凭设计文档提出的关系无法做取值重叠验证,
抽样到本地后才能逐条裁决。

只读由三层保证(账号本身可能有写权限):
  1. 语句白名单,只放行 SELECT / SHOW(并拒绝 INTO OUTFILE / DUMPFILE 等写文件子句);
  2. 会话级只读事务(SET SESSION TRANSACTION READ ONLY),即便白名单被绕过,服务端也拒写;
  3. 单条查询超时(MySQL 5.7+ max_execution_time),不给源库加压。

抽样规则:≤FULL 行的表全量导出(作为被引用方时取值完整);更大的表按主键倒序取最新
SAMPLE 行。行数来自 information_schema 的估计值,可能偏小,因此全量导出也设 FULL 行上限,
触顶的表记入 truncated。大文本/二进制列不导出。表与字段注释写入 _dm_tables / _dm_columns,构建时作为
数据字典证据检索——实库注释往往是最完整的中文语义。

连接由调用方建立后传入,本模块不读取、不保存凭据。
"""
from __future__ import annotations

import os
import re
import sqlite3
import time

FULL, SAMPLE = 50_000, 20_000
_ALLOW = re.compile(r"^\s*(select|show)\b", re.I)
_DENY = re.compile(r"\b(insert|update|delete|replace|alter|drop|create|truncate|grant|rename|lock|call|load|handler|set|"
                   r"outfile|dumpfile)\b", re.I)
_SKIP_TYPE = re.compile(r"(blob|binary|longtext|mediumtext|json|geometry)", re.I)


class ReadOnlyCursor:
    """只放行查询语句的游标包装。"""

    def __init__(self, con):
        self.con = con

    def q(self, sql, args=None):
        body = re.sub(r"'[^']*'|`[^`]*`", "", sql)          # 引号/反引号内的内容不参与关键字判定
        if not _ALLOW.match(sql) or _DENY.search(body):
            raise PermissionError("只读抽样拒绝了非查询语句")
        cur = self.con.cursor()
        try:
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description or []]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()


def open_readonly(con, statement_ms=15000):
    """把一个已建立的 DB-API 连接切到只读会话。失败即中止,不退化为可写会话。"""
    cur = con.cursor()
    try:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        try:
            cur.execute(f"SET SESSION max_execution_time={int(statement_ms)}")
        except Exception:
            pass                                               # Doris/老版本无此变量:只读事务仍生效
    finally:
        cur.close()
    return ReadOnlyCursor(con)


def _affinity(t):
    t = (t or "").lower()
    if re.match(r"(tiny|small|medium|big)?int|bit|bool", t):
        return "INTEGER"
    if re.match(r"(decimal|numeric|double|float|real)", t):
        return "REAL"
    return "TEXT"


def _qi(name):
    return '"' + str(name).replace('"', '""') + '"'


def _bq(name):
    return "`" + str(name).replace("`", "``") + "`"


def _conv(v):
    """驱动返回值 → SQLite 可存的标量。BIT 列以 bytes 返回:≤8 字节按大端整数存,更长的存十六进制。"""
    if v is None or isinstance(v, (int, float, str)):
        return v
    if isinstance(v, (bytes, bytearray)):
        return int.from_bytes(v, "big") if len(v) <= 8 else bytes(v).hex()
    return str(v)


def open_target(out_path):
    """新建抽样目标库(写到 .part,全部完成后再 finish 替换,半截产物不会被当成数据源)。"""
    tmp = out_path + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    dst = sqlite3.connect(tmp)
    dst.execute("CREATE TABLE _dm_tables (table_name TEXT PRIMARY KEY, source_table TEXT, comment TEXT, rows_est INTEGER)")
    dst.execute("CREATE TABLE _dm_columns (table_name TEXT, column_name TEXT, type TEXT, comment TEXT)")
    return dst


def finish_target(dst, out_path):
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    os.replace(out_path + ".part", out_path)


def sample(ro, out_path, prefix="", **kw):
    """单库抽样:ro 为 open_readonly 返回的只读游标。→ 统计字典。out_path 若存在会被替换。"""
    dst = open_target(out_path)
    try:
        stats = sample_into(ro, dst, prefix=prefix, **kw)
    except BaseException:
        dst.close()
        raise
    finish_target(dst, out_path)
    return stats


def sample_into(ro, dst, prefix="", full=FULL, sample_rows=SAMPLE, progress=None, table_filter=None):
    """把一个只读源库抽样写入已打开的目标库(多库合并时表名加 prefix 区分)。"""
    tabs = ro.q("SELECT TABLE_NAME t, TABLE_ROWS n, TABLE_COMMENT c FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME")
    cols = ro.q("SELECT TABLE_NAME t, COLUMN_NAME c, COLUMN_TYPE ty, COLUMN_KEY k, COLUMN_COMMENT cm "
                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION")
    by = {}
    for c in cols:
        by.setdefault(c["t"], []).append(c)
    if table_filter:
        tabs = [t for t in tabs if table_filter(t["t"])]
    stats = {"tables": 0, "rows": 0, "failed": [], "sampled": [], "truncated": [], "started": time.time()}
    existing = {r[0].lower() for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for i, t in enumerate(tabs, 1):
        name = prefix + t["t"]
        if name.lower() in existing:                   # 多库合并时前缀相同导致重名:跳过并记录,不中断整批
            stats["failed"].append({"table": name, "error": "duplicate_name"})
            continue
        existing.add(name.lower())
        cs = by.get(t["t"], [])
        keep = [c for c in cs if not _SKIP_TYPE.search(c["ty"] or "")]
        pk = [c["c"] for c in cs if c["k"] == "PRI"]
        ddl = ", ".join(f"{_qi(c['c'])} {_affinity(c['ty'])}" for c in cs)
        if pk and all(p in {c['c'] for c in keep} for p in pk):
            ddl += ", PRIMARY KEY (" + ", ".join(_qi(p) for p in pk) + ")"
        dst.execute(f"CREATE TABLE {_qi(name)} ({ddl})")
        est = int(t["n"] or 0)
        dst.execute("INSERT INTO _dm_tables VALUES (?,?,?,?)", (name, t["t"], t["c"] or "", est))
        dst.executemany("INSERT INTO _dm_columns VALUES (?,?,?,?)",
                        [(name, c["c"], c["ty"], c["cm"] or "") for c in cs])
        if keep:
            sql = f"SELECT {', '.join(_bq(c['c']) for c in keep)} FROM {_bq(t['t'])}"
            order = (" ORDER BY " + ", ".join(f"{_bq(p)} DESC" for p in pk)) if pk else ""
            cap = int(sample_rows) if est > full else int(full)
            sql += f"{order} LIMIT {cap}"
            if est > full:
                stats["sampled"].append(name)
            try:
                rows = ro.q(sql)
                if est <= full and len(rows) >= cap:     # 估计行数偏小,全量导出触顶
                    stats["truncated"].append(name)
                if rows:
                    names = [c["c"] for c in keep]
                    dst.executemany(
                        f"INSERT OR IGNORE INTO {_qi(name)} ({', '.join(_qi(n) for n in names)}) "
                        f"VALUES ({', '.join('?' * len(names))})",
                        [tuple(_conv(r[n]) for n in names) for r in rows])
                    stats["rows"] += len(rows)
            except PermissionError:
                raise
            except Exception as exc:
                stats["failed"].append({"table": name, "error": type(exc).__name__})
        dst.commit()
        stats["tables"] += 1
        if progress:
            progress(i, len(tabs), name)
    stats["seconds"] = round(time.time() - stats.pop("started"), 1)
    return stats

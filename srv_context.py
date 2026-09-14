#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享上下文 —— DR-043 蓝图化的前置基座。

把 server.py 里**无路由/无 app 依赖**的基础原语抽到此处,供 server 与后续各
Flask blueprint **共同 import**(避免 blueprint↔server 循环导入)。只含 stdlib 依赖、
不引用任何路由态/业务全局,故抽取零风险、可独立测试。

首批:只读连接、只读 SQL 判定、写锁、JSON/文本原子写——各 blueprint 落盘/取数都要用。
路径(DB/WORK 等)与引擎自举仍留在 server.py(与 sys.path 装配耦合),按需再迁。
"""
import json
import os
import re
import sqlite3
import tempfile
import threading
from pathlib import Path


def confine(base, *parts):
    """把 parts 拼到 base 下,校验解析后仍在 base 内;逃逸抛 ValueError。

    路径限定的单一实现:此前 server(normpath 版)与 standard_assets(realpath 版)
    各有一份——安全关键代码存在两个略异的副本,修一处漏一处。统一用 realpath:
    除 ../ 外连符号链接逃逸也拦得住,严格强于原 normpath 版。
    """
    import os as _os
    p = _os.path.realpath(_os.path.join(base, *(str(x) for x in parts)))
    b = _os.path.realpath(base)
    if p != b and not p.startswith(b + _os.sep):
        raise ValueError("路径越界,拒绝访问:%s" % p)
    return p

# ── 基础路径(env 可覆盖):只读数据源 / 上传库 / 工作目录。与 server 同目录,值与旧定义逐字一致。
#    PLATFORM/OUTPUTS 与 sys.path 引擎自举仍留 server(与装配耦合),此处只收无副作用的路径。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.environ.get("DATAMIND_DB", os.path.join(ROOT, "demo_metrics.db"))
WORK = os.path.abspath(
    os.environ.get("DATAMIND_WORKDIR") or os.path.join(HERE, "workdir")
)
UPLOAD_DB = os.path.join(WORK, "uploads.db")
os.makedirs(WORK, exist_ok=True)


def ro_connect(path):
    """Open the exact filename read-only, without a writable fallback.

    as_uri quotes ?, # and % in filenames; interpolating a raw filename into a
    SQLite URI can select a different database or inject mode=rw parameters.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据库不存在: {path}")
    return sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True)


# 只放行纯查询:允许 select / with,但 with 之后若出现 DML/DDL 关键字则拒绝
SAFE_SQL = re.compile(r"^\s*(select|with)\b", re.I)
_SQL_WRITE = re.compile(r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|pragma|vacuum|reindex|truncate)\b", re.I)


def sql_code(sql):
    """Mask SQL literals/identifiers/comments for shared read-only/single-statement guards.

    This deliberately rejects dialect-ambiguous escapes, nested comments and
    MySQL executable comments instead of guessing which SQL mode a driver uses.
    It is a lexical guard; source accounts must still lack write/file privileges.
    """
    if not isinstance(sql, str):
        return None
    out, i, n = [], 0, len(sql)
    while i < n:
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if end < 0 or sql.startswith(("/*!", "/*M!"), i) or "/*" in sql[i + 2:end]:
                return None
            out.append(" "); i = end + 2
        elif sql.startswith("--", i):
            if i + 2 < n and not sql[i + 2].isspace():
                return None  # MySQL requires whitespace, SQLite/Postgres do not
            end = sql.find("\n", i + 2)
            out.append(" "); i = n if end < 0 else end + 1
        elif sql[i] == "#":
            return None  # MySQL comment versus PostgreSQL operator
        elif sql[i] in ("'", '"', "`", "["):
            quote = "]" if sql[i] == "[" else sql[i]
            i += 1
            while i < n:
                if sql[i] == "\\":
                    return None  # backslash escaping differs with MySQL/PG SQL modes
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        i += 2; continue
                    i += 1; break
                i += 1
            else:
                return None
            out.append(" ? ")
        elif sql[i] == "$" and re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:]):
            return None  # PostgreSQL string syntax, but MySQL identifier syntax
        else:
            out.append(sql[i]); i += 1
    return "".join(out)


def sql_is_readonly(sql):
    if not isinstance(sql, str) or not SAFE_SQL.match(sql):
        return False
    s = sql_code(sql)
    if s is None or re.search(r"\binto\b", s, re.I):
        return False  # SELECT INTO / INTO OUTFILE / INTO DUMPFILE are writes too
    # WITH 开头需排除内嵌写语句(WITH cte AS(...) DELETE ...)
    if re.match(r"^\s*with\b", s, re.I) and _SQL_WRITE.search(s):
        return False
    return True


# 保护 json 文件读-改-写(edits/chats),防并发丢更新/损坏。server 与各 blueprint 共用同一把锁。
_WRITE_LOCK = threading.RLock()


def _atomic_json(path, data):
    """原子写:先写 .tmp 再 os.replace,避免中途崩溃截断已存文件(会话/编辑/技能状态不丢)"""
    _atomic_text(path, json.dumps(data, ensure_ascii=False))


def _checked_write_path(path):
    """公共落盘 sink 的最小路径守卫。"""
    if not path or ".." in str(path).split(os.sep):
        raise ValueError("拒绝写入含上级目录引用的路径")
    return str(path)


def _atomic_text(path, text):
    """文本文件的原子写(SKILL.md / OWL Turtle 等)。与 _atomic_json 同一规范:
    先写 .tmp 再 os.replace,避免写到一半失败留下截断文件。

    路径由调用方裁决(server 侧统一走 _confined),但此处仍拒绝含 '..' 的路径:
    本函数是全仓所有文本落盘的公共 sink,任何一处调用点漏了校验都会在这里被兜住。
    """
    path = _checked_write_path(path)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp",
                               dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _atomic_bytes(path, data, mode=None):
    """二进制原子写；上传附件与密钥不得直接截断目标文件。"""
    path = _checked_write_path(path)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp",
                               dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
        if mode is not None:
            os.chmod(tmp, mode)             # 权限在发布前生效，不留下短暂的 0644 窗口
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

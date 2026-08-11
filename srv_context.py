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
import threading

# ── 基础路径(env 可覆盖):数据底座 / 上传库 / 工作目录。与 server 同目录,值与旧定义逐字一致。
#    PLATFORM/OUTPUTS 与 sys.path 引擎自举仍留 server(与装配耦合),此处只收无副作用的路径。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.environ.get("DATAMIND_DB", os.path.join(ROOT, "demo_metrics.db"))
UPLOAD_DB = os.path.join(HERE, "workdir", "uploads.db")
WORK = os.path.join(HERE, "workdir")
os.makedirs(WORK, exist_ok=True)


def ro_connect(path):
    """统一只读连接:mode=ro 打开;缺库时响亮失败(不静默新建空库,防丢库被掩盖)。
    仅当 URI 不受支持时才退回普通连接,且仍先确认文件存在 + 强制 query_only。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"数据库不存在: {path}")
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except Exception:
        con = sqlite3.connect(path)  # 极端情况(URI 不支持)退回普通连接,但库已确认存在,不会误建
        try:
            con.execute("PRAGMA query_only=ON")
        except Exception:
            pass
        return con


# 只放行纯查询:允许 select / with,但 with 之后若出现 DML/DDL 关键字则拒绝
SAFE_SQL = re.compile(r"^\s*(select|with)\b", re.I)
_SQL_WRITE = re.compile(r"\b(insert|update|delete|replace|drop|alter|create|attach|detach|pragma|vacuum|reindex|truncate)\b", re.I)


def sql_is_readonly(sql):
    s = sql or ""
    if not SAFE_SQL.match(s):
        return False
    # select 开头天然安全;with 开头需排除内嵌写语句(WITH cte AS(...) DELETE ...)
    if re.match(r"^\s*with\b", s, re.I) and _SQL_WRITE.search(s):
        return False
    return True


# 保护 json 文件读-改-写(edits/chats),防并发丢更新/损坏。server 与各 blueprint 共用同一把锁。
_WRITE_LOCK = threading.RLock()


def _atomic_json(path, data):
    """原子写:先写 .tmp 再 os.replace,避免中途崩溃截断已存文件(会话/编辑/技能状态不丢)"""
    _atomic_text(path, json.dumps(data, ensure_ascii=False))


def _atomic_text(path, text):
    """文本文件的原子写(SKILL.md / OWL Turtle 等)。与 _atomic_json 同一纪律:
    先写 .tmp 再 os.replace,避免写到一半失败留下截断文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w") as fp:
        fp.write(text)
    os.replace(tmp, path)

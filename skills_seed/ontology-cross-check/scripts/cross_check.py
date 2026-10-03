#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多通道交叉复核:在取值重叠之外,用互相独立的数据证据复核已构建本体中的关系。

取值重叠只回答「子键的值在父键里出现过没有」。两张表的自增 id 恰好落在同一区间、
几乎每张表都带的企业编号,都能让重叠率达标。本脚本对每条关系再看几条独立通道:

- 冗余名称:子表与父表都存了名称(如 ent_no/ent_name),按键关联后名称应当一致;
  父键是自增 id 时不一致为强冲突,父键是业务编码时多为冗余名称过期,为弱冲突;
- 自增 id 碰撞:父键是 id、子键名与父表名无关、名称通道也不支持;
- 双向:同一对表在两个方向上都被判成已验证;
- 软删除:只保留父表未删除行后,重叠率是否仍达标;
- 时间先后:子记录的创建时间不应系统性早于它引用的父记录;
- 租户键:被大量表共用的编号列,「归属」在数据上成立,但不是业务关系;
- 按类型分条件:未通过的关系若子表带判别列,按判别值分开看重叠率;
- 字段注释:库内字段注释(dim_column_metadata)写明「外键,关联 X」时,连到别的表为强冲突、连到 X 为支持;
  注释写明是本表主键(且没写外键)时,把它当外键连到别的表为强冲突。同名键(order_id、line_id)在多张表里
  都是主键、整数区间又重合时,取值重叠分不出该连哪张,注释是库作者给出的直接说明。

只读 SQLite,只用标准库。名称不一致(父键为自增 id)与 id 碰撞是强冲突,其余是弱冲突(只送复核)。
默认只出报告;--apply 时把强冲突的已验证关系降为候选并写明原因,
另存为新文件,不改原文件。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

THETA = 60.0
NAME_SUPPORT, NAME_REFUTE = 0.9, 0.5
TEMPORAL_LIMIT, MIN_PAIRS = 0.2, 20
HUB_SHARE, HUB_MIN = 0.2, 10
MIN_PARTITION = 5
MIN_PROBE_MATCH = 30
TIME_COLS = ("create_at", "create_time", "create_date", "created_at", "gmt_create", "ctime")
# 逻辑删除列名规则与表示「未删除」的取值(None 即 NULL);须与 specs/models/valid_rows_rules.json 的
# soft_delete.detect 一致(脚本独立发布、不导入仓库模块,由 tests/unit/test_live_rows.py 核对)
DEL_COL_PATTERN = r"^(is_?del(eted)?|del_?flag|delete_?flag|deleted(_flag)?|is_?delete)$"
LIVE_MARKERS = (None, 0, "0", "N", "n", "false", "")
_DEL_COL = re.compile(DEL_COL_PATTERN, re.I)
_LIVE_SQL = ",".join(str(v) if isinstance(v, int) else "'" + v + "'" for v in LIVE_MARKERS if v is not None)
_SUFFIX = re.compile(r"_(no|id|code|key|num)$", re.I)
# 「外键,关联fact_x」「引用产线(dim_production_line.line_id)」:关键词后 20 字内的第一个英文标识符
_COMMENT_REF = re.compile(r"(?:外键|关联|引用)[^A-Za-z_,，;；。]{0,20}([A-Za-z_][A-Za-z0-9_]*)")
_COMMENT_PK = re.compile(r"主键")


def q(name):
    return '"' + str(name).replace('"', '""') + '"'


def ro_connect(path):
    """只读打开 SQLite。路径先转成 file: URI,文件名里的 ? 或 # 不会被当成 URI 参数。"""
    return sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True)


class Db:
    def __init__(self, path):
        self.con = ro_connect(path)
        self._cols = {}
        self.tables = [r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '\\_dm\\_%' ESCAPE '\\'")]

    def cols(self, table):
        if table not in self._cols:
            try:
                self._cols[table] = [r[1] for r in self.con.execute(f"PRAGMA table_info({q(table)})")]
            except sqlite3.Error:
                self._cols[table] = []
        return self._cols[table]

    def col(self, table, name):
        return next((c for c in self.cols(table) if c.lower() == name.lower()), None)

    def comment(self, table, col):
        """字段注释:只读抽样保存的 _dm_columns 优先,否则用库内数据字典 dim_column_metadata(中文名 + 说明)。"""
        if not hasattr(self, "_comments"):
            self._comments = {}
            has_dm = self.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_dm_columns'")
            if has_dm:
                try:
                    for t, c, cm in self.con.execute("SELECT table_name, column_name, comment FROM _dm_columns"):
                        self._comments[(str(t).lower(), str(c).lower())] = cm or ""
                except sqlite3.Error:
                    pass
            elif "dim_column_metadata" in self.tables:
                try:
                    for t, c, cn, desc in self.con.execute(
                            "SELECT table_name, column_name, column_name_cn, column_desc FROM dim_column_metadata"):
                        self._comments[(str(t).lower(), str(c).lower())] = f"{cn or ''} {desc or ''}"
                except sqlite3.Error:
                    pass
        return self._comments.get((table.lower(), col.lower()), "")

    def one(self, sql, args=()):
        try:
            return self.con.execute(sql, args).fetchone()
        except sqlite3.Error:
            return None


def _stem(key):
    return _SUFFIX.sub("", key or "").lower()


def _is_live(value):
    return value is None or any(m is not None and str(m) == str(value) for m in LIVE_MARKERS) or (
        isinstance(value, float) and value == 0)


def _live(db, table, alias):
    col = next((c for c in db.cols(table) if _DEL_COL.match(c)), None)
    return col, (f" AND ({alias}.{q(col)} IS NULL OR {alias}.{q(col)} IN ({_LIVE_SQL}))" if col else "")


def _markers(db, table, col):
    """删除标记列实际出现的取值,分成「未删除」与「已删除」两组(问数据此生成关联条件)。"""
    try:
        vals = [r[0] for r in db.con.execute(f"SELECT DISTINCT {q(col)} FROM {q(table)} LIMIT 50")]
    except sqlite3.Error:
        return [], []
    vals = [v if v is None or isinstance(v, (int, str)) else (int(v) if isinstance(v, float) and v.is_integer() else str(v))
            for v in vals]
    key = lambda v: (v is not None, str(v))                                 # noqa: E731
    return sorted((v for v in vals if _is_live(v)), key=key), sorted((v for v in vals if not _is_live(v)), key=key)


def comment_channel(db, ct, ck, pt):
    """字段注释点名了父表:是这张为支持,是别的表为冲突;注释说是本表主键而没写外键,连到别的表为冲突。"""
    text = db.comment(ct, ck)
    if not text:
        return {"state": "none"}
    tables = {t.lower(): t for t in db.tables}
    named = [tables[m.lower()] for m in _COMMENT_REF.findall(text) if m.lower() in tables and m.lower() != ct.lower()]
    if named:
        if pt.lower() in {n.lower() for n in named}:
            return {"state": "support", "comment": text.strip()[:80]}
        return {"state": "conflict", "reason": f"字段注释写明关联 {named[0]}", "comment": text.strip()[:80]}
    if _COMMENT_PK.search(text) and ct.lower() != pt.lower():
        return {"state": "conflict", "reason": "字段注释写明为本表主键,不是外键", "comment": text.strip()[:80]}
    return {"state": "none"}


def overlap(db, ct, ck, pt, pk, parent_filter=""):
    r = db.one(f"""SELECT COUNT(*), SUM(CASE WHEN EXISTS(SELECT 1 FROM {q(pt)} p WHERE p.{q(pk)}=c.v{parent_filter})
                   THEN 1 ELSE 0 END) FROM (SELECT DISTINCT {q(ck)} AS v FROM {q(ct)}
                   WHERE {q(ck)} IS NOT NULL AND {q(ck)}<>'') c""")
    if not r or not r[0]:
        return None, 0
    return round(100.0 * (r[1] or 0) / r[0], 1), r[0]


def name_channel(db, ct, ck, pt, pk):
    """子表按键冗余存储的名称,应与父表同一行的名称一致。"""
    stem = _stem(ck)
    cname = db.col(ct, stem + "_name") if stem else None
    if not cname:
        return {"state": "na"}
    pname = db.col(pt, stem + "_name") or db.col(pt, _stem(pk) + "_name") or db.col(pt, "name")
    if not pname:
        return {"state": "na"}
    r = db.one(f"""SELECT COUNT(*), SUM(CASE WHEN TRIM(c.{q(cname)})=TRIM(p.{q(pname)}) THEN 1 ELSE 0 END)
                   FROM (SELECT DISTINCT {q(ck)} AS k, {q(cname)} FROM {q(ct)}
                         WHERE {q(ck)} IS NOT NULL AND {q(cname)} IS NOT NULL AND TRIM({q(cname)})<>'') c
                   JOIN {q(pt)} p ON p.{q(pk)}=c.k""")
    if not r or (r[0] or 0) < MIN_PAIRS:
        return {"state": "na", "pairs": (r or [0])[0] or 0}
    rate = round((r[1] or 0) / r[0], 3)
    state = "support" if rate >= NAME_SUPPORT else ("conflict" if rate < NAME_REFUTE else "weak")
    return {"state": state, "pairs": r[0], "agree": rate, "columns": [f"{ct}.{cname}", f"{pt}.{pname}"]}


def temporal_channel(db, ct, ck, pt, pk):
    tc = next((db.col(ct, c) for c in TIME_COLS if db.col(ct, c)), None)
    tp = next((db.col(pt, c) for c in TIME_COLS if db.col(pt, c)), None)
    if not tc or not tp or ct == pt:
        return {"state": "na"}
    # 整列只有一两个时间值的是数据装载/生成时间,不是业务时间,不能据此判先后(DR-074)
    for t_, c_ in ((ct, tc), (pt, tp)):
        d = db.one(f"SELECT COUNT(DISTINCT {q(c_)}) FROM {q(t_)}")
        if not d or (d[0] or 0) <= 2:
            return {"state": "na", "reason": f"{t_}.{c_} 取值几乎恒定,是装载时间"}
    r = db.one(f"""SELECT COUNT(*), SUM(CASE WHEN julianday(p.{q(tp)}) - julianday(c.{q(tc)}) > 1 THEN 1 ELSE 0 END)
                   FROM {q(ct)} c JOIN {q(pt)} p ON p.{q(pk)}=c.{q(ck)}
                   WHERE julianday(c.{q(tc)}) IS NOT NULL AND julianday(p.{q(tp)}) IS NOT NULL""")
    if not r or (r[0] or 0) < MIN_PAIRS:
        return {"state": "na"}
    rate = round((r[1] or 0) / r[0], 3)
    return {"state": "conflict" if rate > TEMPORAL_LIMIT else "support", "pairs": r[0], "child_earlier": rate}


def soft_delete_channel(db, ct, ck, pt, pk, base):
    col, flt = _live(db, pt, "p")
    if not col or base is None:
        return {"state": "na"}
    ov, _ = overlap(db, ct, ck, pt, pk, flt)
    if ov is None:
        return {"state": "na"}
    return {"state": "conflict" if base >= THETA > ov else "support", "column": col, "overlap_live": ov}


def _prefix_abbrev(ck, pt):
    """词元前缀缩写:anal_obj_id → analysis_object.id(每个词元至少 3 个字母,依次是父表名相邻词元的前缀)。"""
    toks = [t for t in re.split(r"[_\W]+", re.sub(r"_(id|no|code)$", "", ck.lower())) if t]
    ptoks = [t for t in re.split(r"[_\W]+", pt.split("__")[-1].lower()) if t]
    if len(toks) < 2 or any(len(t) < 3 for t in toks):
        return False
    return any(all(ptoks[i + j].startswith(toks[j]) for j in range(len(toks)))
               for i in range(len(ptoks) - len(toks) + 1))


def id_collision(db, ct, ck, pt, pk, name_state):
    if pk.lower() != "id":
        return {"state": "na"}
    tokens = [t for t in re.split(r"[_\W]+", pt.split("__")[-1].lower()) if len(t) >= 3]
    related = any(t in ck.lower() for t in tokens) or (_stem(ck) and _stem(ck) in pt.lower()) \
        or _prefix_abbrev(ck, pt)
    if name_state != "support" and (ck.lower() == "id" or not related):
        return {"state": "conflict", "reason": f"{ck} → {pt}.id:子键名与父表无关,且没有名称证据"}
    return {"state": "na"}


def discriminator(db, ct, ck):
    """多态引用的判别列:obj_no ↔ obj_type、rel_obj_no ↔ rel_obj_type。"""
    stem = _stem(ck)
    # 退回 obj_type 只针对对象引用类的键(obj_no/obj_code/parent_no),避免把任意字段都当成多态
    fallback = ("obj_type",) if re.search(r"obj|parent", ck, re.I) else ()
    for cand in (stem + "_type",) + fallback:
        c = db.col(ct, cand) if cand and cand != ck else None
        if c:
            n = db.one(f"SELECT COUNT(DISTINCT {q(c)}) FROM {q(ct)}")
            if n and 1 < n[0] <= 12:
                return c
    return None


def conditional_channel(db, ct, ck, pt, pk):
    disc = discriminator(db, ct, ck)
    if not disc:
        return {"state": "na"}
    best = None
    for (v,) in db.con.execute(f"SELECT DISTINCT {q(disc)} FROM {q(ct)} WHERE {q(disc)} IS NOT NULL"):
        r = db.one(f"""SELECT COUNT(*), SUM(CASE WHEN EXISTS(SELECT 1 FROM {q(pt)} p WHERE p.{q(pk)}=c.v) THEN 1 ELSE 0 END)
                       FROM (SELECT DISTINCT {q(ck)} AS v FROM {q(ct)} WHERE {q(disc)}=? AND {q(ck)} IS NOT NULL) c""", (v,))
        if r and r[0]:
            ov = round(100.0 * (r[1] or 0) / r[0], 1)
            if r[0] >= MIN_PARTITION and (best is None or ov > best["overlap"]):
                best = {"value": v, "overlap": ov, "distinct": r[0]}
    if not best:
        return {"state": "na"}
    return {"state": "support" if best["overlap"] >= THETA else "weak", "discriminator": disc, **best}


NAME_HINT = re.compile(r"(_name|^name|_label|_desc)$", re.I)
SCOPE_COL = re.compile(r"(tenant|tenement|^ent_no$|^ent_id$|company|factory|plant|^org|site_code)", re.I)


def _tenant_scoped(db, ct, ck, pt, pk, where="", args=()):
    """编号按租户(企业、工厂、组织)复用:父表上「租户 + 编号」唯一、子表按「租户 + 编号」能对上。"""
    shared = [c for c in db.cols(pt) if SCOPE_COL.search(c) and db.col(ct, c)]
    for tc in shared:
        u = db.one(f"SELECT COUNT(*), COUNT(DISTINCT {q(tc)} || '\x1f' || {q(pk)}) FROM {q(pt)} "
                   f"WHERE {q(pk)} IS NOT NULL{where}", args)
        if not u or not u[0] or u[0] != u[1]:
            continue
        o = db.one(f"""SELECT COUNT(*), SUM(CASE WHEN EXISTS(SELECT 1 FROM {q(pt)} p WHERE p.{q(pk)} = c.k
                         AND p.{q(tc)} = c.t{where.replace(q(pk), 'p.' + q(pk)) if where else ''}) THEN 1 ELSE 0 END)
                       FROM (SELECT DISTINCT {q(tc)} t, {q(ck)} k FROM {q(ct)} WHERE {q(ck)} IS NOT NULL) c""", args)
        if o and o[0] and 100.0 * (o[1] or 0) / o[0] >= THETA:
            return {"column": tc, "overlap": round(100.0 * (o[1] or 0) / o[0], 1)}
    return None


def valid_rows_channel(db, ct, ck, pt, pk, ev, scopes=()):
    """重叠达标却因父键不唯一被拒的候选:只看有效行后父键是否唯一。三种「无效行 / 非全局键」:

    - 软删除:已删除行还在表里(业务库常见);
    - 版本重复:同一业务键多次入仓(湖仓常见)。重复的键若名称一致,就是同一实体的多个版本;
    - 分组编码:字典编码按分组复用,代码里写明了分组条件(dict_code = 'X'),只在组内唯一。
    """
    ov = ev.get("overlap")
    # 原裁决是方向反证(子列唯一、目标列不唯一):目标表才是「多」的一侧,有效行规则不适用(DR-071)
    if ev.get("direction") == "reverse":
        return {"state": "na"}
    if not isinstance(ov, (int, float)) or ov < THETA or ev.get("parent_unique") is not False:
        return {"state": "na"}
    r = db.one(f"SELECT COUNT(*), COUNT(DISTINCT {q(pk)}) FROM {q(pt)} WHERE {q(pk)} IS NOT NULL")
    if not r or not r[0]:
        return {"state": "na"}
    for sc in scopes or ():
        if sc.get("table") == ct and str(sc.get("column", "")).lower() == ck.lower() and sc.get("dictionary") == pt:
            gcol = db.col(pt, sc.get("group_column") or "")
            if gcol:
                g = db.one(f"SELECT COUNT(*), COUNT(DISTINCT {q(pk)}) FROM {q(pt)} WHERE {q(gcol)} = ?", (sc["group"],))
                if g and g[0] and g[0] == g[1]:
                    return {"state": "support", "rule": "scoped", "column": gcol, "group": sc["group"],
                            "note": f"{pt}.{pk} 在 {gcol}='{sc['group']}' 组内唯一(代码码表契约)"}
                ts = _tenant_scoped(db, ct, ck, pt, pk, f" AND {q(gcol)} = ?", (sc["group"],))
                if ts:
                    return {"state": "support", "rule": "scoped_tenant", "column": gcol, "group": sc["group"],
                            "tenant_column": ts["column"], "overlap": ts["overlap"],
                            "note": f"{pt}.{pk} 在 {gcol}='{sc['group']}' 组内、按 {ts['column']} 分租户唯一"
                                    f"(代码码表契约;按租户对上 {ts['overlap']}%)"}
    col, flt = _live(db, pt, "p")
    if col:
        live = db.one(f"SELECT COUNT(*), COUNT(DISTINCT p.{q(pk)}) FROM {q(pt)} p WHERE p.{q(pk)} IS NOT NULL{flt}")
        if live and live[0] and live[0] == live[1]:
            live_vals, dead_vals = _markers(db, pt, col)
            return {"state": "support", "rule": "soft_delete", "column": col,
                    "live_values": live_vals or [0], "deleted_values": dead_vals,
                    "note": f"排除已删除行后 {pt}.{pk} 唯一(未删除取值:"
                            f"{'、'.join('NULL' if v is None else repr(v) for v in live_vals) or '0'})"}
    ts = _tenant_scoped(db, ct, ck, pt, pk)
    if ts:
        return {"state": "support", "rule": "tenant_scoped", "tenant_column": ts["column"], "overlap": ts["overlap"],
                "note": f"{pt}.{pk} 按 {ts['column']} 分租户唯一,子表按租户对上 {ts['overlap']}%"}
    tcol = next((db.col(pt, c) for c in TIME_COLS + ("insert_time", "update_time", "edit_time") if db.col(pt, c)), None)
    dup = r[0] - r[1]
    if tcol and dup:
        stable = None
        name = next((c for c in db.cols(pt) if NAME_HINT.search(c) and c.lower() != pk.lower()), None)
        if name:
            s = db.one(f"""SELECT COUNT(*), SUM(CASE WHEN n = 1 THEN 1 ELSE 0 END) FROM (
                             SELECT {q(pk)} k, COUNT(DISTINCT {q(name)}) n FROM {q(pt)} WHERE {q(pk)} IN (
                               SELECT {q(pk)} FROM {q(pt)} GROUP BY 1 HAVING COUNT(*) > 1 LIMIT 500) GROUP BY 1)""")
            stable = (s[1] or 0) / s[0] if s and s[0] else None
        # 同键的多行若靠另一个标识列区分(量测实例:同一量测编码按设备编号各存一行),这是实例表,不是版本重复
        # (DR-072 实测把量测实例表认成了量测编码的父表)
        if _instance_split(db, pt, pk):
            return {"state": "na"}
        if dup <= 0.05 * r[0] or (stable is not None and stable >= 0.8):
            return {"state": "support", "rule": "keep_latest", "column": tcol, "duplicate_rows": dup,
                    "stable_name_share": None if stable is None else round(stable, 3),
                    "note": f"{pt}.{pk} 有 {dup} 行版本重复" + (f"(同键名称一致 {stable:.0%})" if stable else "")
                            + f",按 {tcol} 取最新后唯一"}
    return {"state": "na"}


_ID_COL = re.compile(r"_(no|id|code|sn)$", re.I)


def _instance_split(db, table, key):
    """同一键的重复行能被另一个标识列(编号、id、编码,不含主键 id)区分开:说明是实例表。"""
    for c in db.cols(table):
        if c.lower() in (key.lower(), "id") or not _ID_COL.search(c):
            continue
        r = db.one(f"SELECT COUNT(*), COUNT(DISTINCT {q(key)} || char(31) || {q(c)}), COUNT(DISTINCT {q(c)}) "
                   f"FROM {q(table)} WHERE {q(key)} IS NOT NULL AND {q(c)} IS NOT NULL")
        # 区分列须有足够多的取值(设备编号成百上千);模型、版本这类只有几个取值的列区分的是版本,不是实例。
        # 抽样数据里实例表偶有重复行,(键, 区分列)九成五以上唯一即可
        if r and r[0] and r[1] >= 0.95 * r[0] and (r[2] or 0) >= 20:
            return True
    return False


def hub_keys(db):
    cnt = Counter()
    for t in db.tables:
        for c in db.cols(t):
            if _SUFFIX.search(c) and c.lower() != "id":
                cnt[c.lower()] += 1
    need = max(HUB_MIN, int(len(db.tables) * HUB_SHARE))
    return {k: n for k, n in cnt.items() if n >= need}


def check(ir, db):
    scopes = ((ir.get("scenario") or {}).get("code_contracts") or {}).get("codebooks") or []
    objs = {o.get("name"): o for o in ir.get("objects") or []}
    table = lambda n: (objs.get(n) or {}).get("table") or n
    rels = ir.get("relations") or []
    hubs = hub_keys(db)
    verified_pairs = {(table(r.get("source_concept")), table(r.get("target_concept")))
                      for r in rels if r.get("status") == "verified"}
    out = []
    for i, r in enumerate(rels):
        ev = r.get("evidence") or {}
        ct, pt = table(r.get("source_concept")), table(r.get("target_concept"))
        ck, pk = ev.get("child_key") or "", ev.get("parent_key") or ""
        if not ck or not pk:
            prop = r.get("proposal") or {}
            ck, pk = prop.get("child_key_hint") or "", prop.get("parent_key_hint") or ""
        row = {"i": i, "source": r.get("source_concept"), "target": r.get("target_concept"), "verb": r.get("verb"),
               "status": r.get("status"), "child_key": ck, "parent_key": pk, "channels": {}, "tags": []}
        out.append(row)
        if "," in ck or "," in pk or not ck or not pk or ct not in db.tables or pt not in db.tables \
                or not db.col(ct, ck) or not db.col(pt, pk):
            row["verdict"] = "not_checkable"
            continue
        ck, pk = db.col(ct, ck), db.col(pt, pk)
        ch = row["channels"]
        if r.get("status") == "verified":
            base = ev.get("overlap") if isinstance(ev.get("overlap"), (int, float)) else overlap(db, ct, ck, pt, pk)[0]
            ch["name"] = name_channel(db, ct, ck, pt, pk)
            ch["id_collision"] = id_collision(db, ct, ck, pt, pk, ch["name"]["state"])
            if ct != pt and (pt, ct) in verified_pairs:
                ch["bidirectional"] = {"state": "conflict", "reason": "两个方向都被判为已验证"}
            ch["soft_delete"] = soft_delete_channel(db, ct, ck, pt, pk, base)
            ch["temporal"] = temporal_channel(db, ct, ck, pt, pk)
            if ck.lower() in hubs:
                row["tags"].append("tenant_or_hub_key")
            ch["comment"] = comment_channel(db, ct, ck, pt)
            if ev.get("contract"):                     # 代码里就这么连:独立于数据的一条证据
                ch["contract"] = {"state": "support", "code": ev["contract"].get("code"), "file": ev["contract"].get("file")}
            # 强冲突直接说明「这不是一条连接关系」;弱冲突可能有业务原因(补建的分组、引用已删除的父行、
            # 一对一扩展表),只送人工复核,不改裁决。
            # 名称不一致:父键是自增 id 时说明两边编号体系对不上(强);父键是业务编码时
            # 更可能是子表冗余存的名称过期了,关系本身仍成立(弱)
            name_bad = ch["name"].get("state") == "conflict"
            if name_bad and pk.lower() != "id":
                ch["name"]["stale"] = True
            strong = [k for k in ("id_collision", "comment") if ch.get(k, {}).get("state") == "conflict"]
            if name_bad and pk.lower() == "id":
                strong.insert(0, "name")
            weak = [k for k in ("bidirectional", "soft_delete", "temporal") if ch.get(k, {}).get("state") == "conflict"]
            if name_bad and pk.lower() != "id":
                weak.insert(0, "name")
            support = [k for k in ("name", "temporal", "contract", "comment") if ch.get(k, {}).get("state") == "support"]
            row["conflicts"], row["weak_conflicts"], row["supports"] = strong, weak, support
            row["verdict"] = ("refuted" if strong else "review" if weak else
                              "corroborated" if support else "single_channel")
        else:
            ch["conditional"] = conditional_channel(db, ct, ck, pt, pk)
            ch["valid_rows"] = valid_rows_channel(db, ct, ck, pt, pk, ev, scopes)
            row["verdict"] = ("conditional_candidate" if ch["conditional"]["state"] == "support" else
                              "valid_rows_candidate" if ch["valid_rows"]["state"] == "support" else "unchanged")
    return out, hubs


def summarize(rows, hubs):
    ver = [r for r in rows if r["status"] == "verified"]
    c = Counter(r["verdict"] for r in rows)
    reasons = Counter(k for r in ver for k in r.get("conflicts", []))
    return {"relations": len(rows), "verified": len(ver),
            "verdicts": dict(c), "refuted_by": dict(reasons),
            "tenant_or_hub_key": sum("tenant_or_hub_key" in r["tags"] for r in ver),
            "hub_keys": dict(sorted(hubs.items(), key=lambda kv: -kv[1])[:10])}


def apply(ir, rows):
    """强冲突的已验证关系降为候选;原裁决与复核结果写进 evidence,不删除任何关系。"""
    for row in rows:
        r = ir["relations"][row["i"]]
        ev = r.setdefault("evidence", {})
        ev["cross_check"] = {k: row.get(k) for k in ("verdict", "channels", "conflicts", "weak_conflicts", "supports", "tags")
                             if row.get(k) is not None}
        if row.get("verdict") == "refuted":
            ev["cross_check"]["prev_status"] = r.get("status")
            r["status"] = "candidate"
            r["note"] = "交叉复核冲突(" + "、".join(row["conflicts"]) + "),待人工复核;原裁决:" + str(r.get("note") or "")
        elif row.get("verdict") == "valid_rows_candidate" and ev.get("name_ok") is True:
            # 有效行上父键唯一、重叠达标、键名有据(含代码契约):与数据裁决同一口径,只是先定了有效行
            vr = row["channels"]["valid_rows"]
            ev["cross_check"]["prev_status"] = r.get("status")
            ev["valid_rows"] = {k: vr.get(k) for k in ("state", "rule", "note", "column", "group", "tenant_column",
                                                   "live_values", "deleted_values")
                                if vr.get(k) is not None}      # 原始 parent_unique 保持 false,不改写
            r["status"] = "verified"
            r["note"] = f"有效行复核通过({vr.get('note')});原裁决:" + str(r.get("note") or "")
    return ir


def usable_relations(ir, rows, db):
    """清洗沿用的关系:已验证且未被推翻的,以及按判别条件成立的候选。"""
    objs = {o.get("name"): o for o in ir.get("objects") or []}
    table = lambda n: (objs.get(n) or {}).get("table") or n
    rels = ir.get("relations") or []
    out = []
    for row in rows:
        verdict = row.get("verdict")
        cond = row["channels"].get("conditional") or {}
        matched = ((rels[row["i"]].get("evidence") or {}).get("matched_distinct") or 0)
        if cond.get("overlap") is not None:
            matched = max(matched, int(cond["overlap"] * cond.get("distinct", 0) / 100))
        # 重叠率不达标但匹配量足够的候选作为「探针」:可能是孤儿记录拉低了重叠率,交给清洗环节看语义是否一致
        probe = verdict == "unchanged" and matched >= MIN_PROBE_MATCH
        if verdict not in ("corroborated", "single_channel", "review", "conditional_candidate") and not probe:
            continue
        ct, pt = table(row["source"]), table(row["target"])
        ck, pk = db.col(ct, row["child_key"]), db.col(pt, row["parent_key"])
        if not ck or not pk:
            continue
        where, pair = "", None
        if cond.get("discriminator") and verdict in ("conditional_candidate", "unchanged"):
            val = str(cond["value"]).replace("'", "''")
            where = f"{q(cond['discriminator'])}='{val}'"
            pair = [cond["discriminator"], cond["value"]]
        out.append({"child": ct, "parent": pt, "child_key": ck, "parent_key": pk, "where": where, "cond": pair,
                    "probe": probe})
    return out


def apply_cleaning(ir, cleaning):
    """清洗规则按表挂到对象上,取数与复核时据此使用清洗视图。"""
    by_table = {}
    for rule in cleaning["rules"]:
        for t in ([rule.get("table", "")] + rule.get("populated", []) + rule.get("empty", [])
                  + [u["table"] for u in rule.get("usage", [])]):
            by_table.setdefault(t, []).append(rule)
    for o in ir.get("objects") or []:
        hits = [r for t in (o.get("tables") or [o.get("table")]) for r in by_table.get(t, [])]
        if hits:
            o.setdefault("evidence", {})["cleaning"] = hits
    return ir


def main(argv=None):
    ap = argparse.ArgumentParser(description="多通道交叉复核本体关系(只读)")
    ap.add_argument("--ir", required=True, help="构建产物 JSON(含 objects/relations)")
    ap.add_argument("--db", required=True, help="用于裁决的 SQLite 库(可为只读抽样库)")
    ap.add_argument("--report", help="报告输出路径(JSON);缺省打印摘要")
    ap.add_argument("--apply", help="把复核结论写入该路径的新 IR 文件")
    ap.add_argument("--clean", action="store_true", help="同时生成数据清洗规则(编码冲突、异常编码值、孤儿记录、迁移遗留空表、注释编码)")
    ap.add_argument("--comments", help="含 _dm_columns 字段注释的 SQLite(缺省用 --db 本身)")
    a = ap.parse_args(argv)
    with open(a.ir, encoding="utf-8") as fh:
        ir = json.load(fh)
    db = Db(a.db)
    ccon = None
    try:
        rows, hubs = check(ir, db)
        summary = summarize(rows, hubs)
        report = {"summary": summary, "relations": rows}
        if a.clean:
            import data_clean
            bound = {t for o in ir.get("objects") or [] for t in (o.get("tables") or [o.get("table")]) if t}
            ccon = ro_connect(a.comments) if a.comments else None
            cleaning = data_clean.clean(db, ccon or db.con, usable_relations(ir, rows, db), bound)
            report["cleaning"] = cleaning
            summary["cleaning"] = dict(Counter(r["kind"] for r in cleaning["rules"]))
    finally:
        if ccon is not None:
            ccon.close()
        db.con.close()
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    if a.apply:
        out = apply(ir, rows)
        if a.clean:
            out = apply_cleaning(out, report["cleaning"])
        with open(a.apply, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1, default=str)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

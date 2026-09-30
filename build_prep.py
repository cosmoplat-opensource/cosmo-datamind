#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建体检:在半自动构建之前,先把数据源「看一遍」,给出问题清单与分轮计划。

来自能碳平台 5 库(594 表)的实测经验:直接把整个库交给构建,会同时撞上几类问题——
没有声明外键、软删除行让键看起来不唯一、多态引用只能部分重叠、审计列被当成关系、
演示/历史分表混在业务表里、单轮表太多模型只覆盖一半、枢纽层后建导致跨轮关系无处挂。
这些都能在构建前用确定性规则识别出来,本模块只读、不调模型:

  profile(db)         数据源概况 + 建模规律识别 + 能源/碳排放信号
  doc_coverage(...)   上传资料对本库表的覆盖、资料里提到而库里没有的表(漂移)
  plan(...)           枢纽优先、每轮不超过 max_tables 的分轮计划
  check(...)          汇总为一份体检报告

识别结果只是提示,不改变数据与构建规则;排除哪些表、跑哪些轮次由使用者决定。
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict

import doc_chunks

META_TABLES = ("_dm_tables", "_dm_columns")          # ext_sample 写入的库表/字段注释
SOFT_DELETE = re.compile(r"^(is_?del(eted)?|del_?flag|delete_?flag|deleted(_flag)?|is_?delete)$", re.I)
AUDIT = re.compile(r"^((create|update|modify|modified|last_?edit|last_?update|del|delete)_?"
                   r"(by|user|user_?no|user_?id|uer|name|person|operator)|creator|operator|modifier|updater|"
                   r"(create|update|operate)_?(user|by)_?(no|id|name)?)$", re.I)
DISCRIMINATOR = re.compile(r"^(obj|object|target|biz|ref|owner|source|entity)_?type$", re.I)
POLY_KEY = re.compile(r"^(obj|object|target|biz|ref|owner|source|entity)_?(no|id|code)$", re.I)
KEYLIKE = re.compile(r"(_id|_no|_code)$", re.I)
SHARD = re.compile(r"(_(19|20)\d{2}(\d{2})?|_(bak|backup|copy\d*|old|\d{8}))$", re.I)   # 「历史」表是正常业务表,不算分表
SCRATCH = re.compile(r"^(tmp|temp|test|demo)_|_(tmp|temp|test|demo)$|^my_table$", re.I)
ENERGY = re.compile(r"能耗|用能|电表|电能|水表|计量|能源|折标|标煤|电价|光伏|储能|meter|energy|(?:^|_)(?:pv|es)_", re.I)
CARBON = re.compile(r"碳|排放|co2|carbon|emission|ghg|温室", re.I)


def _qi(name):
    return '"' + str(name).replace('"', '""') + '"'


def _connect(db):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _comments(con):
    """ext_sample 保存的注释(若有):表 → 注释,(表, 列) → 注释,表 → 源库行数估计。"""
    tabs, cols, rows = {}, {}, {}
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "_dm_tables" in names:
        for t, c, n in con.execute("SELECT table_name, comment, rows_est FROM _dm_tables"):
            tabs[t] = c or ""
            rows[t] = n
    if "_dm_columns" in names:
        for t, c, cm in con.execute("SELECT table_name, column_name, comment FROM _dm_columns"):
            cols[(t, c)] = cm or ""
    return tabs, cols, rows


def profile(db, deleted_share_cap=40):
    """数据源概况与建模规律识别。只读。"""
    con = _connect(db)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            if r[0] not in META_TABLES]
        t_comment, c_comment, rows_est = _comments(con)
        cols, pks, fks, rows = {}, {}, 0, {}
        for t in tables:
            info = con.execute(f"PRAGMA table_info({_qi(t)})").fetchall()
            cols[t] = [r[1] for r in info]
            pks[t] = [r[1] for r in info if r[5]]
            fks += len(con.execute(f"PRAGMA foreign_key_list({_qi(t)})").fetchall())
            rows[t] = con.execute(f"SELECT COUNT(*) FROM {_qi(t)}").fetchone()[0]
        ncols = sum(len(v) for v in cols.values())

        soft = []
        for t in tables:
            for c in cols[t]:
                if SOFT_DELETE.match(c):
                    share = None
                    if rows[t] and len(soft) < deleted_share_cap:
                        deleted = con.execute(f"SELECT COUNT(*) FROM {_qi(t)} WHERE COALESCE({_qi(c)},0) "
                                              "NOT IN (0,'0','N','n','false','')").fetchone()[0]
                        share = round(100 * deleted / rows[t], 1)
                    soft.append({"table": t, "column": c, "deleted_pct": share})
                    break
        poly = []
        for t in tables:
            disc = [c for c in cols[t] if DISCRIMINATOR.match(c)]
            keys = [c for c in cols[t] if POLY_KEY.match(c)]
            if disc and keys:
                values = [r[0] for r in con.execute(
                    f"SELECT DISTINCT {_qi(disc[0])} FROM {_qi(t)} WHERE {_qi(disc[0])} IS NOT NULL LIMIT 12")]
                poly.append({"table": t, "discriminator": disc[0], "keys": keys, "values": values})
        audit = sorted({(t, c) for t in tables for c in cols[t] if AUDIT.match(c)})
        hub = Counter(c.lower() for t in tables for c in cols[t] if KEYLIKE.search(c) and c.lower() != "id")
        hubs = [{"key": k, "tables": n} for k, n in hub.most_common() if n >= 5][:12]
        shards = [t for t in tables if SHARD.search(t.split("__", 1)[-1])]
        scratch = [t for t in tables if SCRATCH.search(t.split("__", 1)[-1]) or re.search(r"演示|临时|测试", t_comment.get(t, ""))]
        empty = [t for t in tables if rows[t] == 0]

        def hits(pattern):
            # 只看去掉「库名__」前缀后的表名与注释:多库合并时库名本身(如 energy_org)不能算作信号
            ts = [t for t in tables if pattern.search(t.split("__", 1)[-1]) or pattern.search(t_comment.get(t, ""))]
            cs = [(t, c) for (t, c), cm in c_comment.items() if t in cols and (pattern.search(c) or pattern.search(cm))]
            cs += [(t, c) for t in tables for c in cols[t] if pattern.search(c) and (t, c) not in c_comment]
            return ts, sorted(set(cs))
        e_tabs, e_cols = hits(ENERGY)
        c_tabs, c_cols = hits(CARBON)
        commented = sum(1 for (t, c), cm in c_comment.items() if cm and t in cols)
        return {
            "tables": len(tables), "columns": ncols, "rows": sum(rows.values()),
            "empty_tables": empty, "declared_fks": fks,
            "has_comments": bool(t_comment or c_comment),
            "table_comment_pct": round(100 * sum(1 for t in tables if t_comment.get(t)) / max(1, len(tables))),
            "column_comment_pct": round(100 * commented / max(1, ncols)) if c_comment else 0,
            "soft_delete": soft, "polymorphic": poly, "audit_columns": [{"table": t, "column": c} for t, c in audit],
            "hub_keys": hubs, "shard_tables": shards, "scratch_tables": scratch,
            "energy": {"tables": e_tabs, "columns": len(e_cols)},
            "carbon": {"tables": c_tabs, "columns": c_cols[:20], "column_count": len(c_cols)},
            "_cols": cols, "_pks": pks, "_rows": rows, "_comments": t_comment,
        }
    finally:
        con.close()


def findings(prof):
    """把识别结果整理为面向使用者的问题清单:级别、说明、处理建议、例子。"""
    out = []
    if prof["tables"] > 1 and prof["declared_fks"] == 0:
        out.append({"id": "no_fk", "level": "info", "title": "没有声明外键",
                    "detail": "关系只能由模型提议、再用取值重叠验证;数据越完整,可证实的关系越多。",
                    "examples": []})
    if prof["soft_delete"]:
        worst = sorted((s for s in prof["soft_delete"] if s["deleted_pct"]), key=lambda s: -s["deleted_pct"])[:3]
        out.append({"id": "soft_delete", "level": "warn", "title": f"{len(prof['soft_delete'])} 张表使用软删除",
                    "detail": "已删除的行仍在表里,会让编码看起来不唯一、重叠率偏低;复核「父键不唯一」时先排除已删除行。",
                    "examples": [f"{s['table']}.{s['column']}(已删除 {s['deleted_pct']}%)" for s in worst]})
    if prof["polymorphic"]:
        out.append({"id": "polymorphic", "level": "warn", "title": f"{len(prof['polymorphic'])} 张表有多态引用",
                    "detail": "同一个编号列按类型列的取值指向不同的表;应建成按类型区分的关联,整体对照单一父表只能部分重叠。",
                    "examples": [f"{p['table']}:{p['discriminator']} → {'/'.join(p['keys'])}" for p in prof["polymorphic"][:3]]})
    if prof["audit_columns"]:
        out.append({"id": "audit", "level": "info", "title": f"{len(prof['audit_columns'])} 个审计列",
                    "detail": "创建人、修改人一类的列表达的是「操作人」角色,不是业务关系;不必逐条建关系。",
                    "examples": [f"{a['table']}.{a['column']}" for a in prof["audit_columns"][:3]]})
    if prof["scratch_tables"] or prof["shard_tables"]:
        out.append({"id": "noise_tables", "level": "warn",
                    "title": f"疑似演示/临时表 {len(prof['scratch_tables'])} 张 · 历史分表或备份 {len(prof['shard_tables'])} 张",
                    "detail": "默认不纳入构建计划;如确属业务表,可在计划里手动加回。",
                    "examples": (prof["scratch_tables"] + prof["shard_tables"])[:4]})
    if prof["empty_tables"]:
        out.append({"id": "empty", "level": "info", "title": f"{len(prof['empty_tables'])} 张空表",
                    "detail": "空表上的关系无法用数据验证,只能保留为待取证。", "examples": prof["empty_tables"][:4]})
    if not prof["has_comments"]:
        out.append({"id": "no_comments", "level": "info", "title": "数据源没有表/字段注释",
                    "detail": "中文语义只能来自上传资料;建议上传数据字典或设计文档。外部库经「只读抽样」导入时会保留注释。",
                    "examples": []})
    return out


def suggest_references(prof):
    """能源与碳排放分开建议:有能源信号建议能源管理参照;碳排放单独判断。"""
    e, c = prof["energy"], prof["carbon"]
    out = []
    if len(e["tables"]) >= 3:
        out.append({"industry": "energy_management", "name": "能源管理",
                    "reason": f"{len(e['tables'])} 张表与能源计量、能耗、电价等相关"})
    if c["tables"]:
        out.append({"industry": "carbon_accounting", "name": "碳排放核算",
                    "reason": f"{len(c['tables'])} 张表与碳排放相关"})
    elif c["column_count"] and len(e["tables"]) >= 3:
        out.append({"industry": "carbon_accounting", "name": "碳排放核算", "separate": True,
                    "reason": f"没有碳排放表,只有 {c['column_count']} 个相关字段——碳排放多以派生指标存在;"
                              "建议能源与碳排放分开建,碳排放本体依据核算标准另建"})
    return out


def doc_coverage(tables, texts):
    """上传资料对本库表的覆盖:哪些表在资料里有专门的说明段;资料里以表名为标题、库里却没有的表。

    texts:[(文件名, 全文)]。表名可带「库前缀__」,与资料中的裸表名对齐。
    """
    chunks = []
    for name, txt in texts:
        chunks += doc_chunks.chunk_text(txt, name)
    if not chunks:
        return {"files": 0, "chunks": 0, "described": 0, "tables": len(tables), "missing": [], "doc_only": []}
    _, st = doc_chunks.select(chunks, anchors=list(tables), budget=10 ** 9, anchor_share=1.0, min_anchor_chars=1)
    bare = {t.split("__", 1)[-1].lower() for t in tables}
    heading_names = set()
    for c in chunks:
        last = c["path"].split(" > ")[-1] if c["path"] else ""
        m = re.search(r"(?:^|\s)([a-z][a-z0-9_]{2,})(?:\s*(?:——|—|（|\(|$))", last.lower())
        if m:
            heading_names.add(m.group(1))
    doc_only = sorted(n for n in heading_names if n not in bare and "_" in n)
    return {"files": len({c["source"] for c in chunks}), "chunks": len(chunks),
            "described": st["anchors_covered"], "tables": len(tables),
            "missing": st["anchors_missing"][:20], "doc_only": doc_only[:20], "doc_only_count": len(doc_only)}


def _group_key(table):
    db, _, rest = table.rpartition("__")
    toks = [x for x in rest.split("_") if x]
    head = toks[0] if toks else rest
    if len(head) <= 2 and len(toks) > 1:              # 过短的前缀(如 es_、pv_)与下一词合并才有区分度
        head = "_".join(toks[:2])
    return (db + "__" if db else "") + head


def plan(prof, max_tables=45, exclude=()):
    """枢纽优先、每轮不超过 max_tables 张表的分轮计划。

    排序依据:类型/字典层最先(被最多表引用);其余按「被引用度」——库里有多少张表带着
    指向该组的编号列(如 user 组 ← user_no)。这是启发式,使用者可以勾选、跳过轮次。
    """
    cols, excl = prof["_cols"], set(exclude)
    tables = [t for t in cols if t not in excl]
    groups = defaultdict(list)
    for t in tables:
        groups[_group_key(t)].append(t)
    colsets = {t: {c.lower() for c in cols[t]} for t in cols}

    def indegree(members):
        stems = set()
        for t in members:
            s = t.split("__", 1)[-1].lower()
            stems |= {s, s.rstrip("s"), s.split("_")[0]}
        keys = {f"{s}_{k}" for s in stems if len(s) >= 3 for k in ("id", "no", "code")}
        return sum(1 for t, cs in colsets.items() if t not in members and cs & keys)

    def tier(name):
        n = name.split("__", 1)[-1]
        if re.match(r"(meta|dict|dictionary|type|category|code)", n):
            return 0
        if re.match(r"(user|enterprise|ent|org|dept|company|tenant|role)", n):
            return 1
        return 2

    # 先按层级,再按所属库聚在一起(同一轮尽量来自同一个库,语义连贯),库内按被引用度排序
    def dbs(g):
        return g.split("__", 1)[0] if "__" in g else ""
    ordered = sorted(groups, key=lambda g: (tier(g), dbs(g) if tier(g) == 2 else "", -indegree(groups[g]), g))
    rounds, cur = [], []
    for g in ordered:
        members = sorted(groups[g])
        while members:
            room = max_tables - len(cur)
            if room <= 0 or (cur and len(members) > room and len(members) <= max_tables):
                rounds.append(cur); cur = []; continue
            cur += members[:room]; members = members[room:]
    if cur:
        rounds.append(cur)
    out = []
    comments = prof.get("_comments") or {}
    for i, ts in enumerate(rounds, 1):
        heads = Counter(_group_key(t).split("__", 1)[-1] for t in ts)
        label = "、".join(h for h, _ in heads.most_common(4)) + (" 等" if len(heads) > 4 else "")
        cn = [comments.get(t) for t in ts if comments.get(t)][:3]
        out.append({"round": i, "label": label, "tables": ts, "count": len(ts),
                    "hint": "、".join(c[:12] for c in cn)})
    return out


def check(db, texts=(), max_tables=45):
    """体检报告:概况、问题清单、资料覆盖、参照建议、分轮计划。"""
    prof = profile(db)
    excluded = sorted(set(prof["scratch_tables"]) | set(prof["shard_tables"]))
    tables = list(prof["_cols"])
    report = {
        "summary": {k: prof[k] for k in ("tables", "columns", "rows", "declared_fks", "has_comments",
                                           "table_comment_pct", "column_comment_pct")},
        "empty_tables": len(prof["empty_tables"]),
        "findings": findings(prof),
        "hub_keys": prof["hub_keys"],
        "references": suggest_references(prof),
        "docs": doc_coverage(tables, list(texts)),
        "excluded": excluded,
        "plan": plan(prof, max_tables=max_tables, exclude=excluded),
        "max_tables": max_tables,
    }
    return report

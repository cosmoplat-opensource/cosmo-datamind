#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建体检:在半自动构建之前,先把数据源「看一遍」,给出问题清单与分轮计划。

多库业务数据上的实践表明,直接把整个库交给构建,常同时遇到几类问题:
没有声明外键、逻辑删除(软删除)的行让键看起来不唯一、多态引用只能部分重叠、审计列被误判为关系、
演示表与历史分表混在业务表里、单轮表过多导致模型只覆盖一部分、枢纽表后建使跨轮关系缺少可挂接的对象。
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
from pathlib import Path

import concept_hints
import doc_chunks
import ontology_domain

META_TABLES = ("_dm_tables", "_dm_columns")          # ext_sample 写入的库表/字段注释
SOFT_DELETE = re.compile(ontology_domain.SOFT_DELETE_COLUMN_PATTERN, re.I)     # 逻辑删除列,规则见 valid_rows_rules.json
_LIVE_SQL = ",".join(str(v) if isinstance(v, int) else "'" + str(v).replace("'", "''") + "'"
                     for v in ontology_domain.LIVE_MARKERS if v is not None)
AUDIT = re.compile(r"^((create|update|modify|modified|last_?edit|last_?update|del|delete)_?"
                   r"(by|user|user_?no|user_?id|uer|name|person|operator)|creator|operator|modifier|updater|"
                   r"(create|update|operate)_?(user|by)_?(no|id|name)?)$", re.I)
DISCRIMINATOR = re.compile(r"^(obj|object|target|biz|ref|owner|source|entity)_?type$", re.I)
POLY_KEY = re.compile(r"^(obj|object|target|biz|ref|owner|source|entity)_?(no|id|code)$", re.I)
KEYLIKE = re.compile(r"(_id|_no|_code)$", re.I)
SHARD = re.compile(r"(_(19|20)\d{2}(\d{2})?|_(bak|backup|copy\d*|old|\d{8}))$", re.I)   # 「历史」表是正常业务表,不算分表
# 第三方工具自带的表(积木报表、Quartz 定时任务、工作流引擎、XXL-Job 等):属于工具自身,不是业务概念(DR-069)
TOOL_TABLE = re.compile(r"^(jimu_|qrtz_|act_(re|ru|hi|ge|id|evt|procdef)_|flw_|xxl_job_|undo_log$|flyway_|databasechangelog)",
                        re.I)
SCRATCH = re.compile(r"^(tmp|temp|test|demo)_|_(tmp|temp|test|demo)$|^my_table$", re.I)
# 注释只认「临时表 / 测试数据 / 演示用」这类说表本身用途的词组;「临时用电」「测试结果」是业务词,不算(DR-074)
SCRATCH_COMMENT = re.compile(r"(演示|临时|测试)(表|数据|用(?!电|水|气|工)|环境)|^(演示|临时|测试)$")
ENERGY = re.compile(r"能耗|用能|电表|电能|水表|计量|能源|折标|标煤|电价|光伏|储能|meter|energy|(?:^|_)(?:pv|es)_", re.I)
# 制造信号只看去掉层级/厂区前缀后的业务词:工单、过站、测试、物料、工艺路线……
MANUF = re.compile(r"工单|工序|工艺|物料|过站|直通率|良率|返修|贴片|(^|_)(pm_mo|mo_base|wip|bom|route|operation|"
                   r"repair|pallet|fpy|work_order|mo)(_|$)", re.I)
PCBA = re.compile(r"贴片|(^|_)(smt|aoi|spi|ict|fct|pcb|pcba|dipaoi|smtaoi)(_|$)", re.I)
CARBON = re.compile(r"碳|排放|co2|carbon|emission|ghg|温室", re.I)


def _qi(name):
    return '"' + str(name).replace('"', '""') + '"'


def _connect(db):
    # 路径先转成 file: URI:文件名里的 ? 或 # 不会被当成 URI 参数(否则 mode=ro 可能失效)
    return sqlite3.connect(Path(db).absolute().as_uri() + "?mode=ro", uri=True)


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
                                              f"NOT IN ({_LIVE_SQL})").fetchone()[0]
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
        tool = [t for t in tables if TOOL_TABLE.search(t.split("__", 1)[-1])]
        scratch = [t for t in tables if SCRATCH.search(t.split("__", 1)[-1]) or SCRATCH_COMMENT.search(t_comment.get(t, ""))]
        empty = [t for t in tables if rows[t] == 0]

        def hits(pattern):
            # 只看去掉「库名__」前缀后的表名与注释:多库合并时库名本身(如 energy_org)不能算作信号
            ts = [t for t in tables if pattern.search(t.split("__", 1)[-1]) or pattern.search(t_comment.get(t, ""))]
            cs = [(t, c) for (t, c), cm in c_comment.items() if t in cols and (pattern.search(c) or pattern.search(cm))]
            cs += [(t, c) for t in tables for c in cols[t] if pattern.search(c) and (t, c) not in c_comment]
            return ts, sorted(set(cs))
        # 概念层线索(DR-064):表族(多表一概念)、类别拆分(一表多概念)、版本重复(有效行)
        concept = concept_hints.table_families(cols, rows)
        concept["category_splits"] = concept_hints.category_splits(con, cols, rows)
        concept["version_duplicates"] = concept_hints.version_duplicates(con, cols, rows)
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
            "hub_keys": hubs, "shard_tables": shards, "scratch_tables": scratch, "tool_tables": tool,
            "energy": {"tables": e_tabs, "columns": len(e_cols)},
            "carbon": {"tables": c_tabs, "columns": c_cols[:20], "column_count": len(c_cols)},
            "concept": concept,
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
        out.append({"id": "soft_delete", "level": "warn", "title": f"{len(prof['soft_delete'])} 张表使用逻辑删除(软删除)",
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
    if prof.get("tool_tables"):
        out.append({"id": "tool_tables", "level": "info",
                    "title": f"第三方工具自带表 {len(prof['tool_tables'])} 张(报表、定时任务、工作流等)",
                    "detail": "这些表属于工具自身,不是业务概念,默认不纳入构建计划;如需要可在计划里手动加回。",
                    "examples": prof["tool_tables"][:4]})
    if prof["scratch_tables"] or prof["shard_tables"]:
        out.append({"id": "noise_tables", "level": "warn",
                    "title": f"疑似演示/临时表 {len(prof['scratch_tables'])} 张 · 历史分表或备份 {len(prof['shard_tables'])} 张",
                    "detail": "默认不纳入构建计划;如确属业务表,可在计划里手动加回。",
                    "examples": (prof["scratch_tables"] + prof["shard_tables"])[:4]})
    concept = prof.get("concept") or {}
    if concept.get("families"):
        fams = concept["families"]
        out.append({"id": "table_families", "level": "warn",
                    "title": f"{len(fams)} 个表族(归并 {concept['tables_in_families']} 张表)",
                    "detail": "同一实体按厂区、数仓层级、站点或备份复制成多张表,应建成一个概念;"
                              "分轮计划只放每族的代表表,其余成员构建后挂到同一个对象上。",
                    "examples": [f"{f['core']}:{len(f['members'])} 张,代表 {f['representative']}" for f in fams[:3]]})
    if concept.get("category_splits"):
        sp = concept["category_splits"]
        out.append({"id": "category_splits", "level": "warn", "title": f"{len(sp)} 处按类别装着多种概念",
                    "detail": "实例表按类别编码区分不同的领域概念,类别名称在目录表里;"
                              "按类别建子概念(或在对象说明中写明类别),不要只建一个笼统对象。",
                    "examples": [f"{x['table']}.{x['column']} → {x['catalog']}:"
                                 + "、".join(c["name"] or c["code"] for c in x["categories"][:4]) for x in sp[:3]]})
    if concept.get("version_duplicates"):
        vd = concept["version_duplicates"]
        out.append({"id": "version_duplicates", "level": "warn", "title": f"{len(vd)} 张表的业务键有版本重复",
                    "detail": "同一业务键多次入仓,会让父键看起来不唯一、关系被拒;与软删除一样,先定有效行"
                              "(按键保留最新一条)再判唯一与重叠。",
                    "examples": [f"{v['table']}.{v['key']}(重复 {v['duplicate_rows']} 行)" for v in vd[:3]]})
    if prof["empty_tables"]:
        out.append({"id": "empty", "level": "info", "title": f"{len(prof['empty_tables'])} 张空表",
                    "detail": "空表上的关系无法用数据验证,只能保留为待取证。", "examples": prof["empty_tables"][:4]})
    if not prof["has_comments"]:
        out.append({"id": "no_comments", "level": "info", "title": "数据源没有表/字段注释",
                    "detail": "中文语义只能来自上传资料;建议上传数据字典或设计文档。外部库经「只读抽样」导入时会保留注释。",
                    "examples": []})
    return out


def scenario(prof):
    """场景画像:多厂多层湖仓(多表一概念)与元数据驱动业务库(一表多概念),两者可并存。"""
    c = prof.get("concept") or {}
    n = max(1, prof["tables"])
    shares = round(100 * (c.get("tables_in_families") or 0) / n)
    kinds = []
    if shares >= 30:
        kinds.append({"id": "lakehouse", "name": "多厂多层湖仓",
                      "reason": f"{shares}% 的表属于表族(同一实体按厂区、层级、站点或副本复制)",
                      "strategy": "多表一概念:表族只建代表表,其余成员挂到同一个对象上"})
    if c.get("category_splits"):
        kinds.append({"id": "metadata_driven", "name": "元数据驱动业务库",
                      "reason": f"{len(c['category_splits'])} 处实例表按类别目录装着多种概念",
                      "strategy": "一表多概念:按类别编码建子概念,目录行提升为概念"})
    return kinds


def suggest_references(prof):
    """行业参照与领域技能一起建议。能源与碳排放分开;制造按信号区分 PCBA/SMT 与通用离散制造。"""
    e, c = prof["energy"], prof["carbon"]
    cores = concept_hints.name_cores(list(prof["_cols"]))[0]
    words = {t: cores.get(t, t) for t in prof["_cols"]}
    m_tabs = [t for t, w in words.items() if MANUF.search(w) or MANUF.search(prof["_comments"].get(t, ""))]
    p_tabs = [t for t, w in words.items() if PCBA.search(w) or PCBA.search(prof["_comments"].get(t, ""))]
    # 领域信号按占比判断:大库里零星几张相关表(制造库里的计量表、能碳库里的运维工单)不算该领域
    n = max(1, prof["tables"])
    out = []
    if len(p_tabs) >= max(3, 0.03 * n):
        out.append({"industry": "pcba", "name": "PCBA / SMT", "skill": "electronics-manufacturing-ontology",
                    "reason": f"{len(p_tabs)} 张表与贴片、检测(AOI/ICT/FCT)、板件相关;另有 {len(m_tabs)} 张工单、过站、物料相关"})
    elif len(m_tabs) >= max(5, 0.05 * n):
        out.append({"industry": "manufacturing", "name": "离散制造", "skill": "electronics-manufacturing-ontology",
                    "reason": f"{len(m_tabs)} 张表与工单、工序、物料、过站相关"})
    energy = len(e["tables"]) >= max(3, 0.03 * n)
    if energy:
        out.append({"industry": "energy_management", "name": "能源管理", "skill": "energy-carbon-ontology",
                    "reason": f"{len(e['tables'])} 张表与能源计量、能耗、电价等相关"})
    if c["tables"]:
        out.append({"industry": "carbon_accounting", "name": "碳排放核算", "skill": "energy-carbon-ontology",
                    "reason": f"{len(c['tables'])} 张表与碳排放相关"})
    elif c["column_count"] and energy:
        out.append({"industry": "carbon_accounting", "name": "碳排放核算", "separate": True,
                    "skill": "energy-carbon-ontology",
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


def _group_key(table, core=None):
    db, _, rest = table.rpartition("__")
    # 湖仓表名带层级/厂码/来源前缀,按业务词根的头一个词分组(co、pm、wip…)才有业务含义
    toks = [x for x in (core or rest).split("_") if x]
    head = toks[0] if toks else rest
    if len(head) <= 2 and len(toks) > 1:              # 过短的前缀(如 es_、pv_)与下一词合并才有区分度
        head = "_".join(toks[:2])
    return (db + "__" if db else "") + head


def plan(prof, max_tables=45, exclude=(), families=None):
    """枢纽优先、每轮不超过 max_tables 张表的分轮计划。

    排序依据:类型/字典层最先(被最多表引用);其余按「被引用度」——库里有多少张表带着
    指向该组的编号列(如 user 组 ← user_no)。这是启发式,使用者可以勾选、跳过轮次。
    """
    max_tables = max(1, int(max_tables))             # 每轮至少一张表,否则分轮循环无法推进
    cols, excl = prof["_cols"], set(exclude)
    # 表族只让代表表进计划,其余成员随代表表走(构建后挂到同一个对象上)
    riders = {}
    for f in families or []:
        rep_t = f["representative"]
        if rep_t in excl:
            continue
        for m in f["members"]:
            if m != rep_t and m not in excl:
                riders.setdefault(rep_t, []).append(m)
    ride = {m for ms in riders.values() for m in ms}
    tables = [t for t in cols if t not in excl and t not in ride]
    cores = concept_hints.name_cores(list(cols))[0]
    groups = defaultdict(list)
    for t in tables:
        groups[_group_key(t, cores.get(t))].append(t)
    colsets = {t: {c.lower() for c in cols[t]} for t in cols}

    def indegree(members):
        stems = set()
        for t in members:
            s = t.split("__", 1)[-1].lower()
            stems |= {s, s.rstrip("s"), s.split("_")[0]}
        keys = {f"{s}_{k}" for s in stems if len(s) >= 3 for k in ("id", "no", "code")}
        return sum(1 for t, cs in colsets.items() if t not in members and cs & keys)

    def tier(g):
        # 层次按成员的原表名判断(元数据/字典表常以 meta_、dict_ 开头,词根里反而没有)
        names = [t.split("__", 1)[-1].lower() for t in groups.get(g, [])] or [g.split("__", 1)[-1]]
        if any(t.startswith("dim_") for t in names):      # 数仓维度层就是被引用最多的主数据
            return 0
        n = re.sub(r"^(ods|stg|dwd|dws|dim|ads)_", "", names[0])
        if re.match(r"(meta|dict|dictionary|type|category|code)", n) or re.search(r"(^|_)(sy_)?dict(_|$)", n):
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
        heads = Counter(_group_key(t, cores.get(t)).split("__", 1)[-1] for t in ts)
        label = "、".join(h for h, _ in heads.most_common(4)) + (" 等" if len(heads) > 4 else "")
        cn = [comments.get(t) for t in ts if comments.get(t)][:3]
        item = {"round": i, "label": label, "tables": ts, "count": len(ts),
                "hint": "、".join(c[:12] for c in cn)}
        fm = {t: riders[t] for t in ts if t in riders}
        if fm:
            item["family_members"] = fm
        out.append(item)
    return out


def check(db, texts=(), max_tables=45):
    """体检报告:概况、问题清单、资料覆盖、参照建议、分轮计划。"""
    prof = profile(db)
    excluded = sorted(set(prof["scratch_tables"]) | set(prof["shard_tables"]) | set(prof.get("tool_tables") or ()))
    tables = list(prof["_cols"])
    report = {
        "summary": {k: prof[k] for k in ("tables", "columns", "rows", "declared_fks", "has_comments",
                                           "table_comment_pct", "column_comment_pct")},
        "empty_tables": len(prof["empty_tables"]),
        "findings": findings(prof),
        "hub_keys": prof["hub_keys"],
        "references": suggest_references(prof),
        "scenario": scenario(prof),
        "docs": doc_coverage(tables, list(texts)),
        "excluded": excluded,
        "plan": plan(prof, max_tables=max_tables, exclude=excluded,
                     families=(prof.get("concept") or {}).get("families")),
        "concept": {"families": len((prof.get("concept") or {}).get("families") or []),
                    "tables_in_families": (prof.get("concept") or {}).get("tables_in_families", 0),
                    "category_splits": len((prof.get("concept") or {}).get("category_splits") or []),
                    "version_duplicates": len((prof.get("concept") or {}).get("version_duplicates") or [])},
        "max_tables": max_tables,
    }
    return report

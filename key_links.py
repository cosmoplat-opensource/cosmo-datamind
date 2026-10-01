#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按键名确定性地补提关系候选(DR-069)。

分轮构建时,模型只看得到本轮表的结构;前几轮已建对象只给了名称与表名,看不到列。
实测(能碳三轮)中,实例表 → 类型目录、指标 → 指标定义这些核心关系一条都没提出,
关联表(企业-用能单元)也连错了目标。这里按「键属于哪张表」补提候选:

- 键的归属:列的词根(去掉 _id/_code/_no 等后缀、展开 dev/sys/ent 等缩写)与表的核心名一致,
  这一列就是该表的标识键(inst_device.dev_no、meta_device_type.dev_type_code);
- 子键 = 修饰词 + 父表标识键(mete_dev_type_code → dev_type_code),或子键词根指向父表核心名
  (dev_group_id → meta_device_group.id);
- 多态引用(obj_type + obj_no):父表为本轮与已有对象中标识键后缀相同的表,先用探测重叠筛掉明显不相干的;
- 租户键(企业、组织、工厂编号)只在关联表上提,不逐表提(与交叉复核技能的约定一致);
- 审计列不提;同一对表模型已经提过的不重复提。

这里只提候选,是否成立仍由数据裁决(重叠率、父键唯一、键名有据)决定。只用标准库与仓库内纯函数模块。
"""
from __future__ import annotations

import re

import concept_hints
import dao_core

_KEY = re.compile(r"_(id|code|no|sn|num|key)$")
_AUDIT = re.compile(r"^((create|update|modify|modified|last_?edit|last_?update|del|delete)_?"
                    r"(by|user|user_?no|user_?id|uer|name|person|operator)|creator|operator|modifier|updater)$")
DISCRIMINATOR = re.compile(r"^(obj|object|target|biz|ref|owner|source|entity|rel_obj)_?type$")
POLY_KEY = re.compile(r"^(obj|object|target|biz|ref|owner|source|entity|rel_obj)_?(no|id|code)$")
_ASSOC = re.compile(r"(^|_)(rel|relation|link|links|map|mapping)$")
_TENANT = {"enterprise", "tenant", "organization", "company", "factory", "corp"}
_GENERIC = {"type", "status", "state", "name", "value", "flag", "level", "kind", "sort", "order", "parent", "id", "code"}
_CATALOG = re.compile(r"(^|_)(meta|dim|def|dict|dictionary|md|master|mst|type|types|catalog)(_|$)")
_POLY_FLOOR = 30.0          # 多态候选的探测重叠下限(%)
MAX_PROPOSALS = 80


def _norm(col):
    return dao_core._snake(col or "")


def _stem(col):
    return dao_core.expand(dao_core.key_stem(col))


def _owner_key(core, col):
    """col 是否为核心名为 core 的表的标识键。"""
    st = _stem(col)
    if not st or st in _GENERIC or not _KEY.search(_norm(col)):
        return False
    return st == core or core.endswith("_" + st) or st.endswith("_" + core)


def _refers(child_col, core, parent_col):
    """子键是否指向(核心名为 core 的表的)标识键 parent_col。"""
    sc = _stem(child_col)
    if not sc:
        return False
    if dao_core.key_stem(parent_col) == "":                        # 父键是 id/code 这类泛化键
        if sc in _GENERIC:                                         # type_code → dict_type.id 这类泛词不提
            return False
        return sc == core or sc.endswith("_" + core) or (len(sc) >= 4 and core.endswith("_" + sc))
    sp = _stem(parent_col)
    return sc == sp or sc.endswith("_" + sp)


def _is_catalog(table):
    return bool(_CATALOG.search(_norm(table.split("__")[-1])))


_LAYER = {"ods", "dwd", "dws", "dim", "ads", "dm", "fact", "meta", "inst", "t", "tb"}


def table_core(table):
    """表的核心名(去掉「库名__」与分层前缀,展开缩写):user__enterprise → enterprise。"""
    toks = _norm(str(table or "").split("__")[-1]).split("_")
    while len(toks) > 1 and toks[0] in _LAYER:
        toks = toks[1:]
    return dao_core.expand("_".join(toks))


def binding_score(name, table):
    """对象名与绑定表的吻合度:2 = 核心名相同,1 = 一方包含另一方,0 = 无关。"""
    n, c = dao_core.expand(_norm(name or "")), table_core(table)
    if not n or not c:
        return 0
    if n == c:
        return 2
    return 1 if (n in c or c in n) else 0


def _shared_prefix(a, b):
    n = 0
    for x, y in zip(a.split("_"), b.split("_")):
        if x != y:
            break
        n += 1
    return n


def propose(tab_cols, objects, round_tables, existing=(), probe=None, max_proposals=MAX_PROPOSALS, used_keys=()):
    """返回补提的关系候选。

    tab_cols: {表: [(列, 类型), …]}(全库);objects: [{name, table}](本轮与已有本体的对象);
    round_tables: 本轮范围;existing: 已提出的 (源对象, 目标对象) 对;used_keys: 模型已用作子键的 (表, 列),不再另配父表;
    probe(child_table, child_col, parent_table, parent_col) → 重叠率 %,用于筛多态候选,不给则不提多态候选。
    """
    t2o = {}
    for o in objects or []:
        t = o.get("table")
        if t and t in tab_cols and t not in t2o:
            t2o[t] = o.get("name")
    if not t2o:
        return []
    cores = concept_hints.name_cores(list(tab_cols))[0]
    core = {t: dao_core.expand(_norm(cores.get(t, t.split("__")[-1]))) for t in t2o}
    cols = {t: [c for c, _ in tab_cols.get(t, [])] for t in t2o}
    owned = {t: [c for c in cols[t] if _owner_key(core[t], c)] for t in t2o}
    # 父表标识键还包括 id(由子键词根指向父表名来补证)
    rnd = set(round_tables or ())
    seen = {tuple(p) for p in existing or ()}
    used = {(str(t).lower(), str(c).lower()) for t, c in used_keys or ()}
    out, done = [], set()

    def add(ct, cc, pt, pc, why):
        pair = (t2o[ct], t2o[pt])
        if ct == pt or (ct, cc, pt) in done:
            return
        done.add((ct, cc, pt))
        # 模型已提过这对表时不另起一条,只把这组键作为备选交给裁决(模型给的键常是审计列或复合列)
        out.append({"source": t2o[ct], "target": t2o[pt], "child_table": ct, "parent_table": pt,
                    "child_key": cc, "parent_key": pc, "verb": "引用", "origin": "key_match",
                    "rationale": why, **({"alternative": True} if pair in seen else {})})

    for ct in t2o:
        tcols = cols[ct]
        disc = next((c for c in tcols if DISCRIMINATOR.match(_norm(c))), None)
        for cc in tcols:
            n = _norm(cc)
            if not _KEY.search(n) or _AUDIT.match(n) or n == "id":
                continue
            if (ct.lower(), cc.lower()) in used and not (disc and POLY_KEY.match(n)):
                continue                                               # 多态键本来就指向多张表,不因模型用过而跳过
            own = cc in owned[ct]           # 本表的标识键:只在实例表引用同名的目录表时再提(inst_index.index_code → meta_index)
            matches = []
            for pt in t2o:
                if pt == ct or (ct not in rnd and pt not in rnd):      # 至少一端在本轮
                    continue
                if disc and POLY_KEY.match(n):
                    if probe is None:
                        continue
                    suf = n.rsplit("_", 1)[-1]
                    for pc in owned[pt]:
                        if _norm(pc).rsplit("_", 1)[-1] == suf and core[pt] not in _TENANT:
                            ov = probe(ct, cc, pt, pc)
                            if ov is not None and ov >= _POLY_FLOOR:
                                add(ct, cc, pt, pc, f"多态引用:{cc}(判别列 {disc})与 {pt}.{pc} 探测重叠 {ov:.0f}%")
                    continue
                if own and not (_is_catalog(pt) and not _is_catalog(ct)):
                    continue
                cands = [pc for pc in owned[pt] if _refers(cc, core[pt], pc)]
                if not cands and "id" in [c.lower() for c in cols[pt]] and _refers(cc, core[pt], "id"):
                    cands = [next(c for c in cols[pt] if c.lower() == "id")]
                if not cands:
                    continue
                if core[pt] in _TENANT and not _ASSOC.search(core[ct]):
                    continue                                           # 租户键只在关联表上提
                exact = 100 if core[pt] == _stem(cc) or core[pt] == _stem(cands[0]) else 0
                matches.append((exact + _shared_prefix(core[ct], core[pt]), pt, cands[0]))
            # 多张表都能对上时(config_id 对上所有 *_config 表),取与子表名共有前缀最长的一张;并列则不提,留给模型与人工
            if matches:
                best = max(m[0] for m in matches)
                top = [m for m in matches if m[0] == best]
                if len(top) == 1:
                    _, pt, pc = top[0]
                    add(ct, cc, pt, pc, f"键名:{ct}.{cc} 指向 {pt} 的标识键 {pc}")
    out.sort(key=lambda p: (p["child_table"] not in rnd, p["origin"]))
    return out[:max_proposals]

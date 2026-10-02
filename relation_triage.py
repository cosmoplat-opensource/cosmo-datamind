#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选关系分流(DR-073):把候选分成「自动驳回 / 暂缓 / 待审」,待审的按证据强弱排序。

全库构建后候选关系有三百多条,是人工审阅的大头,其中多数凭数据就能判定不成立。这里只用已记录的裁决证据
(重叠率、父键唯一、键名依据、交叉复核结论、字段类型),不调模型、不查库,结果可回放:

- 自动驳回:数据不支持(重叠率低于 20%)、交叉复核强冲突、同一子键已验证指向更贴切的父表、
  键名无据且父键不唯一或重叠不足、键名无据的整数键(自增 id 碰巧重叠)、
  父键不是父表的标识(企业编号指向任意带该列的表,这是共享列而非引用);
- 暂缓:两端表在抽样中没有数据,无从取证,接入数据后再判;
- 待审:其余候选,按「重叠率 × 键名依据 × 父键唯一」计分,高分在前,并写明还缺哪一项证据。

驳回与暂缓的关系从关系列表移到 ``rejected_relations`` / ``deferred_relations``,保留原证据,可追溯、可恢复。
"""
from __future__ import annotations

import re

import dao_core
import key_links

WEAK = dao_core.WEAK_FLOOR
THETA = dao_core.MIN_OVERLAP


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _col_type(objs_by_name, obj_name, col):
    o = objs_by_name.get(obj_name) or {}
    for a in o.get("attrs") or []:
        if str(a.get("col") or "").lower() == str(col or "").lower():
            return str(a.get("type") or "").upper()
    return ""


def _parent_identifier(table, key):
    """父键是父表自己的标识:主键 id,或词根与表的核心名一致的编码列。"""
    if not table or not key or "," in str(key):
        return True                       # 复合键另有判定,不在这里驳回
    if str(key).lower() == "id":
        return True
    return key_links._owner_key(key_links.table_core(table), key) or key_links._is_catalog(table)


_AUDIT = key_links._AUDIT


def _exact_owner(table, key):
    """父表核心名与键词根完全一致(enterprise 之于 ent_no);只「以之结尾」的关联表(location_enterprise)不算。"""
    return bool(table and key) and key_links.table_core(table) == key_links._stem(key)


def _names_parent(pt, ck, pk):
    """子键名直接点名父表的标识:xxx_id → xxx.id,或与父表自身编码列同名(produce_code → produce.produce_code)。"""
    if not pt or not ck or not pk or "," in str(pk):
        return False
    core, st = key_links.table_core(pt), key_links._stem(ck)
    if not st or st in key_links._GENERIC:
        return False
    if str(pk).lower() == "id":
        return st == core or core.endswith("_" + st)
    return key_links._owner_key(core, pk) and key_links._stem(pk) == st


def _header_detail(rel, o2t, pt, ck, pk):
    """主从表:子表名以父表名开头(pv_report_detail 之于 pv_report),同名键是父表自身的标识。
    父表有重复行多是版本或抽样所致,交人审而不是当作共享列驳回。"""
    ct = o2t.get(rel.get("source_concept"))
    if not ct or str(ck).lower() != str(pk).lower():
        return False
    core = key_links.table_core(pt)
    st = re.sub(r"_(uid|uuid|id|no|code|sn|key)$", "", key_links._norm(str(pk)))
    return key_links.table_core(ct).startswith(core + "_") and bool(st) and st not in key_links._GENERIC \
        and st in core.split("_")


def classify(rel, objs_by_name, o2t, catalogs=None):
    """返回 (类别, 理由, 优先级):类别为 reject / defer / review。"""
    ev = rel.get("evidence") or {}
    note = str(rel.get("note") or "")
    ov = _num(ev.get("overlap"))
    if ov is None:
        ov = _num(rel.get("overlap"))
    ck, pk = ev.get("child_key"), ev.get("parent_key")
    if not ck or ov is None or ov < 0:
        if "无数据" in note or "无从取证" in note:
            return "defer", "两端表在抽样中没有数据,无从取证", 0.0
        return "reject", "没有可回放的连接键证据", 0.0
    cc = ev.get("cross_check") or {}
    if cc.get("conflicts") or cc.get("verdict") == "refuted" or "交叉复核冲突" in note:
        if cc.get("prev_status") == "verified" and ev.get("name_ok") is True and (ov or 0) >= THETA \
                and str(ck).strip().lower() != "id":
            # 裁决已验证、键名有据,只是复核发现疑点(如名称快照不一致):疑点交人审,不自动驳回
            return "review", "缺少:交叉复核疑点的人工判断(" + "、".join(cc.get("conflicts") or ["冲突"]) + \
                ";原裁决已验证)", round(ov / 100.0 * 0.5, 3)
        return "reject", "交叉复核强冲突(如自增 id 碰巧重叠、名称不一致)", 0.0
    if note.startswith("同一子键"):
        return "reject", "同一子键已验证指向更贴切的父表", 0.0
    name_ok = ev.get("name_ok") is True
    pu = ev.get("parent_unique")
    pt = o2t.get(rel.get("target_concept"))
    if ov < WEAK and not (ev.get("sample_limited") or {}).get("parent"):
        if name_ok and _names_parent(pt, ck, pk):
            return "defer", (f"键名直接指向 {pt} 的标识,但抽样中重叠率仅 {ov:.0f}%:"
                             "两表各自只保留最新数据或存在孤儿记录,需全量数据核对"), 0.0
        return "reject", f"重叠率 {ov:.0f}% 低于 {WEAK:.0f}%,数据不支持", 0.0
    coded_catalog = (key_links._is_catalog(pt or "") and ov >= THETA and "," not in str(pk)
                     and key_links._owner_key(key_links.table_core(pt), pk)
                     and "INT" not in _col_type(objs_by_name, rel.get("source_concept"), ck))
    # 多态键(obj_no + 判别列)本就与父键不同名,不按键名驳回,留给人工
    src = objs_by_name.get(rel.get("source_concept")) or {}
    poly = key_links.POLY_KEY.match(key_links._norm(str(ck))) and any(
        key_links.DISCRIMINATOR.match(key_links._norm(str(a.get("col") or ""))) for a in src.get("attrs") or [])
    if not name_ok and not poly:
        if pu is False and not coded_catalog:
            return "reject", "键名无据且父键不唯一", 0.0
        if ov < THETA:
            return "reject", f"键名无据且重叠率 {ov:.0f}% 未达 {THETA:.0f}%", 0.0
        if "INT" in _col_type(objs_by_name, rel.get("source_concept"), ck):
            return "reject", "键名无据的整数键:自增 id 值域天然重合,不能据此认定", 0.0
    if any(_AUDIT.match(key_links._norm(c.strip())) for c in str(ck).split(",")):
        return "reject", "审计列(创建人、修改人)表达的是操作人角色,不逐表建关系", 0.0
    _core = key_links.table_core(pt or "")
    catalog_own = key_links._is_catalog(pt or "") and (
        any(key_links._owner_key(_core, k) for k in (pk, ck))
        or (_core == key_links._norm(str(ck)) or _core.endswith("_" + key_links._norm(str(ck)))) and "_" in key_links._norm(str(ck)))
    if pu is False and "," not in str(pk) and not (_exact_owner(pt, pk) or catalog_own
                                                    or _header_detail(rel, o2t, pt, ck, pk)):
        return "reject", f"{pk} 不是 {pt} 的标识,父键不唯一:这是共享列或关联表(如企业编号、企业-位置关联),不是引用", 0.0
    alt = (catalogs or {}).get(key_links.table_core(pt or ""))
    if alt and alt != pt and not key_links._is_catalog(pt) and pu is False:
        return "reject", f"{pt} 是实例表,同名的目录表 {alt} 才是 {pk} 的定义", 0.0
    # 待审:按证据强弱计分,并写明缺什么
    missing = []
    if coded_catalog and not name_ok:
        missing.append("键名依据(编码列取值落在目录表编码内)")
    elif not name_ok:
        missing.append("键名依据")
    if pu is False:
        missing.append("父键唯一(父表有重复行)")
    if ov < THETA:
        missing.append(f"重叠率({ov:.0f}%,其余为孤儿记录或抽样未覆盖)")
    if ev.get("direction") == "reverse":
        missing.append("方向(子列唯一而父列不唯一)")
    if (ev.get("sample_limited") or {}).get("parent"):
        missing.append("全量数据核对(父表在抽样中被截断)")
    score = (ov / 100.0) * (1.0 if name_ok else 0.6) * (1.0 if pu is not False else 0.7) \
        * (0.5 if ev.get("direction") == "reverse" else 1.0)
    return "review", "缺少:" + ("、".join(missing) if missing else "人工确认业务含义"), round(score, 3)


def triage(ir):
    """就地分流 IR 的候选关系;返回各类条数。"""
    rels_key = "relations" if "relations" in ir else "links"
    rels = ir.get(rels_key) or []
    objs_by_name = {o.get("name"): o for o in ir.get("objects") or [] if isinstance(o, dict)}
    o2t = {k: (v or {}).get("table") for k, v in objs_by_name.items()}
    catalogs = {key_links.table_core(t): t for t in o2t.values() if t and key_links._is_catalog(t)}
    keep, rejected, deferred = [], list(ir.get("rejected_relations") or []), list(ir.get("deferred_relations") or [])
    stat = {"rejected": 0, "deferred": 0, "review": 0}
    for r in rels:
        if not isinstance(r, dict) or r.get("status") in ("verified", "asserted"):
            keep.append(r)
            continue
        kind, why, score = classify(r, objs_by_name, o2t, catalogs)
        if kind == "review":
            r["review"] = {"priority": score, "reason": why}
            keep.append(r)
        else:
            r["triage"] = {"decision": kind, "reason": why}
            (rejected if kind == "reject" else deferred).append(r)
        stat["rejected" if kind == "reject" else "deferred" if kind == "defer" else "review"] += 1
    ir[rels_key] = keep
    ir["rejected_relations"], ir["deferred_relations"] = rejected, deferred
    return stat


def review_queue(ir):
    """待审候选按优先级从高到低。"""
    rels = ir.get("relations") or ir.get("links") or []
    return sorted((r for r in rels if isinstance(r, dict) and r.get("review")),
                  key=lambda r: -r["review"]["priority"])

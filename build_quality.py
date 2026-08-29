#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本体构建产物的确定性验收检查。

本模块组合图结构、关系证据契约、定义质量、上层关系映射与能力问题(CQ)，给出
pass/review/fail 三态结果。它只读 IR、
不调模型、不自动修图,因此可在 quick_build、LLM 构建、API 和测试中复用。
"""
from __future__ import annotations

import cq_check
import dao_core
import definition_eval
import health_check
import ontology_grounding


def _relations(ir):
    # 旧版 IR 使用 links，新版使用 relations。部分迁移文件会保留空 links；
    # 此时仍应检查实际有内容的 relations，不能因空兼容字段而漏审。
    if ir.get("links"):
        return ir.get("links", []), "source", "target"
    return ir.get("relations", []), "source_concept", "target_concept"


def audit_verified_evidence(ir):
    """审计 verified 关系是否满足可回放证据契约。人审 asserted 不冒充数据验证。"""
    relations, source_key, target_key = _relations(ir)
    issues = []
    checked = 0
    for index, relation in enumerate(relations):
        if relation.get("status") != "verified":
            continue
        checked += 1
        evidence = relation.get("evidence") or {}
        source = evidence.get("source") or ""
        legacy_sources = evidence.get("sources") or []
        declared_fk = (source == "declared_fk" or evidence.get("declared") is True or
                       "schema-fk" in legacy_sources or
                       str(relation.get("note") or "").startswith(("声明外键", "声明FK")))
        label = f"{relation.get(source_key) or '?'}→{relation.get(target_key) or '?'}"
        missing_keys = [key for key in ("child_key", "parent_key") if not evidence.get(key)]
        if missing_keys:
            issues.append({"type": "verified_missing_join_key", "relation": label, "index": index,
                           "desc": f"verified 关系缺少结构化连接键:{'、'.join(missing_keys)}",
                           "fix": "重新执行数据裁决并保存 child_key/parent_key;无证据则降为 candidate"})
            continue
        if declared_fk:
            continue

        overlap = evidence.get("overlap", relation.get("overlap"))
        if not isinstance(overlap, (int, float)) or overlap < dao_core.MIN_OVERLAP:
            issues.append({"type": "verified_overlap_below_threshold", "relation": label, "index": index,
                           "desc": f"verified 关系重叠率 {overlap!r} 未达到固定阈值 {dao_core.MIN_OVERLAP:g}%",
                           "fix": "降为 candidate,或用可回放数据重新取证"})
        if evidence.get("parent_unique") is not True:
            issues.append({"type": "verified_parent_not_unique", "relation": label, "index": index,
                           "desc": "verified 关系未记录父键唯一=true",
                           "fix": "重算父键唯一性;不唯一或无法证明时降为 candidate"})
        if evidence.get("name_ok") is not True:
            issues.append({"type": "verified_name_unsupported", "relation": label, "index": index,
                           "desc": "verified 关系未记录键名/父表名语义证据",
                           "fix": "用统一 name_ok 复核;无命名证据时降为 candidate"})
        if evidence.get("direction") == "reverse":
            issues.append({"type": "verified_reverse_direction", "relation": label, "index": index,
                           "desc": "verified 关系证据显示方向为反向",
                           "fix": "按唯一侧为父重新定向,原边不得保持 verified"})
    return {"checked": checked, "issue_count": len(issues), "valid": not issues, "issues": issues}


def audit_grounding(ir):
    """复核已声明的 BFO/IOF 映射是否存在，并满足定义域和值域约束。"""
    relations, source_key, target_key = _relations(ir)
    categories = {}
    for obj in ir.get("objects", []):
        oid = obj.get("id") or obj.get("name")
        if oid:
            categories[oid] = obj.get("bfo") or ontology_grounding.default_category(obj.get("kind"))
    issues = []
    mapped = 0
    for index, relation in enumerate(relations):
        source, target = relation.get(source_key), relation.get(target_key)
        item = ontology_grounding.normalize(
            relation.get("founded_relation"), relation.get("temporal"), relation.get("verb"),
            categories.get(source), categories.get(target),
        )
        mapped += item["status"] == "mapped"
        claimed = bool(relation.get("founded_relation") or relation.get("grounding_iri") or
                       relation.get("grounding_status") == "mapped")
        if claimed and item["status"] != "mapped":
            issues.append({"type": "invalid_upper_relation_mapping", "index": index,
                           "relation": f"{source or '?'}→{target or '?'}",
                           "desc": item["reason"],
                           "fix": "修正实体上层类别或关系方向；无法确认时保留为本地对象属性"})
    return {"mapped": mapped, "unmapped": len(relations) - mapped,
            "issue_count": len(issues), "valid": not issues, "issues": issues}


def cq_status_text(cq):
    """把 CQ 验收报告转成不夸大结论的短文案。"""
    if not isinstance(cq, dict) or not cq.get("provided"):
        return "CQ 未提供"
    total = int(cq.get("total") or 0)
    counts = cq.get("counts") or {}
    answerable = int(counts.get("answerable") or 0)
    partial = int(counts.get("partial") or 0)
    unanswerable = int(counts.get("unanswerable") or 0)
    return (f"CQ 可回答 {answerable}/{total} · 部分支持 {partial} · "
            f"不可回答 {unanswerable}")


def evaluate(ir, cqs=None, definition_threshold=0.6):
    """执行验收检查，返回 ``result ∈ {pass, review, fail}`` 的结构化报告。"""
    ir = ir if isinstance(ir, dict) else {}
    health = health_check.check(ir)
    evidence = audit_verified_evidence(ir)
    grounding = audit_grounding(ir)
    definitions_full = definition_eval.score_ontology(ir, weak_threshold=definition_threshold)
    definitions = {key: definitions_full[key] for key in ("scored", "mean_score", "weak", "weak_threshold")}
    definitions["weakest"] = definitions_full.get("rows", [])[:20]

    blocking_issues = []
    gaps = []
    if not ir.get("objects"):
        item = {"type": "empty_ontology", "desc": "构建产物没有对象", "fix": "检查数据源与抽取结果后重新构建"}
        blocking_issues.append(item); gaps.append(item)
    for item in health_check.gaps_from(health):
        blocking_issues.append(item); gaps.append(item)
    for item in evidence["issues"]:
        gap = {"type": "evidence_" + item["type"], "desc": item["desc"], "fix": item["fix"]}
        blocking_issues.append(item); gaps.append(gap)

    review_queue = []
    for signal in health.get("signals", []):
        review_queue.append({"type": "health_" + signal["type"], "desc": signal["desc"], "fix": signal["fix"]})
    relations, _sk, _tk = _relations(ir)
    candidates = [r for r in relations if r.get("status") in ("candidate", "gap")]
    if candidates:
        review_queue.append({"type": "candidate_relations", "count": len(candidates),
                             "desc": f"{len(candidates)} 条候选/缺口关系尚无可复核证据",
                             "fix": "补数据窗口、文档依据或人审;不要直接升级为 verified"})
    semantic_disputes = [r for r in relations
                         if (r.get("semantic_status") or r.get("semantic"))
                         in ("fail", "rejected", "disputed")]
    if semantic_disputes:
        review_queue.append({"type": "semantic_disputes", "count": len(semantic_disputes),
                             "desc": f"{len(semantic_disputes)} 条关系的数据证据与语义复审存在争议",
                             "fix": "进入人审；争议关系不计入 CQ 强路径，人工确认后再置为 asserted"})
    query_errors = list((ir.get("scenario") or {}).get("query_errors") or [])
    if query_errors:
        review_queue.append({"type": "adjudication_query_errors", "count": len(query_errors),
                             "desc": f"构建取证过程中有 {len(query_errors)} 项查询失败，不能解释为没有证据",
                             "fix": "修复数据库/表结构/查询问题后重新取证；失败项不得升级为 verified"})
    for item in grounding["issues"]:
        review_queue.append(item)
        gaps.append(item)
    if definitions["weak"]:
        item = {"type": "weak_definitions", "count": definitions["weak"],
                "desc": f"{definitions['weak']} 个对象的定义质量低于 {definition_threshold:g}",
                "fix": "补非循环的属加种差定义与反例"}
        review_queue.append(item); gaps.append(item)

    if cqs:
        cq = cq_check.check_all(cqs, ir)
        cq["provided"] = True
        cq_gaps = cq_check.gaps_from(cq)
        gaps.extend(cq_gaps)
        review_queue.extend(cq_gaps)
    else:
        cq = {"provided": False, "total": 0, "counts": {}, "coverage": None, "items": [],
              "note": "未提供能力问题,不生成虚假的 CQ 覆盖率"}
        review_queue.append({"type": "cq_not_provided", "desc": "本轮未提供能力问题(CQ),尚未做业务适用性验收",
                             "fix": "补充 3~10 个关键业务问题后重新执行验收检查"})

    result = "fail" if blocking_issues else ("review" if review_queue else "pass")
    return {
        "result": result,
        "gate": result,  # 兼容 0.1 版 API；新代码应读取 result。
        "blocking_pass": not blocking_issues,
        "hard_pass": not blocking_issues,  # 兼容旧客户端。
        "summary": {"blocking_issues": len(blocking_issues), "hard_errors": len(blocking_issues),
                    "review_items": len(review_queue),
                    "verified_checked": evidence["checked"], "candidate_relations": len(candidates)},
        "health": health,
        "evidence": evidence,
        "grounding": grounding,
        "semantic": {"disputed": len(semantic_disputes),
                     "strong_for_cq": sum(1 for r in relations if cq_check.relation_is_strong(r))},
        "definitions": definitions,
        "cq": cq,
        "blocking_issues": blocking_issues,
        "hard_errors": blocking_issues,  # 兼容旧客户端。
        "review_queue": review_queue[:100],
        "gaps": gaps[:200],
        "note": "确定性验收检查：fail 表示结构或 verified 证据契约不满足；review 表示仍需人工复核",
    }

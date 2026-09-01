#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成内容的确定性语义与证据检查。

本模块不调用大语言模型。它只处理两类可机械判定的问题：

1. 名称明确表示单据、记录或报告时，优先判为信息对象（``ice``），避免把
   “生产工单”“维修记录”等信息载体误当作其所描述的业务过程；
2. 正例只有在输入证据中能逐字定位，或已有明确的 observed 来源标记时才展示。

规则用于构建后裁决和既有构建产物的只读净化。只读净化返回深拷贝，不改写历史
JSON；这样既不继续传播无来源文本，也保留原始产物供审计和必要时回溯。
"""
from __future__ import annotations

import copy
import re
from typing import Iterable

import ontology_grounding


VALID_KINDS = {"object", "event", "action", "asset", "role", "ice"}

# 信息载体优先于活动词。例如“质量检验记录”是记录，不是检验活动本身。
_INFORMATION_TERMS = re.compile(
    r"(?:订单|工单|单据|记录|日志|台账|目录|清单|报告|报表|申请单|通知单|计划书|"
    r"方案|合同|发票|凭证|许可证|档案|标准文本|规范文本|指标定义|地址簿|"
    r"(?:^|[_\W])(?:order|work[_ -]?order|record|log|report|invoice|contract|document|"
    r"catalog|register|ledger|request|notice|permit|form)(?:$|[_\W]))",
    re.I,
)
_EVENT_TERMS = re.compile(
    r"(?:事件|过程|活动|事故|故障|停机|报警|交付|发货|收货|退货|检验|维修|生产|作业|"
    r"(?:^|[_\W])(?:event|process|activity|incident|failure|downtime|alarm|shipment|"
    r"delivery|inspection|repair|production)(?:$|[_\W]))",
    re.I,
)
_SPACE = re.compile(r"\s+")


def _text(*parts) -> str:
    return " ".join(str(x or "").strip() for x in parts if str(x or "").strip())


def normalize_kind(kind, *, name="", cn="", table="", definition=""):
    """按可解释词项修正对象种类，返回 ``(kind, reason_or_none)``。

    ``action``、``asset``、``role`` 是显式建模决定，不在这里重写。其余类型只在
    名称或定义含有明确词项时调整；没有强信号时保留调用方给出的合法类型。
    """
    raw = kind if kind in VALID_KINDS else "object"
    if raw in {"action", "asset", "role"}:
        return raw, None
    value = _text(name, cn, table, definition)
    if _INFORMATION_TERMS.search(value):
        return "ice", None if raw == "ice" else "information_artifact_term"
    if _EVENT_TERMS.search(value):
        return "event", None if raw == "event" else "event_or_process_term"
    return raw, None


def definition_conflicts(definition, kind):
    """判断定义是否把信息对象与其所描述的过程混为一谈。"""
    value = str(definition or "").strip()
    if not value:
        return False
    has_info = bool(_INFORMATION_TERMS.search(value))
    has_event = bool(_EVENT_TERMS.search(value))
    return (kind == "ice" and has_event and not has_info) or (
        kind == "event" and has_info and not has_event
    )


def _normalized_excerpt(value):
    return _SPACE.sub(" ", str(value or "")).strip()


def locate_example(example, sources: Iterable[tuple[str, str]]):
    """在证据文本中逐字定位正例，返回来源名；找不到则返回 ``None``。

    少于四个字符的片段区分度过低，不能据此声称例子来自输入证据。
    """
    needle = _normalized_excerpt(example)
    if len(needle) < 4:
        return None
    for label, raw in sources:
        if needle in _normalized_excerpt(raw):
            return str(label)
    return None


def sanitize_ir(ir, *, sources=()):
    """深拷贝并净化 IR 中的对象类型和无来源正例。

    历史产物若已经带有 ``example_provenance.status=observed``，继续保留；否则必须
    能在本次提供的 ``sources`` 中定位。函数只返回净化副本，不修改输入对象。
    """
    if not isinstance(ir, dict):
        return ir
    out = copy.deepcopy(ir)
    changed_kinds = removed_examples = rejected_definitions = 0
    for obj in out.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        old_kind = obj.get("kind") if obj.get("kind") in VALID_KINDS else "object"
        kind, reason = normalize_kind(
            old_kind,
            name=obj.get("name"),
            cn=obj.get("cn"),
            table=obj.get("table") or " ".join(obj.get("tables") or []),
            definition=obj.get("definition"),
        )
        obj["kind"] = kind
        if reason:
            changed_kinds += 1
            obj["kind_review"] = {
                "original": old_kind,
                "normalized": kind,
                "rule": reason,
                "status": "deterministic_normalization",
            }
            old_bfo = obj.get("bfo")
            obj["bfo"] = ontology_grounding.default_category(kind)
            obj["bfo_review"] = {
                "original": old_bfo,
                "normalized": obj["bfo"],
                "reason": "kind_normalized",
            }
        if definition_conflicts(obj.get("definition"), kind):
            obj["definition"] = ""
            obj["definition_status"] = "withheld_kind_conflict"
            rejected_definitions += 1
        elif (obj.get("definition") or "").strip():
            obj.setdefault("definition_status", "model_proposed_pending_review")

        example = (obj.get("example") or "").strip()
        provenance = obj.get("example_provenance") or {}
        observed = provenance.get("status") == "observed" and provenance.get("source")
        located = locate_example(example, sources) if example and not observed else None
        if example and not observed and not located:
            obj["example"] = ""
            obj["example_provenance"] = {
                "status": "withheld_no_source",
                "note": "未能在输入证据中逐字定位，故不作为实例展示",
            }
            removed_examples += 1
        elif example:
            obj["example_provenance"] = {
                "status": "observed",
                "source": provenance.get("source") or located,
            }
        if (obj.get("counterExample") or "").strip():
            obj.setdefault("counterexample_status", "illustrative_pending_review")

    scenario = out.setdefault("scenario", {})
    scenario["content_quality"] = {
        "kind_normalized": changed_kinds,
        "unsupported_examples_withheld": removed_examples,
        "conflicting_definitions_withheld": rejected_definitions,
        "mode": "deterministic_non_destructive_view",
    }
    return out

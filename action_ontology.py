#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已登记的动作类型投影为本体 IR 中的一等节点。

动作注册表是系统内已经存在、可调用的配置事实；它与从需求文档中由模型提出的
动作候选不同。投影只表达“系统中登记了这个动作，并配置了对象/表绑定”，不会把
审计记录式 PoC 说成真实业务系统写回，也不会把自动绑定冒充领域专家确认。
"""
from __future__ import annotations

import copy
import re


def _node_key(action_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(action_id or "").strip())[:80]
    return "action__" + safe if safe else ""


def _object_key(obj):
    return obj.get("id") or obj.get("name")


def _tables(obj):
    values = list(obj.get("tables") or [])
    if obj.get("table"):
        values.append(obj["table"])
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _targets(objects, action):
    """按显式对象标识或绑定表匹配目标；不做模糊猜测。"""
    object_ref = str(action.get("object") or "").strip().lower()
    table_ref = str(action.get("object_table") or "").strip().lower()
    out = []
    for obj in objects:
        if obj.get("kind") == "action":
            continue
        aliases = {
            str(obj.get(key) or "").strip().lower()
            for key in ("id", "name", "cn")
            if str(obj.get(key) or "").strip()
        }
        if (table_ref and table_ref in _tables(obj)) or (object_ref and object_ref in aliases):
            key = _object_key(obj)
            if key and key not in out:
                out.append(key)
    return out


def project_registered_actions(ir, action_types):
    """原位投影启用动作，并返回统计；重复调用保持幂等。

    关系使用 ``candidate``，因为注册表绑定可以证明“已配置”，却不能替代领域专家对
    “该动作在业务语义上确实作用于该对象”的确认。动作节点本身携带
    ``binding_status=configured`` 和 ``execution_mode=decision_capture``，供 UI 如实显示。
    """
    if not isinstance(ir, dict):
        raise TypeError("ir 必须是 dict")
    objects = ir.setdefault("objects", [])
    relations = ir.setdefault("relations", [])
    existing_by_action = {
        str(obj.get("action_id")): obj
        for obj in objects
        if obj.get("kind") == "action" and obj.get("action_id")
    }
    existing_names = {_object_key(obj) for obj in objects if _object_key(obj)}
    existing_bindings = {
        (rel.get("source_concept"), rel.get("target_concept"), rel.get("verb"))
        for rel in relations
    }
    added_nodes = added_relations = enriched_nodes = 0

    for raw in action_types or []:
        if not isinstance(raw, dict) or raw.get("enabled") is False:
            continue
        action_id = str(raw.get("id") or "").strip()
        node_key = _node_key(action_id)
        if not action_id or not node_key:
            continue
        targets = _targets(objects, raw)
        risk = raw.get("risk") if raw.get("risk") in ("low", "high") else "high"
        spec = {
            "action_id": action_id,
            "risk": risk,
            "approval_required": risk == "high",
            "parameters": copy.deepcopy(raw.get("params") or []),
            "effects": copy.deepcopy(raw.get("effects") or []),
            "execution_mode": "decision_capture",
            "real_writeback": False,
            "connector_status": "not_connected",
            "invocable": True,
            "binding_status": "configured" if targets else "unbound",
            "target_objects": list(targets),
        }
        node = existing_by_action.get(action_id)
        if node is None:
            if node_key in existing_names:
                continue
            node = {
                "name": node_key,
                "cn": str(raw.get("cn") or action_id)[:80],
                "kind": "action",
                "table": None,
                "tables": [],
                "field_count": 0,
                "indicators": [],
                "candidate": False,
                "remark": str(raw.get("desc") or "已登记动作类型")[:500],
                "definition": str(raw.get("desc") or "")[:500],
                "isPrimitive": True,
                "example": "",
                "counterExample": "",
                "maturity": "Provisional",
                "bfo": "PlannedProcess",
                "evidence": {"sources": [str(raw.get("source") or "动作注册表")]},
                "provenance": {
                    "directSource": "action_registry",
                    "adaptedFrom": [str(raw.get("source") or "动作注册表")],
                    "excerptedFrom": None,
                },
            }
            objects.append(node)
            existing_names.add(node_key)
            existing_by_action[action_id] = node
            added_nodes += 1
        else:
            enriched_nodes += 1
            node["candidate"] = False
            node["bfo"] = "PlannedProcess"
        node["action_id"] = action_id
        node["action_spec"] = spec

        for target in targets:
            signature = (node["name"], target, "作用于")
            if signature in existing_bindings:
                continue
            relations.append({
                "source_concept": node["name"],
                "target_concept": target,
                "verb": "作用于",
                "status": "candidate",
                "evidence_status": "configured",
                "semantic": "not_reviewed",
                "semantic_status": "not_reviewed",
                "overlap": None,
                "note": "动作注册表显式绑定；仍需领域专家确认业务语义",
                "evidence": {
                    "source": "action_registry",
                    "action_id": action_id,
                    "binding": "object_table" if raw.get("object_table") else "object",
                    "object_table": raw.get("object_table") or "",
                },
                "founded_relation": "",
                "grounding_iri": "",
                "grounding_status": "unmapped",
                "grounding_reason": "动作配置绑定不是数据库外键，需人审后再决定上层关系",
                "temporal": "",
            })
            existing_bindings.add(signature)
            added_relations += 1

    scenario = ir.setdefault("scenario", {})
    scenario["object_count"] = len(objects)
    scenario["relation_count"] = len(relations)
    scenario["action_count"] = sum(1 for obj in objects if obj.get("kind") == "action")
    scenario["action_projection"] = {
        "source": "action_registry",
        "added_nodes": added_nodes,
        "enriched_nodes": enriched_nodes,
        "added_relations": added_relations,
        "execution_mode": "decision_capture",
        "real_writeback": False,
    }
    return scenario["action_projection"]

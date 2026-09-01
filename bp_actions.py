#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动作类型、调用与审批 blueprint（DR-020 / DR-043）。

路由（7）：GET /api/actions、POST /api/action/type、type/update、type/delete、
invoke、GET log、POST approve。动作仍是可审计的决策记录，不声称写回业务系统。
"""
import time
import uuid

from flask import Blueprint, jsonify, request

from srv_actions import (
    ACTION_LOG_F,
    ACTION_TYPES_F,
    load_action_log,
    load_action_types,
    valid_action_params,
)
from srv_context import _WRITE_LOCK, _atomic_json

bp_actions = Blueprint("actions", __name__)
_table_list_fn = None


def configure_actions(table_list_fn):
    """注入主应用的数据目录读取函数，避免 blueprint 反向导入 ``server``。"""
    global _table_list_fn
    _table_list_fn = table_list_fn


@bp_actions.get("/api/actions")
def actions_list():
    log = load_action_log()
    return jsonify({
        "types": load_action_types(),
        "pending": sum(1 for item in log if item["status"] == "pending"),
        "executed": sum(1 for item in log if item["status"] == "executed"),
        "denied": sum(1 for item in log if item["status"] == "denied"),
        "execution": {
            "mode": "decision_capture",
            "real_writeback": False,
            "connector_status": "not_connected",
        },
    })


@bp_actions.post("/api/action/type")
def action_type_create():
    body = request.json or {}
    cn = str(body.get("cn") or "").strip()[:40]
    creator = str(body.get("creator") or "").strip()[:40]
    if not cn:
        return jsonify({"error": "动作名称必填"}), 400
    if not creator:
        return jsonify({"error": "创建人必填(姓名或工号,入登记)"}), 400
    risk = body.get("risk") if body.get("risk") in ("low", "high") else "high"
    params = valid_action_params(body.get("params") or [])
    if params is None:
        return jsonify({"error": "参数 schema 非法(name 须小写下划线,select 须给 options)"}), 400
    object_table = str(body.get("object_table") or "").strip()[:60]
    if object_table and _table_list_fn is not None:
        try:
            known = {table["name"].lower() for table in _table_list_fn()}
            if object_table.lower() not in known:
                return jsonify({"error": f"绑定表 {object_table} 不在数据目录中"}), 400
        except Exception:
            # 数据目录短暂不可读不应阻止登记；后续兼容性/漂移检查会显式报告失配。
            pass
    type_id = "act_" + uuid.uuid4().hex[:6]
    action_type = {
        "id": type_id,
        "cn": cn,
        "object": str(body.get("object") or "").strip()[:40] or (object_table or "—"),
        "object_table": object_table,
        "desc": str(body.get("desc") or "").strip()[:300],
        "risk": risk,
        "source": "自建动作 · " + creator,
        "params": params,
        "effects": [{
            "type": "append_event",
            "event": str(body.get("effects_note") or "").strip()[:120] or "动作已登记",
        }],
        "builtin": False,
        "enabled": True,
        "created_by": creator,
        "created_ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    with _WRITE_LOCK:
        action_types = load_action_types()
        action_types.append(action_type)
        _atomic_json(ACTION_TYPES_F, action_types)
    return jsonify({"ok": True, "type": action_type})


@bp_actions.post("/api/action/type/update")
def action_type_update():
    body = request.json or {}
    type_id = body.get("id")
    with _WRITE_LOCK:
        action_types = load_action_types()
        action_type = next((item for item in action_types if item["id"] == type_id), None)
        if not action_type:
            return jsonify({"error": "动作类型不存在"}), 404
        if "enabled" in body:
            action_type["enabled"] = bool(body["enabled"])
        if "desc" in body:
            action_type["desc"] = str(body["desc"]).strip()[:300]
        if not action_type.get("builtin"):
            if "cn" in body and str(body["cn"]).strip():
                action_type["cn"] = str(body["cn"]).strip()[:40]
            if "risk" in body and body["risk"] in ("low", "high"):
                action_type["risk"] = body["risk"]
            if "object" in body:
                action_type["object"] = str(body["object"]).strip()[:40]
            if "object_table" in body:
                action_type["object_table"] = str(body["object_table"]).strip()[:60]
            if "params" in body:
                params = valid_action_params(body["params"])
                if params is None:
                    return jsonify({"error": "参数 schema 非法"}), 400
                action_type["params"] = params
            if "effects_note" in body:
                action_type["effects"] = [{
                    "type": "append_event",
                    "event": str(body["effects_note"]).strip()[:120] or "动作已登记",
                }]
        elif set(body) - {"id", "enabled", "desc"}:
            return jsonify({"error": "内置动作只允许 停用/启用 与修改说明"}), 400
        action_type["updated_ts"] = time.strftime("%Y-%m-%d %H:%M")
        _atomic_json(ACTION_TYPES_F, action_types)
    return jsonify({"ok": True, "type": action_type})


@bp_actions.post("/api/action/type/delete")
def action_type_delete():
    type_id = (request.json or {}).get("id")
    with _WRITE_LOCK:
        action_types = load_action_types()
        action_type = next((item for item in action_types if item["id"] == type_id), None)
        if not action_type:
            return jsonify({"error": "动作类型不存在"}), 404
        if action_type.get("builtin"):
            return jsonify({"error": "内置动作不可删除(可停用)"}), 400
        _atomic_json(ACTION_TYPES_F, [item for item in action_types if item["id"] != type_id])
    return jsonify({"ok": True})


@bp_actions.post("/api/action/invoke")
def action_invoke():
    """发起动作；低风险直接登记，高风险进入审批队列。"""
    body = request.json or {}
    action_type = next(
        (item for item in load_action_types() if item["id"] == body.get("action_id")),
        None,
    )
    if not action_type:
        return jsonify({"error": "动作类型不存在"}), 404
    if action_type.get("enabled") is False:
        return jsonify({"error": "该动作类型已停用,不可发起"}), 400
    operator = str(body.get("operator") or "").strip()[:40]
    if not operator:
        return jsonify({"error": "操作人必填(姓名或工号)"}), 400
    params = body.get("params") or {}
    clean = {}
    for param in action_type["params"]:
        value = str(params.get(param["name"]) or "").strip()
        if param.get("required") and not value:
            return jsonify({"error": f"参数「{param['cn']}」必填"}), 400
        if param.get("type") == "select" and value and value not in (param.get("options") or []):
            return jsonify({"error": f"参数「{param['cn']}」须为 {'/'.join(param.get('options') or [])}"}), 400
        if param.get("type") == "number" and value:
            try:
                float(value)
            except (TypeError, ValueError):
                return jsonify({"error": f"参数「{param['cn']}」须为数字"}), 400
        clean[param["name"]] = value[:500]
    item = {
        "id": uuid.uuid4().hex[:8],
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "action_id": action_type["id"],
        "action_cn": action_type["cn"],
        "object": action_type.get("object", ""),
        "risk": action_type.get("risk", "low"),
        "operator": operator,
        "params": clean,
        "status": "pending" if action_type.get("risk") == "high" else "executed",
        "execution_mode": "decision_capture",
        "real_writeback": False,
        "effects": [],
        "approver": "",
        "approve_comment": "",
    }
    if item["status"] == "executed":
        item["effects"] = _recorded_effects(action_type)
    with _WRITE_LOCK:
        log = load_action_log()
        log.insert(0, item)
        _atomic_json(ACTION_LOG_F, log[:1000])
    return jsonify({
        "ok": True,
        "id": item["id"],
        "status": item["status"],
        "execution_mode": "decision_capture",
        "real_writeback": False,
    })


def _recorded_effects(action_type):
    return [
        ((effect.get("event") or "") + "(已记录)")
        if effect.get("type") == "append_event"
        else (effect.get("target") or effect.get("type") or "")
        for effect in (action_type.get("effects") or [])
    ]


@bp_actions.get("/api/action/log")
def action_log():
    return jsonify({"items": load_action_log()[:200]})


@bp_actions.post("/api/action/approve")
def action_approve():
    """审批形成动作记录；当前仍不写回业务系统。"""
    body = request.json or {}
    record_id, decision = body.get("id", ""), body.get("decision", "")
    approver = str(body.get("approver") or "").strip()[:40]
    comment = str(body.get("comment") or "").strip()[:300]
    if decision not in ("approve", "deny"):
        return jsonify({"error": "decision 须为 approve/deny"}), 400
    if not approver:
        return jsonify({"error": "审批人必填"}), 400
    if decision == "deny" and not comment:
        return jsonify({"error": "驳回必须填写意见"}), 400
    with _WRITE_LOCK:
        log = load_action_log()
        item = next((entry for entry in log if entry["id"] == record_id), None)
        if not item:
            return jsonify({"error": "记录不存在"}), 404
        if item["status"] != "pending":
            return jsonify({"error": "该动作不在待审批状态"}), 400
        action_type = next(
            (entry for entry in load_action_types() if entry["id"] == item["action_id"]),
            {},
        ) or {}
        item["approver"] = approver
        item["approve_comment"] = comment
        item["approve_ts"] = time.strftime("%Y-%m-%d %H:%M")
        if decision == "approve":
            item["status"] = "executed"
            item["effects"] = _recorded_effects(action_type)
        else:
            item["status"] = "denied"
        _atomic_json(ACTION_LOG_F, log)
    return jsonify({"ok": True, "status": item["status"]})

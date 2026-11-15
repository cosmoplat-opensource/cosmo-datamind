#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动作类型、调用与审批 blueprint（DR-020 / DR-043）。

路由（7）：GET /api/actions、POST /api/action/type、type/update、type/delete、
invoke、GET log、POST approve。动作默认是可审计的决策记录，不声称写回业务系统。

可执行动作（DR-120）：环境变量 DATAMIND_ACTION_EXECUTORS 是 JSON 映射 {动作类型 id: 执行器地址}。
执行器只能由部署配置指定、只允许本机地址（127.0.0.1 / localhost / ::1），请求方不能指定，防止服务端请求伪造。
低风险动作在发起时调用执行器；高风险动作在审批通过后调用。执行器的返回写入动作记录的 result，
execution_mode 记为 executor，real_writeback 由执行器自报（试算为 false，写回业务系统为 true）。

执行器约定：POST JSON {"params", "operator", "record_id"} → JSON
{"ok": bool, "summary": 文字结论, "real_writeback": bool, "effects": [...], 其余字段原样保留}。
"""
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Callable

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
ACTION_STATUSES = ("pending", "executed", "denied", "failed")     # 动作记录的全部状态
EXECUTOR_TIMEOUT = 600
_table_list_fn = None
_scope_fields: Callable[[], dict] = lambda: {}
_scope_matches: Callable[[dict], bool] = lambda record: True


def _executor_url(action_id):
    """部署配置里为该动作类型指定的执行器地址;没有配置、配置不合法或不是本机地址时返回 None。"""
    try:
        conf = json.loads(os.environ.get("DATAMIND_ACTION_EXECUTORS") or "{}")
    except ValueError:
        return None
    url = conf.get(action_id) if isinstance(conf, dict) else None
    if not isinstance(url, str) or not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return None
    return url


def _run_executor(item):
    """调用执行器并把结果写入记录;失败时记在记录里,不抛出。没有配置执行器时原样返回。"""
    url = _executor_url(item["action_id"])
    if not url:
        return item
    started = time.time()
    item["execution_mode"] = "executor"
    try:
        payload = json.dumps({"params": item["params"], "operator": item["operator"], "record_id": item["id"]},
                             ensure_ascii=False).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=EXECUTOR_TIMEOUT) as resp:
            result = json.loads(resp.read())
        if not isinstance(result, dict):
            raise ValueError("执行器返回的不是 JSON 对象")
        item["real_writeback"] = bool(result.get("real_writeback"))
        item["result"] = result
        item["effects"] = [str(e)[:200] for e in (result.get("effects") or [])][:20]
        if result.get("ok") is False:
            item["status"] = "failed"
    except urllib.error.HTTPError as exc:      # 保留执行器自己给出的说明,不只是状态码
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        item["status"] = "failed"
        item["real_writeback"] = bool(body.get("real_writeback"))
        item["result"] = {"error": f"HTTP {exc.code}", "summary": str(body.get("summary") or "")[:300]}
    except Exception as exc:  # noqa: BLE001 —— 失败留在记录里,接口照常应答
        item["status"] = "failed"
        item["result"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    item["executor_seconds"] = round(time.time() - started, 1)
    return item


def configure_actions(table_list_fn, scope_fields=lambda: {}, scope_matches=lambda record: True):
    """注入主应用的数据目录读取函数，避免 blueprint 反向导入 ``server``。"""
    global _table_list_fn, _scope_fields, _scope_matches
    _table_list_fn = table_list_fn
    _scope_fields, _scope_matches = scope_fields, scope_matches


@bp_actions.get("/api/actions")
def actions_list():
    log = [item for item in load_action_log() if _scope_matches(item)]
    return jsonify({
        "types": [item for item in load_action_types() if not item.get('source_path_fp') or _scope_matches(item)],
        "pending": sum(1 for item in log if item["status"] == "pending"),
        "executed": sum(1 for item in log if item["status"] == "executed"),
        "denied": sum(1 for item in log if item["status"] == "denied"),
        "failed": sum(1 for item in log if item["status"] == "failed"),
        "execution": {
            "mode": "decision_capture",
            "real_writeback": False,
            "connector_status": "not_connected",
            # DR-120 配置了执行器的动作类型:这些动作发起或批准后会调用执行器,其余只记录
            "executors": sorted(t["id"] for t in load_action_types() if _executor_url(t["id"])),
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
        **_scope_fields(),
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
        if action_type.get('source_path_fp') and not _scope_matches(action_type):
            return jsonify({"error": "动作类型属于另一数据库，请切换来源后编辑"}), 409
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
        if action_type.get('source_path_fp') and not _scope_matches(action_type):
            return jsonify({"error": "动作类型属于另一数据库，请切换来源后删除"}), 409
        if action_type.get("builtin"):
            return jsonify({"error": "内置动作不可删除(可停用)"}), 400
        _atomic_json(ACTION_TYPES_F, [item for item in action_types if item["id"] != type_id])
    return jsonify({"ok": True})


@bp_actions.post("/api/action/invoke")
def action_invoke():
    """发起动作；低风险直接登记(配置了执行器则调用)，高风险进入审批队列。"""
    body = request.get_json()
    if not isinstance(body, dict):
        return jsonify({"error": "请求体必须为 JSON 对象"}), 400
    code, out = invoke(body.get("action_id"), body.get("params", {}), body.get("operator"))
    return jsonify(out), code


def invoke(action_id, params, operator):
    """发起动作(HTTP 接口与问数推演共用):返回 (状态码, 响应体)。"""
    if not isinstance(params, dict):
        return 400, {"error": "params 必须为 JSON 对象"}
    action_type = next(
        (item for item in load_action_types() if item["id"] == action_id),
        None,
    )
    if not action_type:
        return 404, {"error": "动作类型不存在"}
    if action_type.get('source_path_fp') and not _scope_matches(action_type):
        return 409, {'error': '动作类型属于另一数据库，请先切换数据源'}
    if action_type.get("enabled") is False:
        return 400, {"error": "该动作类型已停用,不可发起"}
    operator = str(operator or "").strip()[:40]
    if not operator:
        return 400, {"error": "操作人必填(姓名或工号)"}
    clean = {}
    for param in action_type["params"]:
        raw_value = params.get(param["name"])
        value = "" if raw_value is None else str(raw_value).strip()
        if param.get("required") and not value:
            return 400, {"error": f"参数「{param['cn']}」必填"}
        if param.get("type") == "select" and value and value not in (param.get("options") or []):
            return 400, {"error": f"参数「{param['cn']}」须为 {'/'.join(param.get('options') or [])}"}
        if param.get("type") == "number" and value:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return 400, {"error": f"参数「{param['cn']}」须为数字"}
            if not math.isfinite(number):
                return 400, {"error": f"参数「{param['cn']}」须为有限数字"}
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
        **_scope_fields(),
    }
    if item["status"] == "executed":
        item["effects"] = _recorded_effects(action_type)
        _run_executor(item)                       # 没有配置执行器时不做任何事
    with _WRITE_LOCK:
        log = load_action_log()
        log.insert(0, item)
        _atomic_json(ACTION_LOG_F, log[:1000])
    return 200, {
        "ok": item["status"] != "failed",
        "id": item["id"],
        "status": item["status"],
        "execution_mode": item["execution_mode"],
        "real_writeback": item["real_writeback"],
        **({"result": item["result"]} if "result" in item else {}),
    }


def _recorded_effects(action_type):
    return [
        ((effect.get("event") or "") + "(已记录)")
        if effect.get("type") == "append_event"
        else (effect.get("target") or effect.get("type") or "")
        for effect in (action_type.get("effects") or [])
    ]


@bp_actions.get("/api/action/log")
def action_log():
    return jsonify({"items": [item for item in load_action_log() if _scope_matches(item)][:200]})


@bp_actions.post("/api/action/approve")
def action_approve():
    """审批形成动作记录；动作配置了执行器时，批准后调用执行器，否则只记录。"""
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
        if not _scope_matches(item):
            return jsonify({'error': '动作记录属于另一数据库，请先切换数据源'}), 409
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
    if decision == "approve" and _executor_url(item["action_id"]):
        _run_executor(item)                       # 审批通过后执行;调用在锁外进行,完成后再写回记录
        with _WRITE_LOCK:
            log = load_action_log()
            for i, entry in enumerate(log):
                if entry["id"] == item["id"]:
                    log[i] = item
            _atomic_json(ACTION_LOG_F, log)
    return jsonify({"ok": item["status"] != "failed", "status": item["status"],
                    **({"result": item["result"]} if "result" in item else {})})

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动作类型、调用与审批 blueprint（DR-020 / DR-043）。

路由（8）：GET /api/actions、POST /api/action/type、type/update、type/delete、
invoke、GET log、POST approve、POST resolve。动作默认是可审计的决策记录，不声称写回业务系统。

可执行动作（DR-120、DR-125）：环境变量 DATAMIND_ACTION_EXECUTORS 是 JSON 映射，动作类型 id 对应执行器：
  {"act_x": "http://127.0.0.1:8096/run"}                                   可写回业务系统的执行器
  {"act_y": {"url": "http://127.0.0.1:8096/trial", "simulate_only": true}} 只做试算、不写回的执行器
执行器只能由部署配置指定、只允许本机地址，请求方不能指定；调用不跟随重定向、不走环境代理。
低风险动作在发起时调用执行器；高风险动作在审批通过后调用。问数推演只能调用标了 simulate_only 的执行器。

记录的状态：pending 待审批、executing 已发出调用尚无回执、executed 已执行、failed 执行失败、
unknown 结果未知（超时、连接中断、回执不完整或进程中断）、denied 已驳回。
调用之前先把 executing 落盘；回执里 ok 为 true 才算已执行。结果未知的记录由人核对业务系统后处置。

执行器约定：POST JSON {"params", "operator", "record_id", "idempotency_key", "mode": "simulate"|"execute"}
→ JSON {"ok": bool, "summary": 文字结论, "real_writeback": bool, "effects": [...], 其余字段原样保留}。
同一个 idempotency_key 重复到达时，执行器不应重复执行。
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
ACTION_STATUSES = ("pending", "executing", "executed", "denied", "failed", "unknown")     # 动作记录的全部状态
EXECUTOR_TIMEOUT = 600
STALE_AFTER = EXECUTOR_TIMEOUT + 120       # executing 超过这个秒数仍无回执:进程多半中断过,记为结果未知


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """执行器地址限定为本机;跟随重定向会让实际连接离开本机,所以一律不跟随。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "执行器返回了重定向,已拒绝跟随", headers, fp)


# 不读环境变量里的代理设置(否则发往本机的请求可能被转给代理),不跟随重定向
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
_table_list_fn = None
_scope_fields: Callable[[], dict] = lambda: {}
_scope_matches: Callable[[dict], bool] = lambda record: True


def _executor_spec(action_id):
    """部署配置里为该动作类型指定的执行器 → {"url", "simulate_only"};没有配置、配置不合法或不是本机地址时返回 None。"""
    try:
        conf = json.loads(os.environ.get("DATAMIND_ACTION_EXECUTORS") or "{}")
    except ValueError:
        return None
    entry = conf.get(action_id) if isinstance(conf, dict) else None
    if isinstance(entry, str):
        entry = {"url": entry}
    if not isinstance(entry, dict) or not isinstance(entry.get("url"), str) or not entry["url"]:
        return None
    parsed = urllib.parse.urlparse(entry["url"])
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        return None
    return {"url": entry["url"], "simulate_only": entry.get("simulate_only") is True}


def _executor_url(action_id):
    spec = _executor_spec(action_id)
    return spec["url"] if spec else None


def _run_executor(item, mode="execute"):
    """调用执行器并按回执更新记录;任何失败都留在记录里,不抛出。调用方须已把 executing 状态落盘。"""
    spec = _executor_spec(item["action_id"])
    if not spec:
        return item
    started = time.time()
    item["execution_mode"] = "simulate" if mode == "simulate" else "executor"
    try:
        payload = json.dumps({"params": item["params"], "operator": item["operator"], "record_id": item["id"],
                              "idempotency_key": item["id"], "mode": mode}, ensure_ascii=False).encode()
        req = urllib.request.Request(spec["url"], data=payload, headers={"Content-Type": "application/json"})
        with _OPENER.open(req, timeout=EXECUTOR_TIMEOUT) as resp:
            result = json.loads(resp.read())
        if not isinstance(result, dict):
            raise ValueError("执行器返回的不是 JSON 对象")
        item["real_writeback"] = bool(result.get("real_writeback"))
        item["result"] = result
        item["effects"] = [str(e)[:200] for e in (result.get("effects") or [])][:20]
        if mode == "simulate" and item["real_writeback"]:
            item["status"] = "failed"          # 只读试算却声称写回:不当作成功,留痕待查
            item["result"] = {**result, "violation": "只读试算的执行器声称写回了业务系统"}
        elif result.get("ok") is True:
            item["status"] = "executed"
        elif result.get("ok") is False:
            item["status"] = "failed"
        else:                                   # 回执里没有明确的 ok:不能当作已执行
            item["status"] = "unknown"
            item["result"] = {**result, "error": "回执缺少明确的 ok 字段"}
            if "real_writeback" not in result:
                item["real_writeback"] = None        # 结果未知时不能写成「未写回」
    except urllib.error.HTTPError as exc:      # 执行器答复了错误状态:保留它自己给出的说明
        try:
            body = json.loads(exc.read() or b"{}")
        except Exception:  # noqa: BLE001
            body = {}
        body = body if isinstance(body, dict) else {}
        item["result"] = {"error": f"HTTP {exc.code}", "summary": str(body.get("summary") or exc.reason or "")[:300]}
        if exc.code >= 500 and body.get("ok") is not False:
            # DR-129 5xx 说的是这次通信出了错(执行器中途崩溃、前面的代理超时),不说明业务操作没有提交;
            # 回执里没有明确的 ok=false 时,结果未知,等人核对
            item["status"] = "unknown"
            item["real_writeback"] = None
            item["result"]["summary"] = ((item["result"]["summary"] + ";") if body.get("summary") else "") + \
                "执行器返回了服务端错误且没有给出明确的回执,无法确定是否已执行;请核对业务系统后处置"
        else:                                   # 4xx(请求被拒收),或回执明确说了没有成功
            item["status"] = "failed"
            item["real_writeback"] = bool(body.get("real_writeback"))
    except Exception as exc:  # noqa: BLE001 —— 失败留在记录里,接口照常应答
        reason = getattr(exc, "reason", exc)
        refused = isinstance(reason, ConnectionRefusedError) or isinstance(exc, ConnectionRefusedError)
        # 连接被拒说明请求没有送达,可以确定没执行;超时、连接中断、回执解析失败时执行器可能已经执行,结果未知
        item["status"] = "failed" if refused else "unknown"
        if not refused:
            item["real_writeback"] = None            # 可能已经执行:是否写回同样未知
        item["result"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}",
                          **({} if refused else {"summary": "没有拿到完整的回执,无法确定是否已执行;请核对业务系统后处置"})}
    item["executor_seconds"] = round(time.time() - started, 1)
    return item


def _store(item):
    with _WRITE_LOCK:
        _save_receipt(load_action_log(), item)


def _save_receipt(log, item):
    """调用方持有写锁。最新处置或执行回执排在最前面，同一 ID 只保留一次。"""
    previous = [entry for entry in log if entry.get("id") != item["id"]]
    _atomic_json(ACTION_LOG_F, _retain([item, *previous]))


ACTION_LOG_LIMIT = 1000                 # 已了结记录的保留上限;未了结的记录不因日志变长而被删除
UNRESOLVED = ("pending", "executing", "unknown")


def _retain(log):
    """保留全部未了结记录，以及最近 ACTION_LOG_LIMIT 条已了结记录。"""
    room = ACTION_LOG_LIMIT
    kept = []
    for entry in log:
        if entry.get("status") in UNRESOLVED:
            kept.append(entry)
        elif room:
            kept.append(entry)
            room -= 1
    return kept


def _reconcile(log):
    """调用发出后长时间没有回执的记录(进程中断过)改记为结果未知。返回是否有改动。"""
    changed = False
    for item in log:
        if item.get("status") == "executing" and time.time() - float(item.get("exec_started") or 0) > STALE_AFTER:
            item["status"] = "unknown"
            item["real_writeback"] = None
            item["result"] = {"error": "调用发出后没有回执(进程可能中断过)",
                              "summary": "无法确定是否已执行;请核对业务系统后处置"}
            changed = True
    return changed


def _load_reconciled():
    with _WRITE_LOCK:
        log = load_action_log()
        if _reconcile(log):
            _atomic_json(ACTION_LOG_F, log)
    return log


def configure_actions(table_list_fn, scope_fields=lambda: {}, scope_matches=lambda record: True):
    """注入主应用的数据目录读取函数，避免 blueprint 反向导入 ``server``。"""
    global _table_list_fn, _scope_fields, _scope_matches
    _table_list_fn = table_list_fn
    _scope_fields, _scope_matches = scope_fields, scope_matches


@bp_actions.get("/api/actions")
def actions_list():
    log = [item for item in _load_reconciled() if _scope_matches(item)]
    return jsonify({
        "types": [item for item in load_action_types() if not item.get('source_path_fp') or _scope_matches(item)],
        "pending": sum(1 for item in log if item["status"] == "pending"),
        "executed": sum(1 for item in log if item["status"] == "executed"),
        "denied": sum(1 for item in log if item["status"] == "denied"),
        "failed": sum(1 for item in log if item["status"] == "failed"),
        "executing": sum(1 for item in log if item["status"] == "executing"),
        "unknown": sum(1 for item in log if item["status"] == "unknown"),
        "execution": {
            "mode": "decision_capture",
            "real_writeback": False,
            "connector_status": "not_connected",
            # DR-120 配置了执行器的动作类型:这些动作发起或批准后会调用执行器,其余只记录
            "executors": sorted(t["id"] for t in load_action_types() if _executor_url(t["id"])),
            # 其中只做试算、不写回的:问数推演只能调用这一类(DR-125)
            "simulate_only": sorted(t["id"] for t in load_action_types() if (_executor_spec(t["id"]) or {}).get("simulate_only")),
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


def invoke(action_id, params, operator, simulate=False):
    """发起动作(HTTP 接口与问数推演共用):返回 (状态码, 响应体)。

    simulate=True 是问数推演的调用方式:只允许低风险、且执行器标了 simulate_only 的动作,
    不满足时在调用任何东西、写任何记录之前就拒绝(DR-125)。"""
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
    spec = _executor_spec(action_type["id"])
    if simulate:
        if action_type.get("risk") == "high":
            return 403, {"error": "高风险动作不能由问数发起,请到动作中心发起并走审批", "code": "simulate_not_allowed"}
        if not spec:
            return 409, {"error": "该动作没有配置执行器,无法试算", "code": "no_executor"}
        if not spec["simulate_only"]:
            return 403, {"error": "该动作的执行器没有标记为只做试算,问数不调用;请到动作中心发起", "code": "simulate_not_allowed"}
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
        if spec:
            # 先把「已发出调用、尚无回执」落盘,再调用:进程中途中断也留有记录,按同一个编号对账
            item.update(status="executing", exec_started=time.time())
            _store(item)
            _run_executor(item, "simulate" if simulate else "execute")
    _store(item)
    return 200, {
        "ok": item["status"] in ("executed", "pending"),
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
    # 最近 200 条之外的待审批记录仍要能处理:全部未了结记录都返回,已了结的只返回最近 200 条
    items, shown = [], 0
    log = [item for item in _load_reconciled() if _scope_matches(item)]
    for item in log:
        if item.get("status") in UNRESOLVED:
            items.append(item)
        elif shown < 200:
            items.append(item)
            shown += 1
    return jsonify({"items": items, "total": len(log), "truncated": len(items) < len(log)})


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
            item["effects"] = _recorded_effects(action_type)
            if _executor_spec(item["action_id"]):      # 批准后要调用执行器:先记「已发出调用」,回执到了再定结果
                item.update(status="executing", exec_started=time.time())
            else:
                item["status"] = "executed"
        else:
            item["status"] = "denied"
        _save_receipt(log, item)
    if item["status"] == "executing":
        _run_executor(item)                       # 调用在锁外进行,完成后按回执写回记录
        _store(item)
    return jsonify({"ok": item["status"] in ("executed", "denied"), "status": item["status"],
                    **({"result": item["result"]} if "result" in item else {})})


@bp_actions.post("/api/action/resolve")
def action_resolve():
    """处置结果未知的记录:由人核对业务系统后,确认实际是已执行还是未执行。要署名并写明依据。"""
    body = request.json or {}
    record_id, outcome = body.get("id", ""), body.get("outcome", "")
    operator = str(body.get("operator") or "").strip()[:40]
    comment = str(body.get("comment") or "").strip()[:300]
    if outcome not in ("executed", "failed"):
        return jsonify({"error": "outcome 须为 executed(已执行)或 failed(未执行)"}), 400
    if not operator or not comment:
        return jsonify({"error": "处置人和核对依据必填"}), 400
    with _WRITE_LOCK:
        log = load_action_log()
        item = next((entry for entry in log if entry["id"] == record_id), None)
        if not item:
            return jsonify({"error": "记录不存在"}), 404
        if not _scope_matches(item):
            return jsonify({'error': '动作记录属于另一数据库，请先切换数据源'}), 409
        if item["status"] != "unknown":
            return jsonify({"error": "只有结果未知的记录需要处置"}), 400
        item.update(status=outcome, resolved_by=operator, resolve_comment=comment,
                    resolve_ts=time.strftime("%Y-%m-%d %H:%M"))
        if item.get("real_writeback") is None:       # 人核对后的结论:已执行的正式调用即已写回,未执行即未写回
            item["real_writeback"] = (True if item.get("execution_mode") == "executor" else None) if outcome == "executed" else False
        _save_receipt(log, item)
    return jsonify({"ok": True, "status": item["status"]})

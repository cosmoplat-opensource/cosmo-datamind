#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指标契约 blueprint(DR-054)。

路由(4):GET /api/metric/contract、POST /api/metric/status、POST /api/metric/adjudicate、
GET /api/metric/dimensions。人工只能授予 certified / deprecated;verified 只能由核验产生,
本 blueprint 不提供把指标直接置为 verified 的途径。
"""
import time

from flask import Blueprint, jsonify, request

import metric_contract
import metric_pipeline

bp_metrics = Blueprint("metrics", __name__)
_deps: dict = {}


def configure_metrics(load_graph, open_writable, db_for_source, write_json, write_lock):
    """注入主应用的图谱读写与数据源解析,避免 blueprint 反向导入 ``server``。"""
    _deps.update(load_graph=load_graph, open_writable=open_writable, db_for_source=db_for_source,
                 write_json=write_json, write_lock=write_lock)


def _find(ir, name):
    for layer, arr in ((ir or {}).get("metric_layers") or {}).items():
        for i, m in enumerate(arr or []):
            if isinstance(m, dict) and (m.get("name") == name or m.get("id") == name):
                return layer, i, m
    return None, None, None


@bp_metrics.get("/api/metric/contract")
def metric_contract_view():
    key = (request.args.get("graph") or "demo").strip()
    name = (request.args.get("name") or "").strip()
    ir = _deps["load_graph"](key)
    if ir is None: return jsonify({"error": "非法图谱键"}), 400
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    layer, _, m = _find(ir, name)
    if not m: return jsonify({"error": "指标不存在"}), 404
    out = {"graph": key, "layer": layer, "metric": m, "is_contract": metric_contract.is_contract(m)}
    if metric_contract.is_contract(m):
        try: out["compiled_sql"] = metric_contract.compile_sql(m)
        except ValueError as e: out["compile_error"] = str(e)
        out["caliber"] = metric_contract.describe(m)
        out["allowed_dimensions"] = metric_contract.allowed_dimensions(m, ir)
    return jsonify(out)


@bp_metrics.get("/api/metric/dimensions")
def metric_dimensions():
    key = (request.args.get("graph") or "demo").strip()
    name = (request.args.get("name") or "").strip()
    ir = _deps["load_graph"](key)
    if ir is None: return jsonify({"error": "非法图谱键"}), 400
    _, _, m = _find(ir or {}, name)
    if not m: return jsonify({"error": "指标不存在"}), 404
    if not metric_contract.is_contract(m):
        return jsonify({"error": "该指标尚无契约(旧形状),不能推导维度可达性"}), 400
    return jsonify({"graph": key, "metric": name, **metric_contract.allowed_dimensions(m, ir)})


@bp_metrics.post("/api/metric/status")
def metric_status():
    """人工确认口径:certified(等价于关系的 asserted)/ deprecated / 撤回到 candidate。
    不接受 verified——那只能由核验产生。"""
    body = request.json or {}
    key = str(body.get("graph") or "").strip()
    name = str(body.get("metric") or "").strip()
    status = str(body.get("status") or "").strip()
    reviewer = str(body.get("reviewer") or "").strip()[:40]
    reason = str(body.get("reason") or "").strip()[:200]
    if status not in ("certified", "deprecated", "candidate"):
        return jsonify({"error": "status 只能是 certified / deprecated / candidate;verified 只能由核验产生"}), 400
    if status == "certified" and not reviewer:
        return jsonify({"error": "certified 须记录确认人(reviewer)"}), 400
    ir, wp, err = _deps["open_writable"](key)
    if err: return err
    layer, idx, m = _find(ir, name)
    if not m: return jsonify({"error": "指标不存在"}), 404
    if status == "certified" and not metric_contract.is_contract(m):
        return jsonify({"error": "旧形状指标没有可编译口径,不能确认为 certified;先补契约"}), 400
    m["status"] = status
    m["candidate"] = status == "candidate"
    if status == "certified":
        m["certified_by"], m["certified_at"] = reviewer, time.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        m.pop("certified_by", None); m.pop("certified_at", None)
    if reason: m["review_reason"] = reason
    with _deps["write_lock"]:
        _deps["write_json"](wp, ir)
    return jsonify({"graph": key, "metric": name, "layer": layer, "status": status})


@bp_metrics.post("/api/metric/adjudicate")
def metric_adjudicate():
    """对图谱里的契约重跑执行核验(不新增候选);证据里保存的参照复用。"""
    body = request.json or {}
    key = str(body.get("graph") or "").strip()
    db = _deps["db_for_source"](body.get("source") or "demo")
    if not db: return jsonify({"error": "数据源不可用(外部库或不存在)"}), 400
    ir, wp, err = _deps["open_writable"](key)
    if err: return err
    out = metric_pipeline.readjudicate(db, ir)
    ir["metric_layers"] = out["metric_layers"]
    with _deps["write_lock"]:
        _deps["write_json"](wp, ir)
    return jsonify({"graph": key, **out["report"], "counts": metric_contract.counts(ir)})

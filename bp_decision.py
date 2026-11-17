#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工艺路线层与决策层的接口(DR-117)。规则见 ontology_decision.py。

GET  /api/ont/constraints/<key>            约束清单 + 审计
POST /api/ont/constraints/<key>            {"constraint": {...}} 新增或更新(结构非法 400)
POST /api/ont/constraints/<key>/delete     {"id": ...}
GET  /api/ont/routes/<key>                 工艺路线清单,每条带按方向核验的结果
POST /api/ont/routes/<key>                 {"route": {...}} 新增或更新,返回核验结果
POST /api/ont/routes/<key>/delete          {"id": ...}
GET  /api/ont/lighting/<key>               点亮矩阵
POST /api/ont/lighting/<key>               {"objects": {对象名: {status, source}}, "unlit": [...]}(合并写入)
GET  /api/ont/decision_audit/<key>         决策层审计
"""
from copy import deepcopy

from flask import Blueprint, jsonify, request

import cq_check
import ontology_decision as od

bp_decision = Blueprint("decision", __name__)
_deps: dict = {}


def configure_decision(load_graph, open_writable, write_json, write_lock, load_rules):
    _deps.update(load_graph=load_graph, open_writable=open_writable, write_json=write_json,
                 write_lock=write_lock, load_rules=load_rules)


def _read(key):
    ir = _deps["load_graph"](key)
    if ir is None:
        return None, (jsonify({"error": "非法图谱键"}), 400)
    if not ir:
        return None, (jsonify({"error": "图谱不存在"}), 404)
    return ir, None


def _write(key, mutate, after=None):
    """加锁读-改-写;mutate(ir) 返回响应体或抛 ValueError(→ 400)。after(body) 在写入后、返回前补充响应体。"""
    with _deps["write_lock"]:
        ir, wp, err = _deps["open_writable"](key)
        if err:
            return err
        ir = deepcopy(ir)
        try:
            body = mutate(ir)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        _deps["write_json"](wp, ir)
    if after:
        after(body)
    return jsonify(body)


def _upsert(ir, field, item, limit, what):
    items = [x for x in ir.get(field) or [] if x.get("id") != item["id"]] + [item]
    if len(items) > limit:
        raise ValueError(f"{what}过多(上限 {limit})")
    ir[field] = items
    return len(items)


def _delete(ir, field, item_id, what):
    items = ir.get(field) or []
    left = [x for x in items if x.get("id") != item_id]
    if len(left) == len(items):
        raise ValueError(f"{what}不存在")
    ir[field] = left
    return len(left)


def _audit(ir, key):
    return od.audit(ir, _deps["load_rules"](key))


# ── 约束 ────────────────────────────────────────────────────────────────
@bp_decision.get("/api/ont/constraints/<key>")
def constraints_get(key):
    ir, err = _read(key)
    if err:
        return err
    return jsonify({"graph": key, "constraints": ir.get("constraints") or [], "audit": _audit(ir, key)})


@bp_decision.post("/api/ont/constraints/<key>")
def constraints_save(key):
    c = (request.get_json(silent=True) or {}).get("constraint") or {}

    def mutate(ir):
        n = od.normalize_constraint(c, ir)
        return {"ok": True, "constraint": n, "total": _upsert(ir, "constraints", n, 300, "约束")}
    return _write(key, mutate)


@bp_decision.post("/api/ont/constraints/<key>/delete")
def constraints_delete(key):
    cid = str((request.get_json(silent=True) or {}).get("id") or "")
    return _write(key, lambda ir: {"ok": True, "total": _delete(ir, "constraints", cid, "约束")})


# ── 工艺路线 ────────────────────────────────────────────────────────────
def _route_check(ir, route):
    """路线是声明;它成不成立由关系按方向核验。只返回结论和逐段结果,不改路线本身。"""
    rep = cq_check.check_route(route["steps"], ir, route.get("roles"))
    return {k: rep.get(k) for k in ("verdict", "segments", "intact_segments", "total_segments", "reason", "fix")}


@bp_decision.get("/api/ont/routes/<key>")
def routes_get(key):
    ir, err = _read(key)
    if err:
        return err
    routes = [{**r, "check": _route_check(ir, r)} for r in ir.get("routes") or [] if isinstance(r, dict) and r.get("steps")]
    return jsonify({"graph": key, "routes": routes, "audit": _audit(ir, key)})


@bp_decision.post("/api/ont/routes/<key>")
def routes_save(key):
    r = (request.get_json(silent=True) or {}).get("route") or {}

    def mutate(ir):
        n = od.normalize_route(r, ir)
        return {"ok": True, "route": n, "total": _upsert(ir, "routes", n, 100, "工艺路线"), "check": _route_check(ir, n)}

    def recheck(body):
        # 写入用的是不含人工修改的图谱;核验结果按含人工修改的图谱重算,与之后查看时一致
        edited = _deps["load_graph"](key)
        if edited:
            body["check"] = _route_check(edited, body["route"])
    return _write(key, mutate, recheck)


@bp_decision.post("/api/ont/routes/<key>/delete")
def routes_delete(key):
    rid = str((request.get_json(silent=True) or {}).get("id") or "")
    return _write(key, lambda ir: {"ok": True, "total": _delete(ir, "routes", rid, "工艺路线")})


# ── 点亮矩阵 ────────────────────────────────────────────────────────────
@bp_decision.get("/api/ont/lighting/<key>")
def lighting_get(key):
    ir, err = _read(key)
    if err:
        return err
    return jsonify({"graph": key, "lighting": ir.get("lighting") or {}, "audit": _audit(ir, key)})


@bp_decision.post("/api/ont/lighting/<key>")
def lighting_save(key):
    body = request.get_json(silent=True) or {}

    def mutate(ir):
        names = {o.get("name") for o in ir.get("objects") or []}
        lt = ir.setdefault("lighting", {"objects": {}, "unlit": []})
        lt.setdefault("objects", {})
        objects: dict = body["objects"] if isinstance(body.get("objects"), dict) else {}
        for name, entry in objects.items():
            if name not in names:
                raise ValueError(f"对象 {name} 不在本体中")
            lt["objects"][name] = od.normalize_lighting(entry)
        if isinstance(body.get("unlit"), list):
            lt["unlit"] = [{"attribute": str(u.get("attribute") or "")[:80], "needs": str(u.get("needs") or "")[:80],
                            "why": str(u.get("why") or "")[:160]}
                           for u in body["unlit"] if isinstance(u, dict) and u.get("attribute")][:100]
        return {"ok": True, "objects": len(lt["objects"]), "unlit": len(lt.get("unlit") or [])}
    return _write(key, mutate)


# ── 审计 ────────────────────────────────────────────────────────────────
@bp_decision.get("/api/ont/decision_audit/<key>")
def decision_audit(key):
    ir, err = _read(key)
    if err:
        return err
    return jsonify({"graph": key, **_audit(ir, key)})

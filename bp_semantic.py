#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""概念画像与 OSI 风格导出 blueprint(DR-055)。路由(2):GET /api/ont/profile、GET /api/export/osi。"""
from flask import Blueprint, Response, jsonify, request

import concept_profile
import osi_export

bp_semantic = Blueprint("semantic", __name__)
_deps: dict = {}


def configure_semantic(load_graph):
    _deps["load_graph"] = load_graph


@bp_semantic.get("/api/ont/profile")
def ont_profile():
    """``q`` 给出则返回命中的画像(按分值);否则返回全部画像(不含检索文本)。"""
    key = (request.args.get("graph") or "demo").strip()
    ir = _deps["load_graph"](key)
    if ir is None: return jsonify({"error": "非法图谱键"}), 400
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    profiles = concept_profile.build(ir)
    q = (request.args.get("q") or "").strip()
    if q:
        hits = concept_profile.search(profiles, q, limit=int(request.args.get("limit") or 5))
        return jsonify({"graph": key, "q": q, "hits": hits,
                        "rendered": [concept_profile.render(h) for h in hits]})
    return jsonify({"graph": key, "count": len(profiles),
                    "profiles": [{k: v for k, v in p.items() if k != "text"} for p in profiles]})


@bp_semantic.get("/api/export/osi")
def export_osi():
    key = (request.args.get("graph") or "demo").strip()
    ir = _deps["load_graph"](key)
    if ir is None: return jsonify({"error": "非法图谱键"}), 400
    if not ir: return jsonify({"error": "图谱不存在"}), 404
    kind = (request.args.get("kind") or "semantic_model").strip()
    if kind not in ("semantic_model", "ontology"): return jsonify({"error": "kind 只能是 semantic_model 或 ontology"}), 400
    text = osi_export.to_yaml(ir, model_name=key, kind=kind)
    return Response(text, mimetype="text/yaml; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{key}.{kind}.ossie.yaml"'})

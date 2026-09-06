#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OSI 风格语义模型导出 —— DR-055。

Open Semantic Interchange 用厂商中立的 YAML 描述 Model / Datasets / Fields / Metrics /
Dimensions / Relationships,0.2 起加入 Concepts / Relationships(roles、multiplicity、verbalizes)/
Business rules / Ontology mappings。本模块把同一份 IR 投影成这一形状,与 OWL、关系表投影
并列为第三个出口,不构成第二份事实源。

边界:本导出按公开资料中的类结构组织,**未经 OSI 官方 validator 校验**,文档只写
「可导出 OSI 风格 YAML」;规范文本与 validator 到手后再对齐字段名。
不依赖 pyyaml:仓库刻意不引入 YAML 解析器,这里只需要一个确定性的发射器。
"""
from __future__ import annotations

import hashlib
import json

import metric_contract as MC
from ir_shape import rels as _rels

_STRONG = ("verified", "asserted")


def _yaml_scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def emit(value, indent=0):
    """确定性 YAML 发射:字典按插入序,列表逐项;所有字符串加引号,避免类型歧义。"""
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}"
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(emit(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {emit(v, 0).strip() if isinstance(v, (dict, list)) else _yaml_scalar(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return pad + "[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                body = emit(item, indent + 1)
                first, _, rest = body.partition("\n")
                lines.append(f"{pad}- {first.strip()}" + ("\n" + rest if rest else ""))
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return pad + _yaml_scalar(value)


def _key(o, idx):
    return o.get("id") or o.get("name") or o.get("cn") or f"_obj{idx}"


def _table(o):
    return o.get("table") or ((o.get("tables") or [None])[0]) or ""


def build(ir, model_name=None):
    """IR → OSI 风格文档(dict);``to_yaml`` 负责文本化。"""
    ir = ir or {}
    objs = ir.get("objects") or []
    by_key = {_key(o, i): o for i, o in enumerate(objs)}
    datasets = []
    for i, o in enumerate(objs):
        t = _table(o)
        if not t:
            continue
        datasets.append({
            "name": t, "object": _key(o, i), "label": o.get("cn") or o.get("name") or "",
            "primary_key": o.get("pk") or None,
            "fields": [{"name": a.get("col"), "label": a.get("cn") or "", "type": a.get("type") or ""}
                       for a in (o.get("attrs") or []) if a.get("col")],
        })
    rels, sk, tk = _rels(ir)
    relationships = []
    for r in rels:
        s, t = r.get(sk), r.get(tk)
        if s not in by_key or t not in by_key:
            continue
        ev = r.get("evidence") or {}
        ck, pk = ev.get("child_key"), ev.get("parent_key")
        mult = "many_to_one" if ev.get("parent_unique") else ("many_to_one" if (ck and pk) else "unknown")
        relationships.append({
            "name": f"{s}_{t}",
            "from": {"dataset": _table(by_key[s]) or None, "object": s, "field": ck},
            "to": {"dataset": _table(by_key[t]) or None, "object": t, "field": pk},
            "multiplicity": mult,
            "verbalizes": f"{by_key[s].get('cn') or s} {r.get('verb') or '关联'} {by_key[t].get('cn') or t}",
            "status": r.get("status") or "candidate",
            "evidence": {"overlap": ev.get("overlap"), "source": ev.get("source") or ev.get("sources")},
        })
    metrics = []
    for layer, arr in ((ir.get("metric_layers") or {}).items() if isinstance(ir.get("metric_layers"), dict) else []):
        for m in (arr or []):
            if not isinstance(m, dict):
                continue
            item = {"name": m.get("name"), "layer": layer, "dataset": m.get("table") or None,
                    "label": m.get("desc") or "", "unit": m.get("unit") or "",
                    "status": m.get("status") or ("candidate" if m.get("candidate", True) else "")}
            if MC.is_contract(m):
                item["expression"] = {"agg": m["measure"]["agg"], "field": m["measure"].get("col") or None}
                item["filters"] = m.get("filters") or []
                item["time"] = m.get("time") or {}
                item["dimensions"] = m.get("dimensions") or []
                try:
                    item["sql"] = MC.compile_sql(m)
                except ValueError:
                    pass
            elif m.get("value_col"):
                item["expression"] = {"agg": None, "field": m.get("value_col")}
            if m.get("formula"):
                item["derived_by"] = m["formula"]
            metrics.append(item)
    concepts = []
    for i, o in enumerate(objs):
        concepts.append({
            "name": _key(o, i), "label": o.get("cn") or "", "kind": o.get("kind") or "",
            "extends": o.get("bfo") or None, "definition": o.get("definition") or "",
            "maturity": o.get("maturity") or "", "mapping": {"dataset": _table(o) or None},
        })
    doc = {
        "osi_style_version": "0.1-preview",
        "note": "由 COSMO DataMind IR 确定性导出;字段按 OSI 公开资料组织,未经官方 validator 校验",
        "model": {"name": model_name or (ir.get("scenario") or {}).get("name") or "datamind",
                  "ir_digest": hashlib.sha256(json.dumps(ir, sort_keys=True, ensure_ascii=False,
                                                          default=str).encode()).hexdigest()[:16]},
        "datasets": datasets,
        "relationships": relationships,
        "metrics": metrics,
        "concepts": concepts,
    }
    return doc


def to_yaml(ir, model_name=None):
    return emit(build(ir, model_name)) + "\n"

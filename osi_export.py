#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apache Ossie(原 Open Semantic Interchange)格式导出 —— DR-055。

按 ``ontology/standards/ossie/`` 里快照的两份官方 schema(版本常量 ``0.2.0.dev0``)生成:

- 核心语义模型(``semantic_model``):datasets / fields / relationships / metrics。
  schema 对每个对象都是 ``additionalProperties: false``,状态、证据、来源这类本仓信息
  只能放进 ``custom_extensions``(vendor_name="datamind",data 为 JSON 字符串)。
- 本体(``ontology``):concepts(EntityType,``extends`` 指向 BFO/IOF 上层类别概念)与
  relationships(roles / multiplicity / verbalizes)。``ontology_mappings`` 暂不导出——
  其 schema 以远程 $ref 引用核心 schema,离线校验会触发网络解析。

两条不可退让的规则:
1. 只有 verified/asserted 且键齐全的关系才成为 ``relationships``(消费方会把它当 JOIN 路径);
   candidate 关系整体放进模型级 ``custom_extensions``,不提升、不丢失。
2. 只有有契约(聚合已声明)的指标才成为 ``metrics``;旧形状指标同样放进扩展。

不依赖 pyyaml:自带确定性 YAML 发射器;所有字符串加引号,None/空值一律省略键
(schema 的类型约束不接受 null)。
"""
from __future__ import annotations

import hashlib
import json

import metric_contract as MC
from ir_shape import rels as _rels

SPEC_VERSION = "0.2.0.dev0"
VENDOR = "datamind"
_STRONG = ("verified", "asserted")
_DATATYPE = {
    "INT": "Integer", "INTEGER": "Integer", "BIGINT": "Integer", "SMALLINT": "Integer", "TINYINT": "Integer",
    "REAL": "Float", "FLOAT": "Float", "DOUBLE": "Float",
    "NUMERIC": "Decimal", "DECIMAL": "Decimal", "NUMBER": "Decimal", "MONEY": "Decimal",
    "TEXT": "String", "VARCHAR": "String", "CHAR": "String", "STRING": "String", "CLOB": "String",
    "BOOLEAN": "Boolean", "BOOL": "Boolean",
    "DATE": "Date", "TIME": "Time", "DATETIME": "DateTime", "TIMESTAMP": "DateTime",
}


# ── YAML 发射(确定性;供官方 validator 用 pyyaml 读回)──
def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def emit(value, indent=0):
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}"
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(emit(v, indent + 1))
            elif isinstance(v, dict):
                lines.append(f"{pad}{k}: {{}}")
            elif isinstance(v, list):
                lines.append(f"{pad}{k}: []")
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
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
                lines.append(f"{pad}- {_scalar(item)}")
        return "\n".join(lines)
    return pad + _scalar(value)


# ── 辅助 ──
def _clean(d):
    """递归去掉 None / 空字符串 / 空容器的键:schema 不接受 null,空列表也无意义。"""
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            v = _clean(v)
            if v is None or v == "" or v == [] or v == {}:
                continue
            out[k] = v
        return out
    if isinstance(d, list):
        return [x for x in (_clean(v) for v in d) if x is not None and x != "" and x != {} and x != []]
    return d


def _ext(payload):
    return [{"vendor_name": VENDOR, "data": json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)}]


def _expr(text):
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": text}]}


def _key(o, idx):
    return o.get("id") or o.get("name") or o.get("cn") or f"_obj{idx}"


def _table(o):
    return o.get("table") or ((o.get("tables") or [None])[0]) or ""


def _datatype(t):
    t = str(t or "").upper()
    for k, v in _DATATYPE.items():
        if k in t:
            return v
    return None


def _unique(name, seen):
    base = name or "unnamed"
    out, i = base, 2
    while out in seen:
        out = f"{base}_{i}"; i += 1
    seen.add(out)
    return out


def _sql_lit(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def _cond(table, f):
    c, op, v = f"{table}.{f['col']}", f["op"], f.get("value")
    if op == "is_null":
        return f"{c} IS NULL"
    if op == "not_null":
        return f"{c} IS NOT NULL"
    if op in ("in", "not_in"):
        return f"{c} {'IN' if op == 'in' else 'NOT IN'} ({', '.join(_sql_lit(x) for x in v)})"
    return f"{c} {op} {_sql_lit(v)}"


def _metric_expression(m):
    """契约 → 单条 ANSI SQL 聚合表达式(过滤条件折进 CASE WHEN,不依赖 WHERE 子句)。"""
    table, ms = m["table"], m["measure"]
    agg, col = ms["agg"], ms.get("col")
    ref = f"{table}.{col}" if col else None
    conds = " AND ".join(_cond(table, f) for f in (m.get("filters") or []))
    if agg == "count":
        inner = ref or "1"
        return f"COUNT(CASE WHEN {conds} THEN {inner} END)" if conds else (f"COUNT({ref})" if ref else "COUNT(*)")
    if agg == "count_distinct":
        return f"COUNT(DISTINCT CASE WHEN {conds} THEN {ref} END)" if conds else f"COUNT(DISTINCT {ref})"
    fn = agg.upper()
    return f"{fn}(CASE WHEN {conds} THEN {ref} END)" if conds else f"{fn}({ref})"


# ── 核心语义模型 ──
def semantic_model(ir, model_name=None):
    ir = ir or {}
    objs = ir.get("objects") or []
    by_key = {_key(o, i): o for i, o in enumerate(objs)}
    seen_ds, datasets, ds_of_key = set(), [], {}
    for i, o in enumerate(objs):
        t = _table(o)
        if not t:
            continue
        name = _unique(t, seen_ds)
        ds_of_key[_key(o, i)] = name
        seen_f, fields = set(), []
        for a in (o.get("attrs") or []):
            col = a.get("col")
            if not col:
                continue
            dt = _datatype(a.get("type"))
            fields.append(_clean({
                "name": _unique(col, seen_f),
                "expression": _expr(col),
                "label": a.get("cn") or None,
                "datatype": dt,
                "dimension": {"is_time": True} if dt in ("Date", "DateTime", "Time") else None,
            }))
        datasets.append(_clean({
            "name": name, "source": t,
            "primary_key": [o["pk"]] if o.get("pk") else None,
            "description": o.get("definition") or o.get("cn") or None,
            "ai_context": {"synonyms": [x for x in [o.get("cn")] + list(o.get("aliases") or []) if x]},
            "fields": fields,
            "custom_extensions": _ext({"object": _key(o, i), "kind": o.get("kind"), "bfo": o.get("bfo"),
                                       "maturity": o.get("maturity"), "candidate": bool(o.get("candidate"))}),
        }))
    rels, sk, tk = _rels(ir)
    seen_r, relationships, weak = set(), [], []
    for r in rels:
        s, t = r.get(sk), r.get(tk)
        if s not in by_key or t not in by_key:
            continue
        ev = r.get("evidence") or {}
        ck, pk = ev.get("child_key"), ev.get("parent_key")
        rec = {"source": s, "target": t, "verb": r.get("verb"), "status": r.get("status") or "candidate",
               "child_key": ck, "parent_key": pk, "overlap": ev.get("overlap")}
        if r.get("status") in _STRONG and ck and pk and s in ds_of_key and t in ds_of_key:
            relationships.append(_clean({
                "name": _unique(f"{ds_of_key[s]}_to_{ds_of_key[t]}", seen_r),
                "from": ds_of_key[s], "to": ds_of_key[t],
                "from_columns": [c.strip() for c in str(ck).split(",")],
                "to_columns": [c.strip() for c in str(pk).split(",")],
                "ai_context": {"instructions": f"{by_key[s].get('cn') or s} {r.get('verb') or '关联'} {by_key[t].get('cn') or t}"},
                "custom_extensions": _ext(rec),
            }))
        else:
            weak.append(rec)
    seen_m, metrics, unbound = set(), [], []
    for layer, arr in ((ir.get("metric_layers") or {}).items() if isinstance(ir.get("metric_layers"), dict) else []):
        for m in (arr or []):
            if not isinstance(m, dict):
                continue
            status = m.get("status") or ("candidate" if m.get("candidate", True) else "")
            if not (MC.is_contract(m) and m.get("table")):
                unbound.append({"name": m.get("name"), "layer": layer, "status": status,
                                "table": m.get("table"), "value_col": m.get("value_col"),
                                "desc": m.get("desc"), "formula": m.get("formula")})
                continue
            ev = m.get("evidence") or {}
            coltype = None
            for o in objs:
                if str(_table(o)).lower() == str(m["table"]).lower():
                    for a in (o.get("attrs") or []):
                        if a.get("col") == m["measure"].get("col"):
                            coltype = _datatype(a.get("type"))
            metrics.append(_clean({
                "name": _unique(m.get("name"), seen_m),
                "expression": _expr(_metric_expression(m)),
                "description": m.get("desc") or MC.describe(m),
                "datatype": "Integer" if m["measure"]["agg"] in ("count", "count_distinct") else (coltype or "Decimal"),
                "ai_context": {"synonyms": list(m.get("synonyms") or []) or None,
                               "instructions": f"状态 {status};" + (f"单位 {m['unit']};" if m.get("unit") else "")
                                               + "按时间列 " + (m.get("time") or {}).get("col", "") if (m.get("time") or {}).get("col") else f"状态 {status}"},
                "custom_extensions": _ext({"status": status, "layer": layer, "entity": m.get("entity"),
                                           "time": m.get("time"), "filters": m.get("filters"),
                                           "dimensions": m.get("dimensions"), "unit": m.get("unit"),
                                           "compiled_sql": ev.get("compiled_sql"),
                                           "evidence": {k: ev.get(k) for k in ("executed", "match", "reference", "checked_at")},
                                           "certified_by": m.get("certified_by"), "provenance": m.get("provenance")}),
            }))
    model = _clean({
        "name": model_name or (ir.get("scenario") or {}).get("name") or "datamind",
        "description": (ir.get("scenario") or {}).get("description") or None,
        "ai_context": {"instructions": "关系只包含 verified/asserted 且键齐全者;指标只包含有契约者。"
                                       "状态与证据见各元素 custom_extensions(vendor datamind)。"},
        "datasets": datasets,
        "relationships": relationships,
        "metrics": metrics,
        "custom_extensions": _ext({
            "ir_digest": hashlib.sha256(json.dumps(ir, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16],
            "candidate_relations": weak, "unbound_metrics": unbound}),
    })
    return {"version": SPEC_VERSION, "semantic_model": [model]}


# ── 本体 ──
def ontology(ir, name=None):
    ir = ir or {}
    objs = ir.get("objects") or []
    by_key = {_key(o, i): o for i, o in enumerate(objs)}
    uppers = sorted({str(o.get("bfo")) for o in objs if o.get("bfo")})
    comps = [{"concept": u, "type": "EntityType", "description": f"上层类别(BFO 2020 / IOF Core):{u}"} for u in uppers]
    rels, sk, tk = _rels(ir)
    for i, o in enumerate(objs):
        k = _key(o, i)
        seen_n, relationships = set(), []
        for r in rels:
            if r.get(sk) != k or r.get(tk) not in by_key or r.get("status") not in _STRONG:
                continue
            t, verb = r.get(tk), r.get("verb") or "关联"
            ev = r.get("evidence") or {}
            relationships.append(_clean({
                "name": _unique(f"{verb}_{t}", seen_n),
                "roles": [{"concept": t}],
                "multiplicity": "ManyToOne" if ev.get("parent_unique") else None,
                "verbalizes": [f"{{{k}}} {verb} {{{t}}}"],
                "description": r.get("note") or None,
            }))
        comps.append(_clean({
            "concept": k, "type": "EntityType",
            "description": o.get("definition") or o.get("cn") or None,
            "extends": [o["bfo"]] if o.get("bfo") else None,
            "relationships": relationships,
        }))
    return _clean({"version": SPEC_VERSION, "name": name or (ir.get("scenario") or {}).get("name") or "datamind",
                   "description": "由 COSMO DataMind 本体 IR 导出;仅包含 verified/asserted 关系", "ontology": comps})


def build(ir, model_name=None):
    """兼容旧调用:返回核心语义模型文档。"""
    return semantic_model(ir, model_name)


def to_yaml(ir, model_name=None, kind="semantic_model"):
    doc = ontology(ir, model_name) if kind == "ontology" else semantic_model(ir, model_name)
    return emit(doc) + "\n"


def schema_paths():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return {"semantic_model": os.path.join(here, "ontology", "standards", "ossie", "ossie-schema.json"),
            "ontology": os.path.join(here, "ontology", "standards", "ossie", "ontology-schema.json")}


def validate(doc, kind="semantic_model"):
    """按快照的官方 schema 校验(需 jsonschema;缺失时返回 None 表示未校验,不假装通过)。"""
    try:
        import jsonschema
    except ImportError:
        return None
    with open(schema_paths()[kind], encoding="utf-8") as f:
        schema = json.load(f)
    return [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]

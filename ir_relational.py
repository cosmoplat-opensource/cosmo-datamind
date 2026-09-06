#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IR → 关系型语义层投影 —— DR-049。

**为什么要这一层。** 现有导出是 OWL2 / SHACL / SKOS / JSON-LD(`_ir_to_turtle`),
面向互操作与标准合规;但下游业务系统(BI、指标平台、SQL 生成服务)要消费语义时,
接一个 RDF 图并写 SPARQL 的成本远高于查几张表。兄弟团队的语义层实践即直接把
概念/指标/映射/规则落在关系库里供 Agent 查询——这条工程判断是对的。

故本模块提供**同源双形态**:同一份 IR,既可导出 OWL(标准出口),也可投影成关系表
(消费出口)。两者从同一 IR 生成,不存在第二份事实源;投影是**只读派生物**,
不接受回写——回写一律走 IR 的既有编辑路径(`/api/ont/apply`),避免双写漂移。

**与 OWL 导出的对应**:
  ont_object      ← owl:Class(含 BFO 上层归类、IOF-AV 注释:定义/正例/反例/成熟度)
  ont_attribute   ← owl:DatatypeProperty(字段级)
  ont_relation    ← owl:ObjectProperty(含 founded_relation 接地与时间指标)
  ont_metric      ← skos:Concept 指标(分层:atomic/derived/composite;DR-054 起带契约列与状态,
                    v_consumable_metric 只出 verified/certified 且可编译的指标)
  ont_evidence    ← 关系的证据明细(裁决口径:子键/父键/重叠率/来源)——OWL 侧以注释承载,
                    关系型侧单列一表,便于按证据强度过滤

**设计约束**(与本仓一贯规范一致):
- 纯函数、确定性:同一 IR 反复投影结果逐字节一致(便于 diff 与回归);
- 不调 LLM、不访问业务库:只吃 IR;
- 状态与证据**原样带出**,不在投影层做任何"提升"(candidate 不会变成 verified)。
"""
import json
import sqlite3

SCHEMA = """
CREATE TABLE ont_object (
  obj_id        TEXT PRIMARY KEY,   -- IR 对象键
  name          TEXT NOT NULL,      -- 英文/技术名
  cn            TEXT,               -- 中文业务名
  kind          TEXT,               -- object/event/asset/role
  bfo           TEXT,               -- BFO 上层范畴
  bound_table   TEXT,               -- 绑定的物理表
  pk            TEXT,
  definition    TEXT,               -- 属加种差定义
  is_primitive  INTEGER NOT NULL DEFAULT 0,
  example       TEXT,
  counter_example TEXT,
  maturity      TEXT,               -- Provisional/Released...
  is_candidate  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE ont_attribute (
  obj_id     TEXT NOT NULL,
  col        TEXT NOT NULL,
  cn         TEXT,
  data_type  TEXT,
  sources    TEXT,                  -- 证据来源(逗号分隔)
  PRIMARY KEY (obj_id, col)
);
CREATE TABLE ont_relation (
  rel_id        INTEGER PRIMARY KEY,
  source_obj    TEXT NOT NULL,
  target_obj    TEXT NOT NULL,
  verb          TEXT,               -- 业务动词(中文)
  founded_relation TEXT,            -- 接地到的 IOF/BFO 有根据关系
  temporal      TEXT,               -- atAllTimes/atSomeTime
  status        TEXT NOT NULL,      -- verified/asserted/inferred/candidate/gap
  is_candidate  INTEGER NOT NULL DEFAULT 0,
  note          TEXT
);
CREATE TABLE ont_evidence (
  rel_id     INTEGER NOT NULL,
  child_key  TEXT,
  parent_key TEXT,
  overlap    REAL,                  -- 取值重叠率(%)
  sources    TEXT,
  PRIMARY KEY (rel_id)
);
CREATE TABLE ont_metric (
  metric_id  TEXT PRIMARY KEY,
  layer      TEXT NOT NULL,         -- atomic/derived/composite
  name       TEXT NOT NULL,
  type       TEXT,
  descr      TEXT,
  bound_table TEXT,
  value_col  TEXT,
  unit       TEXT,
  is_candidate INTEGER NOT NULL DEFAULT 0,
  status     TEXT NOT NULL DEFAULT 'candidate',  -- candidate/verified/certified/deprecated(DR-054)
  entity     TEXT,                  -- 绑定的本体对象
  agg        TEXT,                  -- sum/count/count_distinct/avg/min/max;旧形状为空
  filters    TEXT,                  -- JSON 数组
  time_col   TEXT,
  grains     TEXT,                  -- 逗号分隔
  dimensions TEXT,                  -- 逗号分隔(绑定表自身列)
  compiled_sql TEXT,                -- 契约编译出的标量 SQL(只读派生,不回写)
  certified_by TEXT
);
CREATE VIEW v_consumable_metric AS
  SELECT metric_id, layer, name, bound_table, agg, compiled_sql, status
  FROM ont_metric WHERE status IN ('verified','certified') AND compiled_sql IS NOT NULL;
CREATE VIEW v_verified_join AS
  SELECT r.source_obj, so.bound_table AS source_table, e.child_key,
         r.target_obj, tv.bound_table AS target_table, e.parent_key,
         e.overlap, r.verb, r.founded_relation
  FROM ont_relation r
  JOIN ont_evidence e ON e.rel_id = r.rel_id
  LEFT JOIN ont_object so ON so.obj_id = r.source_obj
  LEFT JOIN ont_object tv ON tv.obj_id = r.target_obj
  WHERE r.status = 'verified' AND e.child_key IS NOT NULL AND e.parent_key IS NOT NULL;
"""


def _s(v):
    """None → None;其余转字符串(sqlite 不接受 dict/list)。"""
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return ",".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def project(ir, con):
    """把 IR 投影进已打开的 sqlite 连接。返回各表行数。确定性:按 IR 原序写入。"""
    con.executescript(SCHEMA)
    objs = ir.get("objects") or []
    n_attr = 0
    for o in objs:
        oid = _s(o.get("id") or o.get("name"))
        con.execute(
            "INSERT OR REPLACE INTO ont_object VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, _s(o.get("name")), _s(o.get("cn")), _s(o.get("kind")), _s(o.get("bfo")),
             _s(o.get("table")), _s(o.get("pk")), _s(o.get("definition")),
             1 if o.get("isPrimitive") else 0, _s(o.get("example")), _s(o.get("counterExample")),
             _s(o.get("maturity")), 1 if o.get("candidate") else 0))
        for a in (o.get("attrs") or []):
            con.execute("INSERT OR REPLACE INTO ont_attribute VALUES (?,?,?,?,?)",
                        (oid, _s(a.get("col")), _s(a.get("cn")), _s(a.get("type")),
                         _s(a.get("sources"))))
            n_attr += 1

    links = ir.get("links") or []
    n_ev = 0
    for i, l in enumerate(links, 1):
        con.execute("INSERT OR REPLACE INTO ont_relation VALUES (?,?,?,?,?,?,?,?,?)",
                    (i, _s(l.get("source")), _s(l.get("target")), _s(l.get("verb")),
                     _s(l.get("founded_relation")), _s(l.get("temporal")),
                     _s(l.get("status")) or "candidate",
                     1 if l.get("candidate") else 0, _s(l.get("note"))))
        ev = l.get("evidence") or {}
        if ev:
            ovv = ev.get("overlap")
            con.execute("INSERT OR REPLACE INTO ont_evidence VALUES (?,?,?,?,?)",
                        (i, _s(ev.get("child_key")), _s(ev.get("parent_key")),
                         float(ovv) if isinstance(ovv, (int, float)) else None,
                         _s(ev.get("sources"))))
            n_ev += 1

    n_metric = 0
    for layer, rows in (ir.get("metric_layers") or {}).items():
        for m in (rows or []):
            ms, tm = (m.get("measure") or {}), (m.get("time") or {})
            compiled = ""
            if ms.get("agg") and m.get("table"):
                try:
                    import metric_contract
                    compiled = metric_contract.compile_sql(m)
                except (ValueError, ImportError):
                    compiled = ""
            con.execute("INSERT OR REPLACE INTO ont_metric VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (_s(m.get("id")), _s(layer), _s(m.get("name")), _s(m.get("type")),
                         _s(m.get("desc")), _s(m.get("table")), _s(m.get("value_col")),
                         _s(m.get("unit")), 1 if m.get("candidate") else 0,
                         _s(m.get("status")) or "candidate", _s(m.get("entity")), _s(ms.get("agg")),
                         _s(m.get("filters")) if m.get("filters") else None, _s(tm.get("col")),
                         _s(tm.get("grain")), _s(m.get("dimensions")), compiled or None,
                         _s(m.get("certified_by"))))
            n_metric += 1
    con.commit()
    return {"ont_object": len(objs), "ont_attribute": n_attr,
            "ont_relation": len(links), "ont_evidence": n_ev, "ont_metric": n_metric}


def project_to_file(ir, path):
    """投影到 sqlite 文件(存在则覆盖)。"""
    import os
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    try:
        return project(ir, con)
    finally:
        con.close()


def project_to_sql(ir):
    """投影为纯 SQL 文本(DDL + INSERT),供导入 MySQL/PG 等非 sqlite 下游。"""
    con = sqlite3.connect(":memory:")
    try:
        project(ir, con)
        return "\n".join(con.iterdump())
    finally:
        con.close()


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "workdir/demo_ir.json"
    out = sys.argv[2] if len(sys.argv) > 2 else "workdir/semantic_layer.db"
    ir = json.load(open(src, encoding="utf-8"))
    print(json.dumps(project_to_file(ir, out), ensure_ascii=False), "→", out)

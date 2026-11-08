#!/usr/bin/env python3
"""把 DataMind 构建出的本体(built_*.json)转换为 EvoOntology 的内容层五类记录(条件 C)。

转换只做格式映射,不补充任何知识:
- 对象(object/event/ice;不含动作节点)→ Term;名称用英文对象名,中文名与表名作别名,定义沿用构建结果;
- 对象的表与各列 → Mapping;
- 只转换 status=verified 的关系 → Relation(composition,父为整体、子为部分),connection_condition 写明连接列;
- 关系的数据裁决结果(取值重叠率、父键唯一、行数)→ Evidence;
- 不同取值不超过 10 个的文本/整数列的实际取值 → Constraint(enum_semantics),与 DataMind 问数上下文给出的取值一致。

用法: python3 dm2evo.py <graphs.json> <out_root>
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

EXP = Path(__file__).resolve().parent
sys.path.insert(0, str(EXP / "EvoOntology"))
from evoontology import SemanticStore  # noqa: E402
from evoontology.validate import validate  # noqa: E402

DBDIR = Path(os.environ["DM_DBDIR"]) if os.environ.get("DM_DBDIR") else EXP / "EvoOntology" / "benchmarks" / "bird" / "data" / "mini_dev_data" / "dev_databases"
WORK = EXP / "runs" / "datamind" / "workdir"
KEYISH = re.compile(r"(^id$|_id$|id$|_no$|_uuid$|_time$|_date$|^date$)", re.I)
TODAY = date.today().isoformat()
COMPACT = os.environ.get("DM2EVO_PROFILE") == "compact"
USE_CONCEPTS = os.environ.get("DM2EVO_CONCEPTS", "1") == "1"   # 消融用:是否导出概念层
USE_CAVEATS = os.environ.get("DM2EVO_CAVEATS", "1") == "1"     # 消融用:是否导出使用提醒
READABLE = os.environ.get("DM2EVO_READABLE") == "1"            # 术语编号用可读名称;取值目录名按词拆开   # 精简导出:概念至多 40 个、使用提醒至多 8 条,有概念时不再逐条导出取值目录
# 协议 v2:尽量无损导出 —— 候选关系、指标契约、定义/反例/成熟度/来源;并写 loss.json 登记丢失项。
# 默认关闭,旧产物(dm_semantic_v3、C13)可原样重现。
FULL = os.environ.get("DM2EVO_FULL") == "1"
DEDUP = os.environ.get("DM2EVO_DEDUP") == "1"      # DR-093:同名术语合并映射、概念已覆盖的列不另立维度术语、中文定义前加英文名
DM_MAIN = EXP.parent / "cosmo-datamind"


def _metric_contract():
    """DataMind 主仓的指标契约模块(DR-054);只在 FULL 模式需要。"""
    if str(DM_MAIN) not in sys.path:
        sys.path.insert(0, str(DM_MAIN))
    import metric_contract  # noqa: E402
    return metric_contract


def full_definition(o):
    """FULL 模式的术语定义:定义在前,再附反例、成熟度;来源进 scope。"""
    parts = [str(o.get("definition") or o.get("cn") or o.get("table") or "").strip()]
    if o.get("counterExample"):
        parts.append(f"Counterexample: {str(o['counterExample']).strip()}")
    if o.get("maturity"):
        parts.append(f"Maturity: {o['maturity']}")
    return " ".join(p for p in parts if p)


def provenance_text(o):
    p = o.get("provenance") or {}
    if not isinstance(p, dict):
        return ""
    bits = []
    if p.get("directSource"):
        bits.append(f"direct source {p['directSource']}")
    if p.get("adaptedFrom"):
        bits.append("adapted from " + ", ".join(map(str, p["adaptedFrom"])))
    if p.get("excerptedFrom"):
        bits.append(f"excerpted from {p['excerptedFrom']}")
    return "; ".join(bits)


def split_words(s):
    """驼峰与下划线拆成小写词:NetIncomeLoss → net income loss。"""
    return " ".join(w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+", str(s)))


def slug(s):
    return re.sub(r"[^0-9a-zA-Z_]+", "_", str(s)).strip("_").lower() or "x"


def low_card_values(con, table, col, cap=10):
    try:
        vals = [r[0] for r in con.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT {cap + 1}')]
    except sqlite3.Error:
        return None
    ok = 0 < len(vals) <= cap and all(isinstance(v, (str, int)) and not isinstance(v, bool) and 0 < len(str(v).strip()) <= 24 for v in vals)
    return sorted(map(str, vals)) if ok else None


def convert(db, graph_key, out_root):
    ir = json.loads((WORK / f"{graph_key}.json").read_text())
    con = sqlite3.connect(f"file:{DBDIR / db / (db + '.sqlite')}?mode=ro", uri=True)
    terms, mappings, relations, constraints, evidence = [], [], [], [], []
    tid = {}
    family = {}                                        # 精简导出:列集合相同的表归为一族,只导出代表表
    if COMPACT:
        groups = {}
        for o in ir["objects"]:
            if o.get("table") and o.get("kind") != "action":
                key = tuple(sorted(a["col"].lower() for a in o.get("attrs") or [] if a.get("col")))
                groups.setdefault(key, []).append(o["table"])
        family = {ts[0]: ts for ts in groups.values() if len(ts) > 1}
        members = {t for ts in family.values() for t in ts[1:]}
    concept_cols = {(c.get("table"), c.get("column")) for c in ir.get("concepts") or [] if c.get("column")} if DEDUP else set()
    dim_ids = {}                                       # DEDUP:同一共享代码出现在多张表时合并为一个维度术语(按名称+列代码)
    extra_alias = {}                                   # DEDUP:概念已覆盖的列,其候选名并入概念别名
    for o in ir["objects"]:
        table = o.get("table")
        if not table or o.get("kind") == "action":
            continue
        if COMPACT and table in members:
            continue
        t = f"term_{slug(o['name'])}"
        tid[o["name"]] = (t, table)
        cols = [a["col"] for a in o.get("attrs") or [] if a.get("col")]
        ev = f"ev_schema_{slug(table)}"
        evidence.append({"id": ev, "source": f"{db}.sqlite", "query": f"PRAGMA table_info({table})",
                         "result": f"Table {table}({', '.join(cols)})", "validation_method": "schema inspection",
                         "timestamp": TODAY})
        lab = o.get("label_candidate") or {}            # DR-093 由取值推出的候选名(附依据)优先于代码名
        tname = lab.get("en") or o["name"]
        aliases = [x for x in dict.fromkeys([lab.get("cn"), o.get("cn"), table, o["name"], *(o.get("aliases") or [])]) if x and x != tname]
        fam = family.get(table) or []
        scope = f"Records of table {table}" + (f"; the same columns appear in {len(fam)} tables: {', '.join(fam)}" if fam else "")
        if FULL and provenance_text(o):
            scope += f"; provenance: {provenance_text(o)}"
        if lab:
            scope += f"; name inferred from the data (candidate): {lab.get('basis', '')}"
        terms.append({"id": t, "name": tname, "type": "concept" if o.get("kind") == "event" else "entity",
                      "definition": full_definition(o) if FULL else (o.get("definition") or o.get("cn") or table),
                      "scope": scope, "aliases": aliases, "evidence": [ev]})
        mappings.append({"id": f"map_{slug(o['name'])}", "term_id": t, "database_source": db, "table": table, "column": "",
                         "semantic_filter": "", "aggregation_semantics": "", "grain": f"one row per {table} record",
                         "validation": f"table exists with {len(cols)} columns"})
        for a in o.get("attrs") or []:
            col = a.get("col")
            if not col:
                continue
            alab = a.get("label_candidate") or {}
            at = t
            if alab.get("en") and DEDUP and (table, col) in concept_cols:
                extra_alias.setdefault((table, col), []).append(alab["en"])
            elif alab.get("en") and DEDUP and (alab["en"].lower(), col.lower()) in dim_ids:
                at = dim_ids[(alab["en"].lower(), col.lower())]   # 同一共享代码(同名同列)才合并,不同列同名不合并
            elif alab.get("en"):                        # DR-093 有候选名的列单独成为维度术语,映射挂在该术语上
                at = f"term_{slug(table)}__{slug(col)}"
                dim_ids[(alab["en"].lower(), col.lower())] = at
                terms.append({"id": at, "name": f"{alab['en']}", "type": "dimension",
                              "definition": f"{alab['en']} of {tname} (column {table}.{col}); name inferred from the data (candidate): {alab.get('basis', '')}",
                              "scope": f"Column {col} of table {table}", "aliases": [x for x in dict.fromkeys([f"{tname} {alab['en']}", col]) if x],
                              "evidence": [ev]})
            mappings.append({"id": f"map_{slug(o['name'])}__{slug(col)}", "term_id": at, "database_source": db, "table": table,
                             "column": col, "semantic_filter": "", "aggregation_semantics": "",
                             "grain": f"one value per {table} record",
                             "validation": f"column {table}.{col} ({a.get('type') or 'unknown type'})"
                                           + (f"; meaning: {a['cn']}" if a.get("cn") else "")
                                           + (f"; documentation: {a['doc']}" if a.get("doc") else "")})
            vals = None if KEYISH.search(col) else low_card_values(con, table, col)
            if vals:
                cev = f"ev_values_{slug(table)}__{slug(col)}"
                evidence.append({"id": cev, "source": f"{db}.sqlite", "query": f"SELECT DISTINCT {col} FROM {table}",
                                 "result": " / ".join(vals), "validation_method": "distinct value inspection",
                                 "timestamp": TODAY})
                constraints.append({"id": f"c_values_{slug(table)}__{slug(col)}", "target": t,
                                    "constraint_type": "enum_semantics",
                                    "trigger_keywords": list(dict.fromkeys([col, col.lower(), table, *vals]))[:16],
                                    "severity": "warning", "scope": f"Filters on {table}.{col}", "confidence": "high",
                                    "description": f"{table}.{col} only takes these stored values: {' / '.join(vals)}. "
                                                   "Filter with the stored spelling.",
                                    "evidence": [cev]})
        cat = o.get("catalog") or {}
        pe = (o.get("catalog") or {}).get("per_entity")
        if pe and READABLE:                             # 代码目录探查:按数据统计,告诉智能体每个实体自己报了多少种代码
            c0 = o["catalog"]
            terms.append({"id": f"term_{slug(table)}_code_catalog", "name": f"{split_words(c0['name_col']).title()} catalog of each entity",
                          "type": "concept",
                          "definition": (f"In {table}, each {c0['entity_col']} reports its own set of {c0['name_col']} codes: "
                                         f"median {pe['median']} distinct codes per entity (range {pe['min']}-{pe['max']}), "
                                         f"{c0['distinct']} codes in total. The concepts listed here cover only common ones; to find what a specific "
                                         f"entity reports on a topic, run SELECT DISTINCT {c0['name_col']} FROM {table} WHERE {c0['entity_col']} = <id> "
                                         f"and filter by keywords."),
                          "scope": f"codes in {table}.{c0['name_col']}", "aliases": ["company specific tags", "find available codes", "disclosure catalog"],
                          "evidence": []})
        if COMPACT and ir.get("concepts"):
            cat = {}
        for e in (cat.get("entries") or [])[:int(os.environ.get("DM2EVO_CATALOG_TOP") or 1000)]:              # DR-087 取值目录:名称列的常见取值各成一个指标概念
            v = e["value"]
            mid = f"{slug(table)}__{slug(v)}"[:120]
            words = " ".join(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+", v))
            cev = f"ev_catalog_{mid}"
            evidence.append({"id": cev, "source": f"{db}.sqlite",
                             "query": f"SELECT COUNT(*), COUNT(DISTINCT {cat.get('entity_col') or '*'}) FROM {table} WHERE {cat['name_col']} = '{v}'",
                             "result": f"{e['rows']} rows; {e['entities']} entities; most common unit {e.get('unit') or 'n/a'}",
                             "validation_method": "value catalog of a name/value table", "timestamp": TODAY})
            if READABLE:
                mid = slug(split_words(v).replace(" ", "_"))[:100]
            terms.append({"id": f"term_metric_{mid}", "name": (split_words(v).title() if READABLE else v), "type": "metric",
                          "definition": f"Metric '{v}' ({words}) stored as rows of {table} with {cat['name_col']} = '{v}'; "
                                        f"the number is in {cat['value_col']}"
                                        + (f", unit in {cat['unit_col']} (usually {e['unit']})" if cat.get("unit_col") else "") + ".",
                          "scope": f"Rows of {table} where {cat['name_col']} = '{v}'",
                          "aliases": [x for x in dict.fromkeys([words, words.lower()]) if x and x != v], "evidence": [cev]})
            mappings.append({"id": f"map_metric_{mid}", "term_id": f"term_metric_{mid}", "database_source": db,
                             "table": table, "column": cat["value_col"],
                             "semantic_filter": f"{cat['name_col']} = '{v}'",
                             "aggregation_semantics": "",
                             "grain": f"one row per {table} record", "validation": f"{e['rows']} rows, {e['entities']} entities"})
    concept_ids = {}
    concepts_in = (ir.get("concepts") or []) if USE_CONCEPTS else []
    caveats_in = (ir.get("caveats") or []) if USE_CAVEATS else []
    if os.environ.get("DM2EVO_CONCEPT_TOP"):            # 按覆盖实体数(其次命中行数)取前 N 个概念
        concepts_in = sorted(concepts_in, key=lambda c: (-int((c.get("evidence") or {}).get("entities") or 0),
                                                         -int((c.get("evidence") or {}).get("rows") or 0)))[:int(os.environ["DM2EVO_CONCEPT_TOP"])]
    if COMPACT:                                          # 按命中行数取前 40 个概念;使用提醒按首词去重后取前 8 条
        concepts_in = sorted(concepts_in, key=lambda c: -int((c.get("evidence") or {}).get("rows") or 0))[:40]
        seen_cv, kept = set(), []
        for c in caveats_in:
            key = " ".join(re.findall(r"[a-z_]{4,}", c["statement"].lower())[:4])
            if key not in seen_cv:
                seen_cv.add(key); kept.append(c)
        caveats_in = kept[:8]
    for c in concepts_in:                   # DR-088 概念层:经数据核验的业务概念
        cid = (f"term_{slug(split_words(c['name']).replace(' ', '_'))}" if READABLE else f"concept_{slug(c['name'])}")[:120]
        if cid in {x["id"] for x in terms}:
            if DEDUP and cid in concept_ids.values():  # 同名概念在另一张表(共享代码):只补映射
                mappings.append({"id": f"map_{cid}__{slug(c['table'])}_{len(mappings)}", "term_id": cid, "database_source": db,
                                 "table": c["table"], "column": c.get("column") or "", "semantic_filter": c.get("filter") or "",
                                 "aggregation_semantics": c.get("aggregation") or "", "grain": f"rows of {c['table']}",
                                 "validation": f"{(c.get('evidence') or {}).get('rows')} matching rows"})
            continue
        ev_id = f"ev_{cid}"
        evidence.append({"id": ev_id, "source": f"{db}.sqlite",
                         "query": f"SELECT COUNT(*) FROM {c['table']}" + (f" WHERE {c['filter']}" if c.get("filter") else ""),
                         "result": f"{(c.get('evidence') or {}).get('rows')} rows",
                         "validation_method": "concept mapping executed on the data", "timestamp": TODAY})
        al = list(dict.fromkeys([*(c.get("aliases") or []), split_words(c["name"]), *extra_alias.get((c.get("table"), c.get("column")), [])]))
        cdef = c.get("definition") or c["name"]
        if DEDUP and re.search(r"[\u4e00-\u9fff]", cdef):
            cdef = f"{split_words(c['name'])}: {cdef}"
        terms.append({"id": cid, "name": (split_words(c["name"]).title() if READABLE else c["name"]), "type": "concept",
                      "definition": cdef,
                      "scope": f"{c['table']}" + (f" where {c['filter']}" if c.get("filter") else ""),
                      "aliases": [a for a in al if a and a.lower() != c["name"].lower()][:8], "evidence": [ev_id]})
        concept_ids[c["name"].lower()] = cid
        mappings.append({"id": f"map_{cid}", "term_id": cid, "database_source": db, "table": c["table"],
                         "column": c.get("column") or "", "semantic_filter": c.get("filter") or "",
                         "aggregation_semantics": c.get("aggregation") or "", "grain": f"rows of {c['table']}",
                         "validation": f"{(c.get('evidence') or {}).get('rows')} matching rows"})
    for c in (ir.get("derived") or []) if USE_CONCEPTS else []:   # 派生指标:公式与各输入的取数方式
        did = (f"term_{slug(split_words(c['name']).replace(' ', '_'))}" if READABLE else f"derived_{slug(c['name'])}")[:120]
        if did in {x["id"] for x in terms}:
            continue
        inputs = "; ".join(f"{x['name']} = {x['table']}.{x.get('column') or '*'}" + (f" WHERE {x['filter']}" if x.get("filter") else "")
                           for x in c.get("inputs") or [])
        al = list(dict.fromkeys([*(c.get("aliases") or []), split_words(c["name"])]))
        terms.append({"id": did, "name": (split_words(c["name"]).title() if READABLE else c["name"]), "type": "metric",
                      "definition": f"{c.get('definition') or c['name']} Formula: {c['formula']}. Inputs: {inputs}",
                      "scope": "derived metric", "aliases": [a for a in al if a and a.lower() != c["name"].lower()][:8],
                      "evidence": []})
        for u in c.get("uses") or []:                  # 派生关系:输入概念 → 派生指标
            src = concept_ids.get(str(u).lower())
            if src:
                relations.append({"id": f"rel_{slug(did)}__from__{slug(src)}"[:150], "source": src, "relation_type": "derivation",
                                  "target": did, "connection_condition": "same entity and period",
                                  "description": f"{c['name']} is computed from {u}: {c['formula']}", "evidence": []})
        x0 = (c.get("inputs") or [{}])[0]
        mappings.append({"id": f"map_{did}", "term_id": did, "database_source": db, "table": x0.get("table", ""),
                         "column": x0.get("column") or "", "semantic_filter": f"{c['formula']} ({inputs})"[:600],
                         "aggregation_semantics": "compute per entity and period from the inputs", "grain": "per entity and period",
                         "validation": "all inputs are data-verified concepts"})
    for th in (ir.get("themes") or []) if USE_CONCEPTS else []:   # 分析主题:一组相关代码
        tid_ = (f"term_{slug(split_words(th['name']).replace(' ', '_'))}" if READABLE else f"theme_{slug(th['name'])}")[:120]
        if tid_ in {x["id"] for x in terms}:
            continue
        codes = [k["code"] for k in th["codes"]]
        terms.append({"id": tid_, "name": split_words(th["name"]).title() if READABLE else th["name"], "type": "concept",
                      "definition": th.get("definition") or th["name"], "scope": f"{th['table']}.{th['name_col']} codes",
                      "aliases": list(dict.fromkeys([*(th.get("aliases") or []), split_words(th["name"])]))[:8], "evidence": []})
        mappings.append({"id": f"map_{tid_}", "term_id": tid_, "database_source": db, "table": th["table"], "column": "",
                         "semantic_filter": f"{th['name_col']} in ({', '.join(repr(c) for c in codes)})"[:900],
                         "aggregation_semantics": "", "grain": f"rows of {th['table']}",
                         "validation": "; ".join(f"{k['code']}: {k.get('entities')} entities" for k in th["codes"][:12])})
    for i, c in enumerate(ir.get("guidance") or []):   # DR-092 按失败项补充的取数建议:验证查询有结果才收进来
        gid = f"ev_guidance_{i}"
        evidence.append({"id": gid, "source": f"{db}.sqlite", "query": c["check_sql"],
                         "result": (c.get("evidence") or {}).get("result", ""),
                         "validation_method": "guidance check query returned supporting rows", "timestamp": TODAY})
        constraints.append({"id": f"c_guidance_{i}", "target": terms[0]["id"] if terms else "", "constraint_type": "usage_guidance",
                            "trigger_keywords": list(dict.fromkeys(re.findall(r"[A-Za-z_]{4,}", c["statement"])))[:12],
                            "severity": "info", "scope": "analysis on this database", "confidence": "high",
                            "description": c["statement"], "evidence": [gid]})
    if DEDUP:                              # DR-094 取值含义另以 enum_semantics 导出,不重复成泛化提醒
        caveats_in = [c for c in caveats_in if c.get("origin") != "value_semantics"]
    for i, c in enumerate(caveats_in):     # DR-088 使用提醒:验证查询有结果才保留
        ev_id = f"ev_caveat_{i}"
        evidence.append({"id": ev_id, "source": f"{db}.sqlite", "query": c["check_sql"],
                         "result": (c.get("evidence") or {}).get("result", ""),
                         "validation_method": "caveat check query returned supporting rows", "timestamp": TODAY})
        constraints.append({"id": f"c_caveat_{i}", "target": terms[0]["id"] if terms else "", "constraint_type": "data_caveat",
                            "trigger_keywords": list(dict.fromkeys(re.findall(r"[A-Za-z_]{4,}", c["statement"])))[:12],
                            "severity": "warning", "scope": "queries on this database", "confidence": "high",
                            "description": c["statement"], "evidence": [ev_id]})
    if DEDUP and ir.get("value_semantics"):           # DR-094 取值含义:每列一条 enum_semantics,挂在该列的术语上
        ttype = {t["id"]: t["type"] for t in terms}
        col_term = {}
        for m in mappings:
            if m.get("column"):
                k = (m["table"], m["column"]); cur = col_term.get(k)
                if cur is None or (ttype.get(cur) == "entity" and ttype.get(m["term_id"]) != "entity"):
                    col_term[k] = m["term_id"]
        by_col = {}
        for v in ir["value_semantics"]:
            by_col.setdefault((v["table"], v["column"]), []).append(v)
        for (t_, c_), items in by_col.items():
            target = col_term.get((t_, c_)) or (tid.get(t_) or (None,))[0]
            if not target:
                continue
            cid_ = f"c_meaning_{slug(t_)}__{slug(c_)}"
            stored = low_card_values(con, t_, c_)
            evidence.append({"id": f"ev_{cid_}", "source": f"{db}.sqlite",
                             "query": f"SELECT {c_}, COUNT(*) FROM {t_} GROUP BY {c_}",
                             "result": "; ".join(f"{v['value']!r}: {v['rows']} rows" for v in items[:12]),
                             "validation_method": "value meanings stated in the construction evidence, checked to occur in the data",
                             "timestamp": TODAY})
            words = [w for v in items for w in re.findall(r"[A-Za-z]{3,}", v["meaning"])]
            constraints.append({"id": cid_, "target": target, "constraint_type": "enum_semantics",
                                "trigger_keywords": list(dict.fromkeys([*(str(v["value"]) for v in items), *words, c_]))[:20],
                                "severity": "warning", "scope": f"Filters on {t_}.{c_}", "confidence": "high",
                                "description": f"{t_}.{c_} stores codes: " + "; ".join(f"{v['value']!r} = {v['meaning']}" for v in items[:12])
                                               + (f". All stored values: {' / '.join(stored)}." if stored else "."),
                                "evidence": [f"ev_{cid_}"]})
    for r in ir.get("relations") or []:
        if r.get("status") != "verified":
            continue
        ch, pa = tid.get(r.get("source_concept")), tid.get(r.get("target_concept"))
        ev = r.get("evidence") or {}
        if not ch or not pa or not ev.get("child_key"):
            continue
        rid = f"rel_{slug(r['target_concept'])}__{slug(r['source_concept'])}__{slug(ev['child_key'])}"
        eid = f"ev_{rid}"
        evidence.append({"id": eid, "source": f"{db}.sqlite",
                         "query": f"distinct values of {ch[1]}.{ev['child_key']} contained in {pa[1]}.{ev.get('parent_key')}",
                         "result": f"overlap {ev.get('overlap')}% ({ev.get('matched_distinct')}/{ev.get('child_distinct')} distinct child values); "
                                   f"parent key unique: {ev.get('parent_unique')}; child rows {ev.get('child_rows')}, parent rows {ev.get('parent_rows')}",
                         "validation_method": "data adjudication: value overlap and parent-key uniqueness",
                         "timestamp": TODAY})
        relations.append({"id": rid, "source": pa[0], "relation_type": "composition", "target": ch[0],
                          "connection_condition": f"{ch[1]}.{ev['child_key']} corresponds to {pa[1]}.{ev.get('parent_key')}",
                          "description": f"{r.get('verb') or 'references'}; {r.get('note') or ''}".strip("; "),
                          "evidence": [eid]})
        for rk in ev.get("role_keys") or []:              # 同一对表的其他角色键(DR-080),各记一条关系
            rrid = f"{rid}__{slug(rk['child_key'])}"
            evidence.append({"id": f"ev_{rrid}", "source": f"{db}.sqlite",
                             "query": f"distinct values of {ch[1]}.{rk['child_key']} contained in {pa[1]}.{rk['parent_key']}",
                             "result": f"overlap {rk.get('overlap')}%; parent key unique: True",
                             "validation_method": "data adjudication: value overlap and parent-key uniqueness",
                             "timestamp": TODAY})
            relations.append({"id": rrid, "source": pa[0], "relation_type": "composition", "target": ch[0],
                              "connection_condition": f"{ch[1]}.{rk['child_key']} corresponds to {pa[1]}.{rk['parent_key']}",
                              "description": f"role key of the same relation ({rk['child_key']})", "evidence": [f"ev_{rrid}"]})
    loss = []
    if FULL:
        export_full(db, ir, tid, terms, mappings, relations, constraints, evidence, loss)
    con.close()
    root = Path(out_root) / db
    SemanticStore.save_version(root, "ontology_v0", {"terms": terms, "mappings": mappings, "relations": relations,
                                                    "constraints": constraints, "evidence": evidence})
    SemanticStore.set_active(root, "ontology_v0")
    rep = validate(root)
    if FULL:
        (root / "loss.json").write_text(json.dumps(loss, ensure_ascii=False, indent=1))
    return {"db": db, "terms": len(terms), "mappings": len(mappings), "relations": len(relations),
            "constraints": len(constraints), "evidence": len(evidence), "errors": rep.get("errors") if isinstance(rep, dict) else rep,
            **({"loss": [{"kind": x["kind"], "count": x["count"]} for x in loss]} if FULL else {})}


def _loss(loss, kind, items, example=lambda x: x):
    items = list(items or [])
    if items:
        exs = []
        for x in items[:3]:
            e = example(x)
            exs.append(e[:400] if isinstance(e, str) else json.loads(json.dumps(e, ensure_ascii=False, default=str)))
        loss.append({"kind": kind, "count": len(items), "examples": exs})


def export_full(db, ir, tid, terms, mappings, relations, constraints, evidence, loss):
    """协议 v2 的无损导出:候选关系、指标契约、丢失项清单。定义/反例/成熟度/来源已在术语处理时写入。"""
    seen_ids = {x["id"] for x in relations}
    # 1. 候选关系:EvoOntology 没有候选状态,用 association + 描述前缀 + 裁决数字证据表达
    n_cand, dropped_cand = 0, []
    for r in ir.get("relations") or []:
        if r.get("status") != "candidate":
            continue
        ch, pa = tid.get(r.get("source_concept")), tid.get(r.get("target_concept"))
        ev = r.get("evidence") or {}
        ck = ev.get("child_key") or (r.get("proposal") or {}).get("child_key_hint")
        pk = ev.get("parent_key") or (r.get("proposal") or {}).get("parent_key_hint")
        if not ch or not pa:
            dropped_cand.append(r); continue
        rid = f"rel_cand_{slug(r['target_concept'])}__{slug(r['source_concept'])}__{slug(ck or 'nokey')}"
        if rid in seen_ids:
            rid += f"_{n_cand}"
        seen_ids.add(rid)
        eid = f"ev_{rid}"
        evidence.append({"id": eid, "source": f"{db}.sqlite",
                         "query": (f"distinct values of {ch[1]}.{ck} contained in {pa[1]}.{pk}" if ck and pk
                                   else f"candidate link between {ch[1]} and {pa[1]} (keys unresolved)"),
                         "result": (f"overlap {ev.get('overlap')}%; child_distinct {ev.get('child_distinct')}; "
                                    f"parent_distinct {ev.get('parent_distinct')}; matched_distinct {ev.get('matched_distinct')}; "
                                    f"parent_unique {ev.get('parent_unique')}; child_rows {ev.get('child_rows')}; "
                                    f"parent_rows {ev.get('parent_rows')}; decision: {ev.get('decision') or r.get('note') or ''}"),
                         "validation_method": "data adjudication (candidate: below threshold or unresolved key naming)",
                         "timestamp": TODAY})
        relations.append({"id": rid, "source": pa[0], "relation_type": "association", "target": ch[0],
                          "connection_condition": (f"{ch[1]}.{ck} corresponds to {pa[1]}.{pk}" if ck and pk
                                                   else f"{ch[1]} relates to {pa[1]}"),
                          "description": "candidate (data overlap below threshold or unresolved): "
                                         + f"{r.get('verb') or 'references'}; {(r.get('proposal') or {}).get('rationale') or r.get('note') or ''}".strip("; "),
                          "evidence": [eid]})
        n_cand += 1
    _loss(loss, "candidate_relations_without_bound_objects", dropped_cand,
          lambda r: f"{r.get('source_concept')} -> {r.get('target_concept')}")
    # 2. 指标契约(DR-054):metric_layers 与对象 indicators → 指标术语 + 映射 + usage_guidance 约束(含编译 SQL 与状态)
    mc = _metric_contract()
    found, bad = [], []
    for layer, arr in ((ir.get("metric_layers") or {}).items() if isinstance(ir.get("metric_layers"), dict) else []):
        for m in arr or []:
            if isinstance(m, dict):
                found.append((layer, m, ""))
    for o in ir.get("objects") or []:
        for m in o.get("indicators") or []:
            if isinstance(m, dict):
                found.append((m.get("layer") or "atomic", m, o.get("name")))
    term_ids = {x["id"] for x in terms}
    for layer, m, owner in found:
        name = str(m.get("name") or m.get("id") or "").strip()
        if not name:
            bad.append(m); continue
        mid = f"term_metric_{slug(name)}"[:120]
        k = 2
        while mid in term_ids:
            mid = f"term_metric_{slug(name)}_{k}"[:120]; k += 1
        term_ids.add(mid)
        contract = mc.is_contract(m)
        table = m.get("table") or m.get("bound_table") or ""
        col = (m.get("measure") or {}).get("col") or m.get("value_col") or ""
        status = m.get("status") if m.get("status") in mc.STATUSES else "candidate"
        sql, rule, err = "", "", ""
        if contract:
            try:
                rule = mc.describe(m)
                sql = mc.compile_sql(m)
            except Exception as exc:                                  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
        t = m.get("time") or {}
        filt = " AND ".join(f"{f.get('col')} {f.get('op')} {f.get('value')}" for f in (m.get("filters") or []) if isinstance(f, dict))
        evs = []
        ev_c = (m.get("evidence") or {}) if isinstance(m.get("evidence"), dict) else {}
        if ev_c.get("compiled_sql") or sql:
            eid = f"ev_{mid}"
            evidence.append({"id": eid, "source": f"{db}.sqlite", "query": ev_c.get("compiled_sql") or sql,
                             "result": (f"executed: {ev_c.get('executed')}; rows {ev_c.get('rows')}; value {ev_c.get('value')}; "
                                        f"reference match: {ev_c.get('match')}" if ev_c else "compiled from contract; not executed here"),
                             "validation_method": "metric contract compiled and executed read-only against a reference"
                                                  if ev_c.get("executed") else "metric contract compiled deterministically",
                             "timestamp": TODAY})
            evs.append(eid)
        terms.append({"id": mid, "name": name, "type": "metric",
                      "definition": (m.get("desc") or m.get("description") or name)
                                    + (f". Rule: {rule}" if rule else "") + (f". Unit: {m['unit']}" if m.get("unit") else "")
                                    + (f". Layer: {layer}" if layer else ""),
                      "scope": (f"{table}" if table else "unbound") + (f"; owned by object {owner}" if owner else "")
                               + f"; status {status}",
                      "aliases": [a for a in dict.fromkeys(list(m.get("synonyms") or m.get("aliases") or []) + [split_words(name)])
                                  if a and a.lower() != name.lower()][:8],
                      "evidence": evs})
        mappings.append({"id": f"map_{mid}", "term_id": mid, "database_source": db, "table": table, "column": col,
                         "semantic_filter": filt + (f"{'; ' if filt else ''}time {t.get('col')} by {'/'.join(t.get('grain') or [])}" if t.get("col") else ""),
                         "aggregation_semantics": ((m.get("measure") or {}).get("agg") or "") + (f" ({rule})" if rule else ""),
                         "grain": f"per {table} row aggregated" if table else "",
                         "validation": f"status {status}; " + ("contract compiled" if sql else f"not compilable: {err or 'no aggregation declared'}")})
        constraints.append({"id": f"c_{mid}", "target": mid, "constraint_type": "usage_guidance",
                            "trigger_keywords": list(dict.fromkeys([name, split_words(name), *(m.get("synonyms") or [])]))[:12],
                            "severity": "info", "scope": f"computing {name}", "confidence": "high" if status in ("verified", "certified") else "medium",
                            "description": (f"Status {status}. " + (f"Compiled SQL: {sql}" if sql else f"No compilable rule ({err or 'no aggregation declared'})")
                                            + (f". Formula: {m['formula']}" if m.get("formula") else "")),
                            "evidence": evs})
    _loss(loss, "metrics_without_name", bad)
    # 3. 丢失项清单:导出格式表达不了的内容
    _loss(loss, "rejected_relations", ir.get("rejected_relations"), lambda r: f"{r.get('source_concept')} -> {r.get('target_concept')}: {r.get('note') or ''}")
    _loss(loss, "deferred_relations", ir.get("deferred_relations"), lambda r: f"{r.get('source_concept')} -> {r.get('target_concept')}")
    _loss(loss, "concept_rejected", ir.get("concept_rejected"), lambda c: c.get("name") if isinstance(c, dict) else str(c))
    bq = ir.get("build_quality") or {}
    _loss(loss, "build_quality.review_queue", bq.get("review_queue"), lambda x: x.get("reason") or x.get("desc") if isinstance(x, dict) else str(x))
    _loss(loss, "build_quality.blocking_issues", bq.get("blocking_issues"), lambda x: str(x)[:200])
    _loss(loss, "build_quality.summary", [bq.get("summary")] if bq.get("summary") else [], lambda x: x)
    _loss(loss, "gaps", ir.get("gaps"), lambda g: g.get("desc") if isinstance(g, dict) else str(g))
    _loss(loss, "health.signals", (bq.get("health") or {}).get("signals"), lambda s: s.get("desc") if isinstance(s, dict) else str(s))
    _loss(loss, "action_objects", [o for o in ir.get("objects") or [] if o.get("kind") == "action"], lambda o: o.get("name"))
    _loss(loss, "objects_without_table", [o for o in ir.get("objects") or [] if not o.get("table") and o.get("kind") != "action"], lambda o: o.get("name"))
    _loss(loss, "relation_fields_not_representable", [{"fields": ["semantic_status", "review.priority", "cross_check", "theta", "name_score"]}], lambda x: x)
    _loss(loss, "object_fields_not_representable", [{"fields": ["example", "example_provenance", "definition_status", "counterexample_status", "bfo", "isPrimitive", "field_count"]}], lambda x: x)
    _loss(loss, "metric_report", [ir.get("metric_report")] if ir.get("metric_report") else [], lambda x: {k: x.get(k) for k in ("candidates", "contracts", "verified", "executable_unverified", "unbound")})


if __name__ == "__main__":
    graphs = json.loads(Path(sys.argv[1]).read_text())
    for db, info in graphs.items():
        print(json.dumps(convert(db, info["graph"], sys.argv[2]), ensure_ascii=False))

# -*- coding: utf-8 -*-
"""DR-070:DR-069 复测后余下的缺口。

- 孤儿记录:关联表与计量关系里大量引用在父表找不到,重叠率达不到 60%,但键名就是父表的标识键;
- 类别作用域编码:指标编码只在类别内唯一,要按(类别列, 编码)复合键关联,类别列名在两边不同;
- 码表关系:字段按哪套字典分组解释已由清洗规则判定,却没有成为本体关系;
- 取值形态:没有代码包时,字母数字编码 100% 落在父表标识列里,键名不同也应可认定;
- 对象类型:定义里出现「生产」就把位置对象判成事件;
- 性能:相关子查询(EXISTS)在无索引的大表上逐行扫描。
"""
import importlib.util
import json
import os

import build_quality
import content_quality
import server

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ev(tables):
    return {"tab_cols": {t: [(c, "TEXT") for c in cols] for t, cols in tables.items()},
            "schema": "", "docs": "", "n_docs": 0, "refs": []}


def _adjudicate(db, objects, relations, cols, monkeypatch):
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    return server._adjudicate_ir(db, "t", json.loads(json.dumps({"objects": objects, "relations": relations})), _ev(cols))


# ── 孤儿记录 ──────────────────────────────────────────────────────

def _orphan_db(make_sqlite, child_key="sys_no"):
    return make_sqlite({
        "inst_system": ("sys_no TEXT, sys_name TEXT", [(f"S{i:03d}", "s") for i in range(10)]),
        "measure_rel": (f"id INTEGER, {child_key} TEXT",
                        [(i, f"S{i % 10:03d}") for i in range(20)] + [(100 + i, f"GONE{i:03d}") for i in range(20)]),
    })


def test_orphan_heavy_identifier_reference_is_verified(make_sqlite, monkeypatch):
    db = _orphan_db(make_sqlite)
    ir = _adjudicate(db, [{"name": "rel", "table": "measure_rel", "kind": "ice"}, {"name": "sys", "table": "inst_system", "kind": "object"}],
                     [{"source": "rel", "target": "sys", "verb": "计量", "child_key": "sys_no", "parent_key": "sys_no"}],
                     {"inst_system": ["sys_no", "sys_name"], "measure_rel": ["id", "sys_no"]}, monkeypatch)
    rel = ir["relations"][0]
    assert rel["status"] == "verified"
    assert rel["evidence"]["orphan_tolerant"]["orphan_rate"] > 0.6
    assert "孤儿记录" in rel["note"]
    assert build_quality.audit_verified_evidence(ir)["valid"]


def test_orphan_rule_needs_identifier_name(make_sqlite, monkeypatch):
    db = _orphan_db(make_sqlite, child_key="ref_code")
    ir = _adjudicate(db, [{"name": "rel", "table": "measure_rel", "kind": "ice"}, {"name": "sys", "table": "inst_system", "kind": "object"}],
                     [{"source": "rel", "target": "sys", "verb": "计量", "child_key": "ref_code", "parent_key": "sys_no"}],
                     {"inst_system": ["sys_no", "sys_name"], "measure_rel": ["id", "ref_code"]}, monkeypatch)
    assert ir["relations"][0]["status"] == "candidate"


# ── 类别作用域编码 ────────────────────────────────────────────────

def test_category_scoped_code_resolved_by_composite_key(make_sqlite, monkeypatch):
    defs = []
    for cat in ("METE", "SYS", "PV"):
        for code in ("Eout", "Ein", "Load", "Pmax"):
            defs.append((f"{cat}-{code}", cat, code, f"{cat}{code}名", 0))
    defs += [("dupe", "METE", "Eout", "METEEout名", 0)]                       # 同键同名的重复版本
    insts = [(i, ("METE", "SYS", "PV")[i % 3], ("Eout", "Ein", "Load", "Pmax")[i % 4]) for i in range(60)]
    db = make_sqlite({"meta_index": ("id TEXT, obj_code TEXT, index_code TEXT, index_name TEXT, is_del INTEGER", defs),
                      "inst_index": ("id INTEGER, meta_obj_type TEXT, index_code TEXT", insts)})
    ir = _adjudicate(db, [{"name": "inst", "table": "inst_index", "kind": "object"}, {"name": "defn", "table": "meta_index", "kind": "ice"}],
                     [{"source": "inst", "target": "defn", "verb": "实例化", "child_key": "index_code", "parent_key": "index_code"}],
                     {"meta_index": ["id", "obj_code", "index_code", "index_name", "is_del"],
                      "inst_index": ["id", "meta_obj_type", "index_code"]}, monkeypatch)
    rel = ir["relations"][0]
    assert rel["status"] == "verified", rel["note"]
    assert rel["evidence"]["child_key"] == "meta_obj_type,index_code"
    assert rel["evidence"]["parent_key"] == "obj_code,index_code"
    assert build_quality.audit_verified_evidence(ir)["valid"], build_quality.audit_verified_evidence(ir)["issues"]


# ── 码表关系 ──────────────────────────────────────────────────────

def _load_data_clean():
    spec = importlib.util.spec_from_file_location(
        "data_clean", os.path.join(ROOT, "skills_seed", "ontology-cross-check", "scripts", "data_clean.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_codebook_rule_carries_dictionary_columns(make_sqlite):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "skills_seed", "ontology-cross-check", "scripts"))
    dc = _load_data_clean()
    import cross_check
    db_path = make_sqlite({
        "meta__dictionary_item": ("dict_code TEXT, value TEXT, label TEXT",
                                  [("ENERGY", "1", "水"), ("ENERGY", "2", "电"), ("ENERGY", "3", "风"),
                                   ("VALUE", "1", "整型"), ("VALUE", "2", "文本")]),
        "inst__measure_rel": ("id INTEGER, energy_type TEXT", [(i, str(1 + i % 3)) for i in range(30)]),
    })
    db = cross_check.Db(db_path)
    rules = dc.clean(db, db.con, [], {"inst__measure_rel", "meta__dictionary_item"})["rules"]
    book = next(r for r in rules if r["kind"] == "codebook" and r["column"] == "energy_type")
    assert book["dict_table"] == "meta__dictionary_item" and book["group_column"] == "dict_code"
    assert book["value_column"] == "value" and book["group"] == "ENERGY"


def test_codebook_rule_becomes_scoped_relation(make_sqlite):
    db = make_sqlite({
        "meta__dictionary_item": ("dict_code TEXT, value TEXT, label TEXT",
                                  [("ENERGY", "1", "水"), ("ENERGY", "2", "电"), ("VALUE", "1", "整型")]),
        "inst__measure_rel": ("id INTEGER, energy_type TEXT", [(i, str(1 + i % 2)) for i in range(20)]),
    })
    rule = {"kind": "codebook", "table": "inst__measure_rel", "column": "energy_type", "book": "dictionary_item:ENERGY",
            "coverage": 1.0, "dict_table": "meta__dictionary_item", "group_column": "dict_code", "value_column": "value",
            "group": "ENERGY", "ambiguous_with": []}
    ir = {"objects": [{"name": "rel", "table": "inst__measure_rel", "evidence": {"cleaning": [rule]}},
                      {"name": "item", "cn": "字典项", "table": "meta__dictionary_item"}], "relations": []}
    added = server._codebook_relations(ir, db)
    assert added == 1
    rel = ir["relations"][0]
    assert rel["status"] == "verified" and rel["evidence"]["valid_rows"]["rule"] == "scoped"
    assert "meta__dictionary_item.dict_code = 'ENERGY'" in "\n".join(
        server._join_hints(ir, ["inst__measure_rel", "meta__dictionary_item"]))
    assert build_quality.audit_verified_evidence(ir)["valid"], build_quality.audit_verified_evidence(ir)["issues"]


# ── 取值形态证据 ──────────────────────────────────────────────────

def _code_db(make_sqlite, codes):
    return make_sqlite({
        "dim_zone": ("zone_id TEXT, zone_name TEXT", [(c, f"区{c}") for c in codes] + [("X999", "备用")]),
        "dwd_pass": ("id INTEGER, line_sn TEXT", [(i, codes[i % len(codes)]) for i in range(60)]),
    })


def test_code_like_values_substitute_for_key_name(make_sqlite, monkeypatch):
    codes = [f"DIP01{i:02d}" for i in range(12)]
    ir = _adjudicate(_code_db(make_sqlite, codes),
                     [{"name": "pass", "table": "dwd_pass", "kind": "event"}, {"name": "zone", "table": "dim_zone", "kind": "object"}],
                     [{"source": "pass", "target": "zone", "verb": "发生于", "child_key": "line_sn", "parent_key": "zone_id"}],
                     {"dim_zone": ["zone_id", "zone_name"], "dwd_pass": ["id", "line_sn"]}, monkeypatch)
    rel = ir["relations"][0]
    assert rel["status"] == "verified" and rel["evidence"]["value_pattern"]["distinct"] == 12


def test_integer_values_do_not_substitute(make_sqlite, monkeypatch):
    codes = [str(i) for i in range(1, 13)]
    ir = _adjudicate(_code_db(make_sqlite, codes),
                     [{"name": "pass", "table": "dwd_pass", "kind": "event"}, {"name": "zone", "table": "dim_zone", "kind": "object"}],
                     [{"source": "pass", "target": "zone", "verb": "发生于", "child_key": "line_sn", "parent_key": "zone_id"}],
                     {"dim_zone": ["zone_id", "zone_name"], "dwd_pass": ["id", "line_sn"]}, monkeypatch)
    assert ir["relations"][0]["status"] == "candidate"


# ── 对象类型 ──────────────────────────────────────────────────────

def test_kind_follows_head_noun_not_definition():
    nk = content_quality.normalize_kind
    assert nk("event", name="dim_co_area", cn="产线区域", table="dim_co_area",
              definition="一种制造现场位置对象,记录生产位置")[0] == "object"
    assert nk("ice", name="wip", cn="在制品过站明细", table="dwd_wip_detail")[0] == "event"
    assert nk("object", name="fpy", cn="工单直通率日汇总", table="dws_order_fpy_day")[0] == "ice"
    assert nk("ice", name="mo", cn="生产工单", table="t_pm_mo_base")[0] == "ice"
    assert nk("event", name="alarm", cn="告警事件", table="alarm_info")[0] == "event"
    assert nk("object", name="qc", cn="质量检验记录", table="qc_record")[0] == "ice"
    assert nk("asset", name="meter", cn="能源计量器具", table="inst_device")[0] == "asset"


# ── 行业术语:参照概念的别名进入对象,问数可据此对上「线别」 ───────────

def test_reference_aliases_reach_objects_and_dimension_check():
    import build_references
    import intent_check
    ir = {"objects": [{"name": "dim_co_area", "cn": "产线区域", "table": "dim_co_area", "kind": "object"},
                      {"name": "wip", "cn": "在制品过站明细", "table": "dwd_wip", "kind": "event"}], "relations": []}
    build_references.apply_profile(ir, {"version": 1, "industry": {"id": "pcba", "mode": "reference"},
                                        "ontology_standard": {"id": "none", "mode": "reference"}})
    area = ir["objects"][0]
    assert "线别" in area["aliases"] and area["aliases_source"] == "industry_reference"
    build_references.apply_profile(ir, {"version": 1, "industry": {"id": "pcba", "mode": "reference"},
                                        "ontology_standard": {"id": "none", "mode": "reference"}})
    assert area["aliases"].count("线别") == 1                                  # 重复应用不重复添加
    sql = "SELECT a.ca_name AS 线别, COUNT(*) AS n FROM dwd_wip w JOIN dim_co_area a ON a.ca_id = w.area_sn GROUP BY 1"
    assert intent_check.dimension_check("各线别的过站记录数", sql, ir) == []


def test_duplicate_binding_rebound_to_exact_table(make_sqlite, monkeypatch):
    """同一轮两个对象绑到同一张表,其中一个的名称恰好是库里另一张表的核心名:改绑过去(该表可不在本轮范围)。"""
    db = make_sqlite({"user__enterprise": ("ent_no TEXT, ent_name TEXT", [(f"E{i}", "e") for i in range(10)]),
                      "biz__enterprise_survey": ("id INTEGER, ent_no TEXT, q TEXT", [(i, f"E{i}", "q") for i in range(10)]),
                      "inst__enterprise_system_rel": ("ent_no TEXT, sys_no TEXT", [(f"E{i}", f"S{i}") for i in range(10)])})
    objs = [{"name": "enterprise", "cn": "企业", "table": "biz__enterprise_survey", "kind": "object"},
            {"name": "enterprise_survey", "cn": "企业调研记录", "table": "biz__enterprise_survey", "kind": "ice"},
            {"name": "esr", "cn": "企业用能系统关联", "table": "inst__enterprise_system_rel", "kind": "ice"}]
    rels = [{"source": "esr", "target": "enterprise", "verb": "属于", "child_key": "ent_no", "parent_key": "ent_no"}]
    ir = _adjudicate(db, objs, rels, {"user__enterprise": ["ent_no", "ent_name"], "biz__enterprise_survey": ["id", "ent_no", "q"],
                                      "inst__enterprise_system_rel": ["ent_no", "sys_no"]}, monkeypatch)
    ent = next(o for o in ir["objects"] if o["name"] == "enterprise")
    assert ent["table"] == "user__enterprise" and ent["rebound_from"] == "biz__enterprise_survey"
    assert ir["relations"][0]["status"] == "verified"


def test_scoped_key_tried_even_when_model_gave_composite_hint(make_sqlite, monkeypatch):
    defs = [(f"{c}-{k}", c, k, f"{k}名", 0) for c in ("METE", "SYS", "PV") for k in ("Eout", "Ein", "Load", "Pmax")]
    insts = [(i, ("METE", "SYS", "PV")[i % 3], ("Eout", "Ein", "Load", "Pmax")[i % 4], f"{('Eout', 'Ein', 'Load', 'Pmax')[i % 4]}名")
             for i in range(60)]                                           # 指标名不含类别,(编码, 名称)仍不唯一
    db = make_sqlite({"meta_index": ("id TEXT, obj_code TEXT, index_code TEXT, index_name TEXT, is_del INTEGER", defs),
                      "inst_index": ("id INTEGER, meta_obj_type TEXT, index_code TEXT, index_name TEXT", insts)})
    ir = _adjudicate(db, [{"name": "inst", "table": "inst_index", "kind": "object"}, {"name": "defn", "table": "meta_index", "kind": "ice"}],
                     [{"source": "inst", "target": "defn", "verb": "实例化", "child_key": "index_code,index_name",
                       "parent_key": "index_code,index_name"}],
                     {"meta_index": ["id", "obj_code", "index_code", "index_name", "is_del"],
                      "inst_index": ["id", "meta_obj_type", "index_code", "index_name"]}, monkeypatch)
    rel = ir["relations"][0]
    assert rel["status"] == "verified" and rel["evidence"]["parent_key"] == "obj_code,index_code", rel["note"]


def test_scope_column_needs_name_or_code_basis(make_sqlite, monkeypatch):
    """0/1 标志列碰巧落在父表 0/1 类别列的值域里,不能当作类别列。"""
    defs = [(f"{t}-{k}", t, k, f"{k}名") for t in (0, 1) for k in ("a1", "b2", "c3", "d4")]
    insts = [(i, i % 2, ("a1", "b2", "c3", "d4")[i % 4]) for i in range(40)]
    db = make_sqlite({"meta_attribute": ("id TEXT, obj_type INTEGER, attr_key TEXT, attr_name TEXT", defs),
                      "inst_attribute": ("id INTEGER, required INTEGER, meta_attr_key TEXT", insts)})
    ir = _adjudicate(db, [{"name": "inst", "table": "inst_attribute", "kind": "object"}, {"name": "defn", "table": "meta_attribute", "kind": "ice"}],
                     [{"source": "inst", "target": "defn", "verb": "实例化", "child_key": "meta_attr_key", "parent_key": "attr_key"}],
                     {"meta_attribute": ["id", "obj_type", "attr_key", "attr_name"], "inst_attribute": ["id", "required", "meta_attr_key"]},
                     monkeypatch)
    assert "scope" not in (ir["relations"][0].get("evidence") or {})


def test_reference_aliases_go_to_best_matching_object_only():
    import build_references
    ir = {"objects": [{"name": "sys", "cn": "用能单元", "table": "inst_system", "kind": "object"},
                      {"name": "esr", "cn": "企业用能系统关联", "table": "enterprise_system_rel", "kind": "ice"},
                      {"name": "st", "cn": "用能系统类型", "table": "meta_system_type", "kind": "ice"}], "relations": []}
    prof = {"version": 1, "industry": {"id": "energy_management", "mode": "reference"},
            "ontology_standard": {"id": "none", "mode": "reference"}}
    build_references.apply_profile(ir, prof)
    by = {o["name"]: o for o in ir["objects"]}
    assert "用能系统" in by["sys"].get("aliases", [])
    assert not by["esr"].get("aliases") and not by["st"].get("aliases")
    by["esr"]["aliases"] = ["人工别名"]
    build_references.apply_profile(ir, prof)
    assert by["esr"]["aliases"] == ["人工别名"]                                # 人工别名不受影响


def test_recall_prefers_exact_chinese_name_over_glossary_hits(monkeypatch):
    """问句里原样出现的业务名(计量关系)不能被「配置 → config」命中的一串配置表挤出上下文。"""
    objs = [{"name": f"cfg{i}", "cn": f"工单配置{i}", "table": f"biz__work_config{i}", "attrs": []} for i in range(10)]
    objs.append({"name": "mr", "cn": "计量关系", "table": "inst__measure_relation", "attrs": [{"col": "sys_no", "cn": ""}]})
    ir = {"objects": objs, "relations": []}
    profile = {"queryable": True, "bound_tables": [o["table"] for o in objs], "available_tables": [o["table"] for o in objs], "unavailable_tables": []}
    monkeypatch.setattr(server, "_qa_anchor_ir", lambda keys: (ir, ["g"], [], profile))
    trace = {}
    server.build_context("哪些用能单元配置的计量关系最多", trace=trace, graph_keys=["g"])
    assert "inst__measure_relation" in [o.get("table") for o in trace.get("objects", [])]


# ── 模型超时:先重试一次;降级构建的产物要绑表,后续轮次才连得上 ─────────

def test_fallback_objects_are_bound_to_tables():
    ir = {"objects": [{"name": "inst__inst_device", "kind": "object", "tables": ["inst__inst_device"], "field_count": 2}],
          "relations": []}
    ev = {"tab_cols": {"inst__inst_device": [("dev_no", "TEXT"), ("mete_dev_type_code", "TEXT")]}}
    server._bind_fallback_objects(ir, ev, {"inst__inst_device": "设备实例"})
    o = ir["objects"][0]
    assert o["table"] == "inst__inst_device" and o["cn"] == "设备实例"
    assert [a["col"] for a in o["attrs"]] == ["dev_no", "mete_dev_type_code"]


def test_extraction_retried_once_before_fallback(tmp_path, monkeypatch):
    from tests.scenarios import bench, fixtures
    work = tmp_path / "w"
    work.mkdir()
    fx = fixtures.business(str(tmp_path / "b.db"))
    calls = []

    def extract(*a, **k):
        calls.append(1)
        return None if len(calls) == 1 else json.loads(json.dumps(fx["proposal"]))
    monkeypatch.setattr(server, "WORK", str(work))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(work / "build_connections.json"))
    for k, v in {"_build_intent": lambda *a, **k: {}, "_build_summary": lambda *a, **k: "摘要",
                 "_llm_semantic_review": lambda *a, **k: None, "_llm_extract_ontology": extract}.items():
        monkeypatch.setattr(server, k, v)
    conn = bench._register(server, work, fx["db"], "重试库")
    raw = server.app.test_client().post("/api/build/inquire", json={"q": "构建", "source": conn, "name": "重试",
                                                                    "skills": []}).get_data(as_text=True)
    events = [json.loads(x[6:]) for x in raw.splitlines() if x.startswith("data: ")]
    done = next(e for e in events if e.get("type") == "done")
    assert len(calls) == 2 and done.get("method") == "LLM 辅助提议"


def test_key_links_rank_by_evidence_not_round():
    """全量分轮时候选很多:名额按证据排序,旧轮表对本轮实例表的引用不能被挤出(DR-071)。"""
    import key_links
    tc = {"inst__inst_system": [("sys_no", "TEXT")], "biz__bill_data": [("id", "TEXT"), ("sys_no", "TEXT")]}
    objs = [{"name": "sys", "table": "inst__inst_system"}, {"name": "bill", "table": "biz__bill_data"}]
    for k in range(30):                                   # 本轮里一批取值对不上的噪声候选
        tc[f"inst__cfg{k}"] = [("id", "TEXT"), ("cfg_no", "TEXT")]
        tc[f"inst__cfg_item{k}"] = [("id", "TEXT"), ("cfg_no", "TEXT")]
        objs += [{"name": f"cfg{k}", "table": f"inst__cfg{k}"}, {"name": f"item{k}", "table": f"inst__cfg_item{k}"}]
    tc = {k: list(v) for k, v in tc.items()}
    probe = lambda ct, cc, pt, pc: 90.0 if ct == "biz__bill_data" else 5.0      # noqa: E731
    rnd = ["inst__inst_system"] + [f"inst__cfg{k}" for k in range(30)] + [f"inst__cfg_item{k}" for k in range(30)]
    props = key_links.propose(tc, objs, rnd, probe=probe, max_proposals=5)
    assert ("biz__bill_data", "sys_no", "inst__inst_system") in {(p["child_table"], p["child_key"], p["parent_table"]) for p in props}
    assert key_links.DISCRIMINATOR.match("obj_type_code")


def test_exact_core_beats_longer_shared_prefix():
    import key_links
    tc = {"inst__inst_system": [("sys_no", "TEXT")], "biz__energy_bill_config_system": [("id", "TEXT"), ("sys_no", "TEXT")],
          "biz__energy_bill_data": [("id", "TEXT"), ("sys_no", "TEXT")]}
    objs = [{"name": t.split("__")[1], "table": t} for t in tc]
    got = {(p["child_table"], p["parent_table"]) for p in key_links.propose(tc, objs, list(tc), probe=lambda *a: 90.0)}
    assert ("biz__energy_bill_data", "inst__inst_system") in got
    assert ("biz__energy_bill_data", "biz__energy_bill_config_system") not in got


# ── 全量本体:同名概念与两字中心词召回(DR-071) ─────────────────────

def test_duplicate_cn_disambiguated_by_degree():
    ir = {"objects": [{"name": "eso", "cn": "用能单元", "table": "biz__energy_structure_object"},
                      {"name": "sys", "cn": "用能单元", "table": "inst__inst_system"},
                      {"name": "dev", "cn": "设备", "table": "inst__inst_device"}],
          "relations": [{"source_concept": "dev", "target_concept": "sys", "status": "verified"},
                        {"source_concept": "eso", "target_concept": "sys", "status": "candidate"}]}
    n = server._disambiguate_cn(ir)
    by = {o["name"]: o for o in ir["objects"]}
    assert n == 1 and by["sys"]["cn"] == "用能单元"
    assert by["eso"]["cn"] != "用能单元" and by["eso"]["cn_review"]["original"] == "用能单元"
    assert server._disambiguate_cn(ir) == 0                                     # 再跑不重复改名


def test_two_char_head_noun_recalled(monkeypatch):
    objs = [{"name": f"cfg{i}", "cn": f"平台设备{i}号表", "table": f"biz__plat_device_{i}", "attrs": []} for i in range(10)]
    objs.append({"name": "alarm", "cn": "设备告警", "table": "inst__device_alarm", "attrs": []})
    ir = {"objects": objs, "relations": []}
    t = [o["table"] for o in objs]
    monkeypatch.setattr(server, "_qa_anchor_ir", lambda keys: (ir, ["g"], [], {"queryable": True, "bound_tables": t,
                                                                              "available_tables": t, "unavailable_tables": []}))
    trace = {}
    server.build_context("告警次数最多的前5台设备是哪些", trace=trace, graph_keys=["g"])
    assert "inst__device_alarm" in [o.get("table") for o in trace.get("objects", [])]


def test_valid_rows_not_applied_to_reverse_direction(make_sqlite):
    """子列唯一、目标列大量重复:目标表是多的一侧,不能按「版本重复」升为已验证(DR-071)。"""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "skills_seed", "ontology-cross-check", "scripts"))
    import cross_check
    db = cross_check.Db(make_sqlite({
        "cfg": ("id INTEGER, metric_code TEXT", [(i, f"M{i}") for i in range(5)]),
        "inst_metric": ("id INTEGER, metric_code TEXT, metric_name TEXT, create_at TEXT",
                        [(i, f"M{i % 5}", f"量{i % 5}", f"2024-01-{1 + i % 28:02d}") for i in range(200)])}))
    r = cross_check.valid_rows_channel(db, "cfg", "metric_code", "inst_metric", "metric_code",
                                       {"overlap": 100.0, "parent_unique": False, "direction": "reverse"})
    assert r["state"] == "na"

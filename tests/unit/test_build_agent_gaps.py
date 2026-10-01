# -*- coding: utf-8 -*-
"""真实模型评估暴露的构建缺口(DR-069)。

- 后续轮次连不上前面轮次的对象:实例表 → 类型目录这类核心关系一条都没提出;
- 多态引用(obj_type + obj_no)按判别值才成立,键名不同就一律停在候选;
- 键名缩写(dev ↔ device、sys ↔ system、ent ↔ enterprise)与驼峰(owner_entNo)被误判为键名无据;
- 问句里的维度词没有对应到本体,模型挑了含义不明的列作答,结果无任何提示;
- 继续构建时本体名被模型改掉。
"""
import json

import dao_core
import intent_check
import key_links
import server


# ── 键名:缩写与驼峰 ──────────────────────────────────────────────

def test_abbreviated_roots_are_name_compatible():
    assert dao_core.name_ok("dev_group_id", "meta__meta_device_group", "id")
    assert dao_core.key_name_ok("owner_entNo", "ent_no")
    assert dao_core.key_name_ok("mete_dev_type_code", "dev_type_code")


def test_generic_names_still_rejected():
    assert not dao_core.name_ok("order_id", "customers", "id")
    assert not dao_core.key_name_ok("type_code", "dev_type_code")
    assert not dao_core.key_name_ok("dev_no", "sys_no")


# ── 确定性键匹配提议 ──────────────────────────────────────────────

def _tab_cols():
    t = {
        "meta__meta_device_type": ["id", "dev_type_code", "dev_type_name", "is_del"],
        "meta__meta_system_type": ["id", "sys_type_code", "sys_type_name"],
        "meta__meta_index": ["id", "index_code", "index_name"],
        "meta__meta_index_formula": ["id", "meta_index_id", "formula"],
        "user__enterprise": ["id", "ent_no", "ent_name"],
        "inst__inst_device": ["id", "dev_no", "mete_dev_type_code", "loc_no", "create_by", "parent_no"],
        "inst__inst_location": ["id", "loc_no", "loc_name"],
        "inst__inst_system": ["id", "sys_no", "meta_sys_type_code"],
        "inst__enterprise_system_rel": ["id", "ent_no", "sys_no"],
        "inst__inst_index": ["id", "obj_type", "obj_no", "index_code"],
        "biz__bill": ["id", "ent_no", "sys_no", "amount"],
    }
    for k in range(10):                     # 企业编号横跨大量表:租户键
        t[f"biz__cfg{k}"] = ["id", "ent_no", "title"]
    return {k: [(c, "TEXT") for c in v] for k, v in t.items()}


def _objects():
    objs = []
    for t in _tab_cols():
        objs.append({"name": t.split("__")[-1], "table": t})
    return objs


def _pairs(props):
    return {(p["child_table"], p["child_key"], p["parent_table"], p["parent_key"]) for p in props}


def test_key_links_cross_round_and_rel_tables():
    rnd = ["inst__inst_device", "inst__inst_location", "inst__inst_system", "inst__enterprise_system_rel",
           "inst__inst_index", "biz__bill"] + [f"biz__cfg{k}" for k in range(10)]
    got = _pairs(key_links.propose(_tab_cols(), _objects(), rnd, probe=lambda *a: 100.0))
    for want in [("inst__inst_device", "mete_dev_type_code", "meta__meta_device_type", "dev_type_code"),
                 ("inst__inst_system", "meta_sys_type_code", "meta__meta_system_type", "sys_type_code"),
                 ("inst__inst_device", "loc_no", "inst__inst_location", "loc_no"),
                 ("inst__enterprise_system_rel", "ent_no", "user__enterprise", "ent_no"),
                 ("inst__enterprise_system_rel", "sys_no", "inst__inst_system", "sys_no"),
                 ("inst__inst_index", "index_code", "meta__meta_index", "index_code"),
                 ("inst__inst_index", "obj_no", "inst__inst_device", "dev_no"),
                 ("inst__inst_index", "obj_no", "inst__inst_system", "sys_no")]:
        assert want in got, want
    assert not any(c == "create_by" for _, c, _, _ in got)                      # 审计列不提
    assert not any(ct.startswith("biz__cfg") for ct, _, _, _ in got)           # 租户键不逐表提
    assert ("biz__bill", "ent_no", "user__enterprise", "ent_no") not in got
    assert not any(ct == pt for ct, _, pt, _ in got)


def test_key_links_older_round_can_point_to_new_round():
    """前一轮的关联表引用本轮才建的实例表,也要补上。"""
    got = _pairs(key_links.propose(_tab_cols(), _objects(), ["inst__inst_system"], probe=lambda *a: 100.0))
    assert ("inst__enterprise_system_rel", "sys_no", "inst__inst_system", "sys_no") in got
    assert ("inst__inst_system", "meta_sys_type_code", "meta__meta_system_type", "sys_type_code") in got


def test_key_links_skips_pairs_already_proposed_and_weak_polymorphic():
    props = key_links.propose(_tab_cols(), _objects(), ["inst__inst_index"],
                              existing={("inst_index", "meta_index")}, probe=lambda *a: 5.0)
    got = _pairs([p for p in props if not p.get("alternative")])
    assert not any(ct == "inst__inst_index" and pt == "meta__meta_index" for ct, _, pt, _ in got)
    alt = [p for p in props if p.get("alternative")]
    assert [(p["child_key"], p["parent_key"]) for p in alt] == [("index_code", "index_code")]
    assert not any(c == "obj_no" for _, c, _, _ in got)                         # 探测重叠太低的多态候选不提


# ── 多态引用:按判别值裁决,问数须带判别条件 ─────────────────────

def _poly_db(make_sqlite):
    return make_sqlite({
        "device": ("dev_no TEXT, name TEXT", [(f"D{i}", "d") for i in range(20)]),
        "system": ("sys_no TEXT, name TEXT", [(f"S{i}", "s") for i in range(10)]),
        "idx": ("id INTEGER, obj_type INTEGER, obj_no TEXT, v REAL",
                [(i, 0, f"D{i % 20}", 1.0) for i in range(40)] + [(100 + i, 1, f"S{i % 10}", 2.0) for i in range(20)]),
    })


def _poly_ir(make_sqlite, monkeypatch):
    db = _poly_db(make_sqlite)
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    extracted = {"objects": [{"name": "idx", "cn": "指标实例", "table": "idx", "kind": "ice"},
                             {"name": "device", "cn": "设备", "table": "device", "kind": "object"},
                             {"name": "system", "cn": "用能单元", "table": "system", "kind": "object"}],
                 "relations": [{"source": "idx", "target": "device", "verb": "测量", "child_key": "obj_no", "parent_key": "dev_no"},
                               {"source": "idx", "target": "system", "verb": "测量", "child_key": "obj_no", "parent_key": "sys_no"}]}
    ev = {"tab_cols": {"device": [("dev_no", "TEXT"), ("name", "TEXT")], "system": [("sys_no", "TEXT"), ("name", "TEXT")],
                       "idx": [("id", "INTEGER"), ("obj_type", "INTEGER"), ("obj_no", "TEXT"), ("v", "REAL")]},
          "schema": "", "docs": "", "n_docs": 0, "refs": []}
    return db, server._adjudicate_ir(db, "多态", json.loads(json.dumps(extracted)), ev)


def test_polymorphic_reference_verified_per_discriminator(make_sqlite, monkeypatch):
    _, ir = _poly_ir(make_sqlite, monkeypatch)
    rel = {r["target_concept"]: r for r in ir["relations"]}
    assert rel["device"]["status"] == "verified" and rel["system"]["status"] == "verified"
    assert rel["device"]["evidence"]["condition"] == {"column": "obj_type", "value": 0}
    assert rel["system"]["evidence"]["condition"] == {"column": "obj_type", "value": 1}


def test_polymorphic_condition_required_in_qa(make_sqlite, monkeypatch):
    _, ir = _poly_ir(make_sqlite, monkeypatch)
    hint = "\n".join(server._join_hints(ir, ["idx", "device"]))
    assert "idx.obj_type = 0" in hint
    sql = "SELECT d.name, COUNT(*) FROM idx i JOIN device d ON d.dev_no = i.obj_no"
    ok, why = server._validate_sql_ontology(sql + " GROUP BY 1", ir, strict=True)
    assert not ok and "obj_type" in why
    assert server._validate_sql_ontology(sql + " AND i.obj_type = 0 GROUP BY 1", ir, strict=True)[0]
    assert server._validate_sql_ontology(sql + " WHERE i.obj_type = 0 GROUP BY 1", ir, strict=True)[0]


def test_polymorphic_needs_discriminator_column(make_sqlite, monkeypatch):
    """没有判别列时,键名不同仍停在候选(不放宽一般命名规则)。"""
    db = make_sqlite({"device": ("dev_no TEXT", [(f"D{i}",) for i in range(20)]),
                      "log": ("id INTEGER, obj_no TEXT", [(i, f"D{i % 20}") for i in range(40)])})
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    extracted = {"objects": [{"name": "log", "table": "log", "kind": "ice"}, {"name": "device", "table": "device", "kind": "object"}],
                 "relations": [{"source": "log", "target": "device", "verb": "记录", "child_key": "obj_no", "parent_key": "dev_no"}]}
    ev = {"tab_cols": {"device": [("dev_no", "TEXT")], "log": [("id", "INTEGER"), ("obj_no", "TEXT")]},
          "schema": "", "docs": "", "n_docs": 0, "refs": []}
    ir = server._adjudicate_ir(db, "无判别列", extracted, ev)
    assert ir["relations"][0]["status"] == "candidate"


# ── 问数:维度词没有本体依据时如实提示 ─────────────────────────────

def _wip_ir():
    return {"objects": [{"name": "wip", "cn": "在制品过站明细", "table": "dwd_wip",
                         "attrs": [{"col": "track", "cn": ""}, {"col": "zone_sn", "cn": ""}]},
                        {"name": "area", "cn": "产线区域", "table": "dim_zone", "attrs": [{"col": "zone_name", "cn": "区域名称"}]}]}


def test_dimension_without_ontology_basis_is_flagged():
    sql = "SELECT track AS 线别, COUNT(*) AS 过站记录数 FROM dwd_wip GROUP BY track"
    warns = intent_check.dimension_check("各线别的过站记录数是多少", sql, _wip_ir())
    assert warns and warns[0]["term"] == "线别" and warns[0]["column"] == "track"


def test_dimension_with_basis_passes():
    ir = _wip_ir()
    ir["objects"][0]["attrs"][0]["cn"] = "线别"
    sql = "SELECT track AS 线别, COUNT(*) AS n FROM dwd_wip GROUP BY track"
    assert intent_check.dimension_check("各线别的过站记录数", sql, ir) == []
    sql2 = "SELECT a.zone_name AS 产线区域, COUNT(*) AS n FROM dwd_wip w JOIN dim_zone a ON a.id = w.zone_sn GROUP BY 1"
    assert intent_check.dimension_check("各产线区域的过站数", sql2, _wip_ir()) == []


# ── 构建体检:第三方工具表默认不进计划 ───────────────────────────

def test_tool_tables_excluded_from_plan(make_sqlite):
    import build_prep
    db = make_sqlite({"inst__jimu_report": ("id INTEGER, name TEXT", [(1, "r")]),
                      "inst__qrtz_triggers": ("id INTEGER", [(1,)]),
                      "inst__inst_device": ("id INTEGER, dev_no TEXT", [(1, "D1")])})
    prof = build_prep.profile(db)
    assert set(prof["tool_tables"]) == {"inst__jimu_report", "inst__qrtz_triggers"}
    assert "tool_tables" in {f["id"] for f in build_prep.findings(prof)}
    planned = {t for p in build_prep.plan(prof, exclude=prof["tool_tables"]) for t in p["tables"]}
    assert planned == {"inst__inst_device"}


def test_polymorphic_relation_passes_acceptance(make_sqlite, monkeypatch):
    import build_quality
    _, ir = _poly_ir(make_sqlite, monkeypatch)
    rep = build_quality.audit_verified_evidence(ir)
    assert rep["valid"], rep["issues"]


def test_key_links_ambiguous_and_generic_parents():
    tc = {"biz__work_order_notice_config": ["id", "title"], "biz__work_menu_config": ["id", "title"],
          "biz__work_order_notice_detail": ["id", "config_id"], "biz__dict_type": ["id", "name"],
          "biz__gateway_tab_node": ["id", "type_code"], "meta__meta_dict": ["id", "dict_key"],
          "meta__meta_tab_dict": ["id", "dict_key"], "inst__inst_survey_info": ["id", "dict_key"]}
    tc = {k: [(c, "TEXT") for c in v] for k, v in tc.items()}
    objs = [{"name": t.split("__")[-1], "table": t} for t in tc]
    got = _pairs(key_links.propose(tc, objs, list(tc)))
    assert ("biz__work_order_notice_detail", "config_id", "biz__work_order_notice_config", "id") in got
    assert not any(pt == "biz__work_menu_config" for _, _, pt, _ in got)
    assert not any(c == "type_code" for _, c, _, _ in got)
    assert not any(ct == "inst__inst_survey_info" for ct, _, _, _ in got)
    got2 = _pairs(key_links.propose(tc, objs, list(tc), used_keys={("biz__work_order_notice_detail", "config_id")}))
    assert not any(ct == "biz__work_order_notice_detail" for ct, _, _, _ in got2)


# ── 继续构建:同名对象两轮绑了不同的表 ─────────────────────────────

def _rel(s, t, status, ck="ent_no", pk="ent_no"):
    return {"source_concept": s, "target_concept": t, "status": status, "evidence": {"child_key": ck, "parent_key": pk}}


def test_merge_rebinds_object_to_table_matching_its_name():
    base = {"objects": [{"name": "enterprise", "cn": "企业", "table": "biz__enterprise_survey"},
                        {"name": "report", "table": "inst__enterprise_report"}],
            "relations": [_rel("report", "enterprise", "verified", "ent_no", "id")]}
    new = {"objects": [{"name": "enterprise", "cn": "企业", "table": "user__enterprise"},
                       {"name": "user_rel", "table": "user__enterprise_user_rel"}],
           "relations": [_rel("user_rel", "enterprise", "verified")]}
    out, stat = server._merge_ir(base, new)
    ent = next(o for o in out["objects"] if o["name"] == "enterprise")
    assert ent["table"] == "user__enterprise" and ent["rebound_from"] == "biz__enterprise_survey"
    rels = {(r["source_concept"], r["target_concept"]): r for r in out["relations"]}
    assert rels[("user_rel", "enterprise")]["status"] == "verified"
    assert rels[("report", "enterprise")]["status"] == "candidate"          # 按旧表取的证据作废,须重新取证
    assert stat["objects_rebound"] == 1


def test_merge_keeps_base_binding_when_new_is_not_better():
    base = {"objects": [{"name": "enterprise", "table": "user__enterprise"}], "relations": []}
    new = {"objects": [{"name": "enterprise", "table": "biz__enterprise_survey"}, {"name": "x", "table": "x"}],
           "relations": [_rel("x", "enterprise", "verified")]}
    out, _ = server._merge_ir(base, new)
    assert next(o for o in out["objects"] if o["name"] == "enterprise")["table"] == "user__enterprise"
    rel = next(r for r in out["relations"] if r["source_concept"] == "x")
    assert rel["status"] == "candidate"                                       # 本轮按另一张表取的证据不能沿用


def test_used_polymorphic_key_still_matched_to_other_parents():
    got = _pairs(key_links.propose(_tab_cols(), _objects(), ["inst__inst_index"], probe=lambda *a: 100.0,
                                   existing={("inst_index", "inst_system")},
                                   used_keys={("inst__inst_index", "obj_no")}))
    assert ("inst__inst_index", "obj_no", "inst__inst_device", "dev_no") in got


def test_alternative_keys_tried_when_model_keys_wrong(make_sqlite, monkeypatch):
    db = make_sqlite({"dtype": ("code TEXT, name TEXT, create_by TEXT", [(f"T{i}", "t", "u1") for i in range(5)]),
                      "dev": ("dev_no TEXT, dtype_code TEXT, create_by TEXT", [(f"D{i}", f"T{i % 5}", "u1") for i in range(20)])})
    monkeypatch.setattr(server, "_llm_semantic_review", lambda relations, ev: None)
    extracted = {"objects": [{"name": "dev", "table": "dev", "kind": "object"}, {"name": "dtype", "table": "dtype", "kind": "ice"}],
                 "relations": [{"source": "dev", "target": "dtype", "verb": "属于", "child_key": "create_by",
                                "parent_key": "create_by", "alt_keys": [["dtype_code", "code"]]}]}
    ev = {"tab_cols": {"dtype": [("code", "TEXT"), ("name", "TEXT"), ("create_by", "TEXT")],
                       "dev": [("dev_no", "TEXT"), ("dtype_code", "TEXT"), ("create_by", "TEXT")]},
          "schema": "", "docs": "", "n_docs": 0, "refs": []}
    ir = server._adjudicate_ir(db, "备选键", extracted, ev)
    rel = ir["relations"][0]
    assert rel["status"] == "verified" and rel["evidence"]["child_key"] == "dtype_code"

# -*- coding: utf-8 -*-
"""DR-064 概念层线索:两种场景数据共用一套方法。

- 多厂多层湖仓:同一实体按「层级_厂区_来源约定_词根_变体」复制成多张表 → 表族合并;
  厂区与来源前缀不写死,从表名学(后面接的表足够多、剩余部分可互换)。子类型前缀(fct_/aoi_)不能剥。
- 元数据驱动业务库:实例表按类别编码装着多种概念 → 类别拆分,名称来自目录表。
- 两种「无效行」:软删除与版本重复;重叠达标却因父键不唯一被拒的关系,按有效行复核。
"""
import json
import os
import re

import pytest

import build_prep
import concept_hints as ch
import skill_registry

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ENTITIES = ["work_order", "item_master", "line_zone", "proc_group", "pass_record", "pack_label", "repair_record",
            "handover_log", "dual_code_log", "proc_route", "prod_project", "bom_list"]


def _lakehouse_cols():
    cols = {}
    for f, conv in (("f1", "t"), ("f2", "t"), ("f3", "tb"), ("f4", "tb")):
        for e in ENTITIES:
            cols[f"ods_{f}_{conv}_{e}"] = ["id", "insert_time", "dt", f"{e}_key", f"{e}_name"]
        for sub in ("fct", "aoi", "ict"):
            cols[f"dwd_{f}_{sub}_factor_detail"] = ["id", "dt", "project_id", f"{sub}_value"]
    cols["dwd_work_order"] = ["id", "insert_time", "dt", "work_order_key", "work_order_name"]
    cols["dwd_f1_work_order_offline"] = ["id", "insert_time", "dt", "work_order_key"]
    cols["ods_f1_t_item_master_bak20241216"] = ["id", "insert_time", "dt", "item_master_key"]
    return cols


class TestTableFamilies:
    def test_factory_and_source_prefixes_are_learned_not_hardcoded(self):
        res = ch.table_families(_lakehouse_cols())
        assert {"f1", "f2", "f3", "t", "tb"} <= set(res["prefixes"])
        fams = {f["core"]: f for f in res["families"]}
        # 两种命名约定(t_/tb_)、贴源与明细层、离线副本与备份都归到同一个概念
        assert set(fams["work_order"]["members"]) >= {"ods_f1_t_work_order", "ods_f3_tb_work_order",
                                                       "dwd_work_order", "dwd_f1_work_order_offline"}
        assert "ods_f1_t_item_master_bak20241216" in fams["item_master"]["members"]

    def test_subtype_prefixes_keep_different_tests_apart(self):
        fams = {f["core"] for f in ch.table_families(_lakehouse_cols())["families"]}
        assert {"fct_factor_detail", "aoi_factor_detail", "ict_factor_detail"} <= fams
        assert "factor_detail" not in fams

    def test_representative_prefers_the_clean_detail_layer_table(self):
        fams = {f["core"]: f for f in ch.table_families(_lakehouse_cols())["families"]}
        assert fams["work_order"]["representative"] == "dwd_work_order"

    def test_business_tail_words_are_not_stripped(self):
        cols = {"ems__label": ["id", "name"], "ems__label_obj": ["id", "label_id", "obj_no"],
                "inst__device": ["id"], "inst__device_rel": ["id", "dev_no"]}
        assert not ch.table_families(cols)["families"]

    def test_backups_and_cross_db_shells_join_their_table(self):
        cols = {"a__alarm_log": ["id", "msg"], "a__alarm_log_2025": ["id", "msg"],
                "x__price_groups": ["id", "energy_type"], "y__price_groups": ["id", "energy_type"]}
        cores = {f["core"] for f in ch.table_families(cols)["families"]}
        assert cores == {"alarm_log", "price_groups"}


def _meta_db(make_sqlite):
    devices = [(i, f"D{i}", "EMTR" if i < 40 else "IV") for i in range(50)]
    return make_sqlite({
        "meta_device_type": ("id INTEGER, dev_type_code TEXT, dev_type_name TEXT, is_del INTEGER",
                             [(1, "EMTR", "电能表", 0), (2, "IV", "光伏逆变器", 0), (3, "EMTR", "电能表", 1)]),
        "inst_device": ("id INTEGER, dev_no TEXT, mete_dev_type_code TEXT", devices),
        "inst_device_copy": ("id INTEGER, dev_no TEXT, mete_dev_type_code TEXT", devices[:5]),
        "strategy_time": ("id INTEGER, strategy_type TEXT, strategy_name TEXT", [(1, "1", "策略"), (2, "0", "策略")]),
        "iot_box": ("id INTEGER, strategy_type TEXT", [(i, str(i % 2)) for i in range(10)]),
        "work_order": ("id INTEGER, work_order_no TEXT, create_time TEXT",
                       [(i, f"MO{i % 98}", f"2024-12-{1 + i % 28:02d}") for i in range(100)]),
        "mo_item_rel": ("id INTEGER, work_order_no TEXT, create_time TEXT",
                        [(i, f"MO{i % 97}", "2024-12-01") for i in range(100)]),
    })


class TestCategorySplitsAndVersions:
    def test_instance_table_split_by_catalog_names(self, make_sqlite):
        import sqlite3
        db = _meta_db(make_sqlite)
        con = sqlite3.connect(db)
        cols = {t: [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')] for (t,) in
                con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        rows = {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in cols}
        splits = ch.category_splits(con, cols, rows)
        dev = next(s for s in splits if s["table"] == "inst_device")
        assert dev["catalog"] == "meta_device_type"
        assert [(c["code"], c["name"], c["rows"]) for c in dev["categories"]] == [("EMTR", "电能表", 40), ("IV", "光伏逆变器", 10)]
        # 名称全一样的「目录」、实例表自己的副本都不算类别目录
        assert not any(s["table"] == "iot_box" for s in splits)
        assert not any(s["catalog"] == "inst_device_copy" for s in splits)

    def test_version_duplicates_only_on_the_entitys_own_key(self, make_sqlite):
        import sqlite3
        db = _meta_db(make_sqlite)
        con = sqlite3.connect(db)
        cols = {t: [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')] for t in ("work_order", "mo_item_rel")}
        vd = ch.version_duplicates(con, cols, {"work_order": 100, "mo_item_rel": 100})
        assert [(v["table"], v["key"], v["duplicate_rows"]) for v in vd] == [("work_order", "work_order_no", 2)]

    def test_collect_prompt_text_and_scope(self, make_sqlite):
        hints = ch.collect(_meta_db(make_sqlite))
        text = ch.text_for_prompt(hints, scope=["inst_device", "work_order"])
        assert "inst_device.mete_dev_type_code" in text and "EMTR=电能表(40)" in text
        assert "work_order.work_order_no" in text
        assert ch.text_for_prompt(hints, scope=["unrelated"]) == ""
        assert "类别拆分" in ch.summary_text(hints) or "按类别拆分" in ch.summary_text(hints)


def test_attach_families_binds_members_and_flags_duplicates():
    hints = ch.table_families(_lakehouse_cols())
    ir = {"objects": [{"name": "工单", "table": "dwd_work_order"},
                      {"name": "工单副本", "table": "ods_f2_t_work_order"},
                      {"name": "其他", "table": "nothing"}]}
    st = ch.attach_families(ir, hints)
    assert st == {"objects": 1, "duplicates": 1}
    assert ir["objects"][0]["tables"][0] == "dwd_work_order" and "ods_f3_tb_work_order" in ir["objects"][0]["tables"]
    assert ir["objects"][1]["evidence"]["family_duplicate_of"] == "工单"


class TestPreflight:
    def test_plan_puts_only_representatives_in_rounds(self, make_sqlite):
        cols = _lakehouse_cols()
        db = make_sqlite({t: (", ".join(f"{c} TEXT" for c in cs), []) for t, cs in cols.items()})
        rep = build_prep.check(db)
        assert rep["concept"]["families"] >= len(ENTITIES)
        planned = [t for r in rep["plan"] for t in r["tables"]]
        riders = [m for r in rep["plan"] for ms in (r.get("family_members") or {}).values() for m in ms]
        assert "dwd_work_order" in planned and "ods_f1_t_work_order" in riders
        assert sorted(planned + riders + rep["excluded"]) == sorted(cols)
        assert any(f["id"] == "table_families" for f in rep["findings"])
        assert rep["scenario"][0]["id"] == "lakehouse"

    def test_domain_suggestions_are_proportional(self, make_sqlite):
        """制造库里零星几张计量表不该建议能源参照;PCBA 信号建议电子制造技能。"""
        cols = _lakehouse_cols()
        cols.update({f"ods_f1_t_meter_{w}": ["id", "energy_value"] for w in ("aa", "bb", "cc")})
        cols.update({f"ods_f1_t_{a}{b}_ledger": ["id", "note"] for a in "pqrstlmnoh" for b in "uvwxyzjk"})
        db = make_sqlite({t: (", ".join(f"{c} TEXT" for c in cs), []) for t, cs in cols.items()})
        refs = {r["industry"]: r for r in build_prep.check(db)["references"]}
        assert refs["pcba"]["skill"] == "electronics-manufacturing-ontology"
        assert "energy_management" not in refs

    def test_metadata_driven_scenario(self, make_sqlite):
        rep = build_prep.check(_meta_db(make_sqlite))
        assert any(k["id"] == "metadata_driven" for k in rep["scenario"])
        assert any(f["id"] == "category_splits" for f in rep["findings"])
        assert any(f["id"] == "version_duplicates" for f in rep["findings"])


def test_cross_check_accepts_relations_after_valid_rows(make_sqlite):
    """父键因版本重复不唯一而被拒:按键取最新后唯一,标为「有效行成立」。"""
    import importlib.util
    path = os.path.join(ROOT, "skills_seed", "ontology-cross-check", "scripts", "cross_check.py")
    spec = importlib.util.spec_from_file_location("cc_vr", path)
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    db = _meta_db(make_sqlite)
    ir = {"objects": [{"name": "rel", "table": "mo_item_rel"}, {"name": "mo", "table": "work_order"}],
          "relations": [{"source_concept": "rel", "target_concept": "mo", "status": "candidate",
                         "evidence": {"child_key": "work_order_no", "parent_key": "work_order_no",
                                      "overlap": 100.0, "parent_unique": False}}]}
    row = cc.check(ir, cc.Db(db))[0][0]
    assert row["verdict"] == "valid_rows_candidate"
    assert row["channels"]["valid_rows"]["rule"] == "keep_latest"


class TestSkills:
    def _items(self):
        return {i["name"]: i for i in skill_registry.discover([os.path.join(ROOT, "skills_seed")])}

    @pytest.mark.parametrize("name", ["ontology-build-method", "electronics-manufacturing-ontology"])
    def test_new_skills_are_bounded_and_neutral(self, name):
        it = self._items()[name]
        body = skill_registry.skill_body(open(it["skill_md"], encoding="utf-8").read())
        assert it["meta"]["license"] == "Apache-2.0" and len(body) <= 3500
        assert not re.search(r"\b[a-z]{2,4}_(t|tb)_|\b\d{1,3}(\.\d{1,3}){3}\b|https?://|paimon", body)

    def test_method_covers_both_scenarios(self):
        body = skill_registry.skill_body(open(self._items()["ontology-build-method"]["skill_md"], encoding="utf-8").read())
        for rule in ("多表一概念", "一表多概念", "表族", "类别", "有效行", "版本重复", "软删除", "类型对齐", "时点对齐"):
            assert rule in body, rule

    def test_all_selected_skills_fit_the_prompt_budget(self):
        roots = [os.path.join(ROOT, "skills_seed")]
        names = ["ontology-semi-auto", "ontology-build-method", "ontology-cross-check", "energy-carbon-ontology"]
        text, used = skill_registry.method_text(names, roots)
        assert used == names and len(text) < 12000


def test_build_attaches_family_members_and_reports_hints(make_sqlite, tmp_path, monkeypatch):
    server = pytest.importorskip("server")
    w = tmp_path / "work"; w.mkdir()
    monkeypatch.setattr(server, "WORK", str(w))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(w / "build_connections.json"))
    cols = _lakehouse_cols()
    db = make_sqlite({t: (", ".join(f"{c} TEXT" for c in cs), [tuple("x" for _ in cs)]) for t, cs in cols.items()})
    (w / "build_connections.json").write_text(json.dumps([{"id": "conn_l", "name": "湖仓", "kind": "sqlite", "path": db}]),
                                              encoding="utf-8")
    monkeypatch.setattr(server, "_llm_extract_ontology", lambda *a, **k: None)
    monkeypatch.setattr(server, "_build_intent", lambda *a, **k: {})
    monkeypatch.setattr(server, "_build_summary", lambda *a, **k: "摘要")
    body = server.app.test_client().post("/api/build/inquire", json={
        "q": "构建制造本体", "source": "conn_l", "name": "湖仓", "skills": ["ontology-semi-auto"]}).get_data(as_text=True)
    evs = [json.loads(x[6:]) for x in body.splitlines() if x.startswith("data: ")]
    steps = {e["step"]: e for e in evs if e.get("type") == "step"}
    assert "表族" in steps["concept_hints"]["info"]
    assert steps["concept_families"]["ok"]
    done = next(e for e in evs if e.get("type") == "done")
    ir = server.load_ir(done["graph_key"])
    fam = [o for o in ir["objects"] if (o.get("evidence") or {}).get("family")]
    assert fam and all(len(o["tables"]) >= 2 for o in fam)

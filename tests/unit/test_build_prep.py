# -*- coding: utf-8 -*-
"""构建体检:在构建前识别会让验证失败的建模规律,并给出分轮计划。

规则都来自能碳平台 5 库(594 表)的实测:没有声明外键、软删除让键看起来不唯一、
多态引用只能部分重叠、审计列被当成关系、演示/历史分表混入、单轮表太多只覆盖一半、
枢纽层后建导致跨轮关系无处挂。
"""
import build_prep as bp


def _db(make_sqlite, extra=None):
    tables = {
        "meta__meta_device_type": ("id INTEGER PRIMARY KEY, dev_type_code TEXT, is_del INTEGER",
                                   [(1, "METE", 0), (2, "METE", 1), (3, "FMT", 1)]),
        "user__enterprise": ("id INTEGER PRIMARY KEY, ent_no TEXT, create_by TEXT", [(1, "E1", "u1")]),
        "inst__inst_metric": ("id INTEGER PRIMARY KEY, obj_type INTEGER, obj_no TEXT, ent_no TEXT, update_by TEXT",
                              [(1, 0, "DEV_1", "E1", "u"), (2, 1, "SYS_1", "E1", "u")]),
        "ems__alarm_notice_history": ("id INTEGER PRIMARY KEY, ent_no TEXT", [(1, "E1")]),
        "ems__alarm_log_2025": ("id INTEGER PRIMARY KEY, ent_no TEXT", []),
        "ems__meter_type_copy1": ("id INTEGER PRIMARY KEY", []),
        "inst__tmp_report_data": ("id INTEGER PRIMARY KEY", []),
        "ems__energy_bill": ("id INTEGER PRIMARY KEY, ent_no TEXT, energy_use REAL", [(1, "E1", 2.0)]),
        "ems__meter_reading": ("id INTEGER PRIMARY KEY, ent_no TEXT", [(1, "E1")]),
        "ems__energy_price": ("id INTEGER PRIMARY KEY, ent_no TEXT", [(1, "E1")]),
    }
    tables.update(extra or {})
    return make_sqlite(tables)


class TestProfile:
    def test_soft_delete_detected_with_deleted_share(self, make_sqlite):
        prof = bp.profile(_db(make_sqlite))
        sd = {s["table"]: s for s in prof["soft_delete"]}
        assert sd["meta__meta_device_type"]["column"] == "is_del"
        assert sd["meta__meta_device_type"]["deleted_pct"] == 66.7

    def test_polymorphic_reference_detected_with_discriminator_values(self, make_sqlite):
        poly = bp.profile(_db(make_sqlite))["polymorphic"]
        assert poly and poly[0]["table"] == "inst__inst_metric"
        assert poly[0]["discriminator"] == "obj_type" and poly[0]["keys"] == ["obj_no"]
        assert set(poly[0]["values"]) == {0, 1}

    def test_audit_columns_detected(self, make_sqlite):
        cols = {(a["table"], a["column"]) for a in bp.profile(_db(make_sqlite))["audit_columns"]}
        assert ("user__enterprise", "create_by") in cols and ("inst__inst_metric", "update_by") in cols

    def test_history_business_table_is_not_a_shard(self, make_sqlite):
        """「告警通知历史」是正常业务表;只有年份后缀、备份、副本才算分表。"""
        prof = bp.profile(_db(make_sqlite))
        assert "ems__alarm_notice_history" not in prof["shard_tables"]
        assert {"ems__alarm_log_2025", "ems__meter_type_copy1"} <= set(prof["shard_tables"])
        assert "inst__tmp_report_data" in prof["scratch_tables"]

    def test_hub_key_counts(self, make_sqlite):
        hubs = {h["key"]: h["tables"] for h in bp.profile(_db(make_sqlite))["hub_keys"]}
        assert hubs["ent_no"] >= 5

    def test_sample_metadata_tables_are_not_business_tables(self, make_sqlite):
        db = _db(make_sqlite, {"_dm_tables": ("table_name TEXT, source_table TEXT, comment TEXT, rows_est INTEGER",
                                              [("user__enterprise", "enterprise", "企业信息", 1)]),
                               "_dm_columns": ("table_name TEXT, column_name TEXT, type TEXT, comment TEXT",
                                               [("user__enterprise", "ent_no", "varchar", "企业编号")])})
        prof = bp.profile(db)
        assert "_dm_tables" not in prof["_cols"] and prof["has_comments"]
        assert prof["_comments"]["user__enterprise"] == "企业信息"


class TestReferences:
    def test_energy_and_carbon_are_suggested_separately(self, make_sqlite):
        """能源信号建议能源管理;没有碳排放表时,碳排放单独给出「分开建」的建议。"""
        refs = bp.suggest_references(bp.profile(_db(make_sqlite, {
            "ems__energy_indexes": ("id INTEGER PRIMARY KEY, carbon TEXT", [(1, "EelecCE")])})))
        by = {r["industry"]: r for r in refs}
        assert "energy_management" in by
        assert by["carbon_accounting"].get("separate") is True

    def test_carbon_tables_get_direct_suggestion(self, make_sqlite):
        refs = bp.suggest_references(bp.profile(_db(make_sqlite, {
            "ems__carbon_emission": ("id INTEGER PRIMARY KEY, co2e REAL", [(1, 1.0)])})))
        assert any(r["industry"] == "carbon_accounting" and not r.get("separate") for r in refs)


class TestDocCoverage:
    def test_described_missing_and_doc_only(self):
        doc = ("# 设计文档\n### 3.1 enterprise —— 企业\n| ent_no | 企业编号 |\n"
               "### 3.2 legacy_table —— 已下线的表\n| id | 主键 |")
        cov = bp.doc_coverage(["user__enterprise", "ems__energy_bill"], [("d.md", doc)])
        assert cov["described"] == 1 and cov["missing"] == ["ems__energy_bill"]
        assert cov["doc_only"] == ["legacy_table"]

    def test_no_documents(self):
        assert bp.doc_coverage(["a"], [])["files"] == 0


class TestPlan:
    def test_type_and_org_layers_come_first_and_rounds_are_bounded(self, make_sqlite):
        prof = bp.profile(_db(make_sqlite))
        rounds = bp.plan(prof, max_tables=3)
        assert all(r["count"] <= 3 for r in rounds)
        first = rounds[0]["tables"]
        assert "meta__meta_device_type" in first and "user__enterprise" in first

    def test_excluded_tables_are_left_out(self, make_sqlite):
        prof = bp.profile(_db(make_sqlite))
        rounds = bp.plan(prof, max_tables=50, exclude=["ems__alarm_log_2025"])
        assert "ems__alarm_log_2025" not in [t for r in rounds for t in r["tables"]]

    def test_every_table_appears_exactly_once(self, make_sqlite):
        prof = bp.profile(_db(make_sqlite))
        all_t = [t for r in bp.plan(prof, max_tables=2) for t in r["tables"]]
        assert sorted(all_t) == sorted(prof["_cols"]) and len(all_t) == len(set(all_t))


def test_check_report_shape_and_default_exclusions(make_sqlite):
    rep = bp.check(_db(make_sqlite), [("d.md", "### enterprise —— 企业\n| a | b |")], max_tables=4)
    assert rep["summary"]["tables"] == 10 and rep["summary"]["declared_fks"] == 0
    assert {"no_fk", "soft_delete", "polymorphic", "audit", "noise_tables"} <= {f["id"] for f in rep["findings"]}
    assert set(rep["excluded"]) == {"ems__alarm_log_2025", "ems__meter_type_copy1", "inst__tmp_report_data"}
    planned = [t for r in rep["plan"] for t in r["tables"]]
    assert not set(planned) & set(rep["excluded"])
    assert "_cols" not in rep and rep["docs"]["described"] == 1


def test_database_prefix_is_not_an_energy_signal(make_sqlite):
    """多库合并时表名带「库名__」前缀;库名 energy_org 不能让每张表都算作能源相关。"""
    prof = bp.profile(make_sqlite({
        "energy_org__enterprise": ("id INTEGER PRIMARY KEY", []),
        "energy_org__roles_es": ("id INTEGER PRIMARY KEY", []),
        "ems__es_report": ("id INTEGER PRIMARY KEY", []),
    }))
    assert prof["energy"]["tables"] == ["ems__es_report"]

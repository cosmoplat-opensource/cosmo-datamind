# -*- coding: utf-8 -*-
"""drift_check 单测(DR-025)—— 本体与数据源一致性:表/列/主键/关系键缺失。"""
import drift_check


def test_detects_missing_table(ir_healthy, make_sqlite):
    # 库里只有 employees,departments 缺失 → 漂移
    db = make_sqlite({"employees": ("employee_id INTEGER PRIMARY KEY, name TEXT", [(1, "A")])})
    res = drift_check.check(ir_healthy, db)
    assert res["healthy"] is False
    assert res["counts"].get("table_missing", 0) >= 1
    assert any(i["type"] == "table_missing" and i["object"] == "dept" for i in res["issues"])


def test_healthy_when_schema_matches(ir_healthy, make_sqlite):
    db = make_sqlite({
        "employees": ("employee_id INTEGER PRIMARY KEY, name TEXT", [(1, "A")]),
        "departments": ("dept_id INTEGER PRIMARY KEY, name TEXT", [(1, "HR")]),
    })
    res = drift_check.check(ir_healthy, db)
    # 两表都在 → 表级无缺失(关系键校验另计,此处只断言 table_missing=0)
    assert res["counts"].get("table_missing", 0) == 0


def test_reports_unreadable_database_and_empty_report(tmp_path):
    bad = drift_check.check({"objects": []}, str(tmp_path / "missing.db"))
    assert bad["checked"] is False
    assert "数据源不可读" in bad["error"]


def test_detects_column_pk_and_relation_drift_with_links(make_sqlite):
    db = make_sqlite({"Employees": ("employee_id INTEGER PRIMARY KEY, name TEXT", [(1, "A")])})
    ir = {
        "objects": [
            {"id": "concept_only", "cn": "纯概念"},
            {"id": "emp", "cn": "员工", "table": "employees", "pk": "missing_pk",
             "attrs": [{"col": ""}, {"col": "missing_attr", "cn": "遗失字段"}]},
            {"name": "dept", "table": "missing_departments", "pk": "dept_id"},
        ],
        "links": [
            {"source": "emp", "target": "dept", "verb": "belongs_to",
             "evidence": {"child_key": "missing_fk", "parent_key": "dept_id"}},
            {"source": "emp", "target": "concept_only", "verb": "mentions"},
        ],
    }
    report = drift_check.check(ir, db)
    assert report["checked"] is True
    assert report["healthy"] is False
    assert report["counts"] == {
        "pk_missing": 1,
        "column_missing": 1,
        "table_missing": 1,
        "relation_broken": 1,
    }
    assert report["scanned"] == {"objects_bound": 2, "columns": 1, "relations": 2}
    assert report["consistency"] < 100
    gaps = drift_check.gaps_from(report)
    assert len(gaps) == 4
    assert all(gap["type"].startswith("drift_") for gap in gaps)


def test_empty_ir_is_healthy_and_consistent(make_sqlite):
    db = make_sqlite({})
    report = drift_check.check({"objects": [], "relations": []}, db)
    assert report["healthy"] is True
    assert report["consistency"] == 100.0
    assert drift_check.gaps_from({}) == []

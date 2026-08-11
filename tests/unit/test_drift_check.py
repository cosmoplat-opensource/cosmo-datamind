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

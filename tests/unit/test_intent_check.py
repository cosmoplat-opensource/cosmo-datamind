# -*- coding: utf-8 -*-
"""intent_check 单测(DR-026)—— 双盲意图检测:两通道各判后比对。"""
import intent_check


def test_aligned_when_sql_covers_question(ir_healthy):
    res = intent_check.cross_check("员工分布在哪些部门",
                                   "SELECT * FROM employees JOIN departments ON 1=1", ir_healthy)
    assert res["verdict"] == "aligned"
    assert res["overlap"] == 100.0


def test_mismatch_when_sql_hits_unrelated_table(ir_healthy):
    # 问句锚到 员工/部门,但 SQL 查了不相干的表 → 答非所问
    res = intent_check.cross_check("员工有多少", "SELECT count(*) FROM departments_xyz", ir_healthy)
    # 问句只锚到「员工」,SQL 未覆盖 → mismatch
    res2 = intent_check.cross_check("员工有多少", "SELECT count(*) FROM some_other", ir_healthy)
    assert res2["verdict"] == "mismatch"


def test_partial_when_one_dimension_missing(ir_healthy):
    res = intent_check.cross_check("员工和部门的关系",
                                   "SELECT * FROM employees", ir_healthy)
    assert res["verdict"] == "partial"
    assert any(m["key"] == "dept" for m in res["missed"])


def test_unknown_when_question_anchors_nothing(ir_healthy):
    res = intent_check.cross_check("今天天气如何", "SELECT 1", ir_healthy)
    assert res["verdict"] == "unknown"


def test_actual_intent_ignores_cte(ir_healthy):
    # CTE 名不能被当成真实表(否则 SQL「查了什么」失真)
    sql = "WITH employees AS (SELECT 1) SELECT * FROM departments"
    b = intent_check.actual_intent(sql, ir_healthy)
    assert "employees" not in b["tables"]
    assert "departments" in b["tables"]


def test_step_of_marks_mismatch_not_ok(ir_healthy):
    res = intent_check.cross_check("员工有多少", "SELECT * FROM some_other", ir_healthy)
    step = intent_check.step_of(res)
    assert step["ok"] is False
    assert step["step"] == "intent_crosscheck"

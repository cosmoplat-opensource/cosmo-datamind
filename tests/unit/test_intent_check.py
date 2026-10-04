# -*- coding: utf-8 -*-
"""intent_check 单测(DR-026)—— 双盲意图检测:两通道各判后比对。"""
import intent_check


def test_aligned_when_sql_covers_question(ir_healthy):
    res = intent_check.cross_check("员工分布在哪些部门",
                                   "SELECT * FROM employees JOIN departments ON 1=1", ir_healthy)
    assert res["verdict"] == "aligned"
    assert res["overlap"] == 100.0


def test_mismatch_when_sql_hits_unrelated_table(ir_healthy):
    # 问句锚到「员工」,SQL 查了不相干的表 → 答非所问
    res = intent_check.cross_check("员工有多少", "SELECT count(*) FROM some_other", ir_healthy)
    assert res["verdict"] == "mismatch"


def test_near_miss_table_name_is_not_fuzzy_matched(ir_healthy):
    """表名须精确匹配:`departments_xyz` 不得被当作 `departments`。
    若这里放宽成子串/模糊匹配,通道 B 会把没查的对象算成查了,双盲比对随之失真。"""
    res = intent_check.cross_check("员工有多少", "SELECT count(*) FROM departments_xyz", ir_healthy)
    assert res["actual"]["tables"] == ["departments_xyz"]
    assert res["actual"]["objects"] == []          # 未映射到任何本体对象
    assert res["verdict"] == "mismatch"


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


def test_object_name_inside_a_filter_value_is_not_a_declared_object():
    """「设备故障导致的停机总时长」里的「设备」是停机原因的取值「设备故障」的一部分,不是在问设备。
    SQL 按 reason_name='设备故障' 过滤、没有 JOIN 设备表时,不能判为答非所问(DR-077)。"""
    ir = {"objects": [{"id": "eq", "cn": "设备", "table": "dim_equipment"},
                      {"id": "dt", "cn": "停机记录", "table": "fact_downtime"},
                      {"id": "rs", "cn": "停机原因", "table": "dim_downtime_reason"}]}
    sql = ("SELECT SUM(d.duration_hours) FROM fact_downtime d JOIN dim_downtime_reason r "
           "ON d.reason_code = r.reason_id WHERE r.reason_name = '设备故障'")
    res = intent_check.cross_check("设备故障导致的停机总时长是多少小时?", sql, ir)
    assert res["verdict"] != "mismatch"
    # 问句另外单独提到设备时照常比对
    res2 = intent_check.cross_check("各设备因设备故障导致的停机时长?", sql, ir)
    assert res2["verdict"] == "mismatch"


def test_table_linked_by_verified_relation_is_not_off_topic():
    """「参加过 7 次以上活动的学生」:SQL 用考勤表(经已验证关系指向活动)计数,没有 JOIN 活动表,
    不能判为答非所问(BIRD 对比实验中被拒答,DR-080)。与问句对象没有关系的表仍判 mismatch。"""
    ir = {"objects": [{"name": "event", "cn": "活动", "table": "event"},
                      {"name": "attendance", "cn": "出勤", "table": "attendance"},
                      {"name": "member", "cn": "成员", "table": "member"},
                      {"name": "budget", "cn": "预算", "table": "budget"}],
          "relations": [{"source_concept": "attendance", "target_concept": "event", "status": "verified"},
                        {"source_concept": "attendance", "target_concept": "member", "status": "verified"}]}
    res = intent_check.cross_check("哪些人参加过 7 次以上活动",
                                   "SELECT m.name FROM member m JOIN attendance a ON a.member_id = m.id GROUP BY m.id HAVING COUNT(*) > 7", ir)
    assert res["verdict"] == "aligned" or res["verdict"] == "partial"
    res2 = intent_check.cross_check("活动有哪些", "SELECT * FROM budget", ir)
    assert res2["verdict"] == "mismatch"

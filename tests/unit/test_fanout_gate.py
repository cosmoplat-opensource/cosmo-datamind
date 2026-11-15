# -*- coding: utf-8 -*-
"""扇出关卡(DR-055):1:N JOIN 后对父侧列求和/求均/计数即拦截;COUNT(DISTINCT)、MIN/MAX、子表列不拦。"""
import server

IR = {
    "objects": [{"id": "order", "table": "fact_sales_order"}, {"id": "customer", "table": "dim_customer"}],
    "links": [{"source": "order", "target": "customer", "status": "verified",
               "evidence": {"child_key": "cust_id", "parent_key": "cust_id"}}],
}


def _v(sql):
    return server._validate_sql_ontology(sql, IR, strict=True)


def test_parent_side_sum_is_blocked_with_actionable_reason():
    ok, why = _v("SELECT c.region, SUM(c.credit_limit) FROM fact_sales_order o "
                 "JOIN dim_customer c ON o.cust_id = c.cust_id GROUP BY 1")
    assert not ok and "扇出风险" in why and "dim_customer" in why and "COUNT(DISTINCT" in why


def test_child_side_sum_passes():
    ok, why = _v("SELECT c.region, SUM(o.amount) FROM fact_sales_order o "
                 "JOIN dim_customer c ON o.cust_id = c.cust_id GROUP BY 1")
    assert ok, why


def test_count_distinct_and_minmax_on_parent_pass():
    ok, _ = _v("SELECT COUNT(DISTINCT c.cust_id), MAX(c.credit_limit) FROM fact_sales_order o "
               "JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert ok


def test_reversed_join_order_still_detects_parent():
    ok, why = _v("SELECT AVG(c.credit_limit) FROM dim_customer c "
                 "JOIN fact_sales_order o ON c.cust_id = o.cust_id")
    assert not ok and "扇出风险" in why


def test_unqualified_columns_are_not_guessed(monkeypatch):
    monkeypatch.setattr(server, "_qa_db", lambda: "/nonexistent/none.db")      # 查不到列属于哪张表时不猜
    ok, _ = _v("SELECT SUM(credit_limit) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert ok


# ── 两张子表同时关联(DR-130):一张工单三条工序、两条设备占用,关联后工序行被占用行数放大 ────────

PLANT = {
    "objects": [{"id": "order", "table": "orders"}, {"id": "task", "table": "tasks"}, {"id": "occ", "table": "occupancy"}],
    "links": [{"source": "task", "target": "order", "status": "verified", "evidence": {"child_key": "order_no", "parent_key": "order_no"}},
              {"source": "occ", "target": "order", "status": "verified", "evidence": {"child_key": "order_no", "parent_key": "order_no"}}],
}


def _p(sql):
    return server._validate_sql_ontology(sql, PLANT, strict=True)


def test_two_child_tables_of_one_parent_multiply_each_other():
    ok, why = _p("SELECT o.order_no, SUM(t.hours) FROM orders o JOIN tasks t ON t.order_no = o.order_no "
                 "JOIN occupancy q ON q.order_no = o.order_no GROUP BY 1")
    assert not ok and "扇出风险" in why and "tasks" in why and "occupancy" in why and "分别" in why


def test_two_child_tables_joined_directly_on_the_shared_key_are_many_to_many():
    ok, why = _p("SELECT SUM(q.minutes) FROM tasks t JOIN occupancy q ON t.order_no = q.order_no")
    assert not ok and "扇出风险" in why and "多对多" in why


def test_row_counts_across_two_child_tables_are_blocked_but_distinct_counts_pass():
    base = "FROM orders o JOIN tasks t ON t.order_no = o.order_no JOIN occupancy q ON q.order_no = o.order_no"
    ok, why = _p("SELECT COUNT(*) " + base)
    assert not ok and "扇出风险" in why
    assert _p("SELECT COUNT(DISTINCT o.order_no), COUNT(DISTINCT t.task_id), MAX(q.minutes) " + base)[0]


def test_one_child_table_alone_is_still_fine():
    ok, why = _p("SELECT o.order_no, SUM(t.hours), COUNT(*) FROM orders o JOIN tasks t ON t.order_no = o.order_no GROUP BY 1")
    assert ok, why


def test_pre_aggregated_children_pass():
    ok, why = _p("WITH th AS (SELECT order_no, SUM(hours) h FROM tasks GROUP BY order_no), "
                 "qm AS (SELECT order_no, SUM(minutes) m FROM occupancy GROUP BY order_no) "
                 "SELECT o.order_no, th.h, qm.m FROM orders o LEFT JOIN th ON th.order_no = o.order_no "
                 "LEFT JOIN qm ON qm.order_no = o.order_no")
    assert ok, why


def test_an_expression_over_a_parent_column_is_also_blocked():
    ok, why = _v("SELECT SUM(c.credit_limit * 1.0) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert not ok and "扇出风险" in why
    assert _v("SELECT SUM(o.amount * 1.0), SUM(CASE WHEN o.amount > 0 THEN o.amount END) FROM fact_sales_order o "
              "JOIN dim_customer c ON o.cust_id = c.cust_id")[0]


def test_an_unqualified_column_is_attributed_when_only_one_table_has_it(make_sqlite, monkeypatch):
    db = make_sqlite({"fact_sales_order": ("order_no TEXT, cust_id TEXT, amount REAL", [("S1", "C1", 10.0)]),
                      "dim_customer": ("cust_id TEXT PRIMARY KEY, credit_limit REAL", [("C1", 500.0)])})
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    ok, why = _v("SELECT SUM(credit_limit) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert not ok and "扇出风险" in why and "credit_limit" in why
    assert _v("SELECT SUM(amount) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")[0]
    assert _v("SELECT COUNT(cust_id) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")[0]   # 两张表都有:不猜

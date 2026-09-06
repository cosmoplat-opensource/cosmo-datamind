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


def test_unqualified_columns_are_not_guessed():
    ok, _ = _v("SELECT SUM(credit_limit) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert ok

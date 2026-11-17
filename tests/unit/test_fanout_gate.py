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


# ── 不该拦的写法(DR-131):父表的列只是条件或逐行乘数;子查询各算各的;半连接不产生重复行 ─────────

def test_parent_column_used_only_as_a_condition_or_multiplier_is_not_blocked():
    base = " FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id"
    assert _v("SELECT SUM(CASE WHEN c.region = '华东' THEN o.amount ELSE 0 END)" + base)[0]
    assert _v("SELECT SUM(o.qty * c.discount_rate)" + base)[0]
    ok, why = _v("SELECT SUM(CASE WHEN c.region = '华东' THEN c.credit_limit END)" + base)   # 只有父表的列,仍然拦
    assert not ok and "扇出风险" in why


def test_each_query_block_is_checked_against_its_own_joins():
    ok, why = _v("WITH tot AS (SELECT SUM(c.credit_limit) AS s FROM dim_customer c) "
                 "SELECT o.cust_id, SUM(o.amount) / (SELECT s FROM tot) FROM fact_sales_order o "
                 "JOIN dim_customer c ON o.cust_id = c.cust_id GROUP BY 1")
    assert ok, why
    ok, why = _v("SELECT (SELECT SUM(c2.credit_limit) FROM dim_customer c2), SUM(o.amount) FROM fact_sales_order o "
                 "JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert ok, why
    ok, why = _v("WITH x AS (SELECT SUM(c.credit_limit) AS s FROM fact_sales_order o JOIN dim_customer c "
                 "ON o.cust_id = c.cust_id) SELECT s FROM x")                    # 子查询内部的重复计数照样拦
    assert not ok and "扇出风险" in why


def test_an_unqualified_column_inside_a_subquery_is_not_charged_to_the_outer_join(make_sqlite, monkeypatch):
    db = make_sqlite({"fact_sales_order": ("cust_id TEXT, amount REAL", [("C1", 10.0)]),
                      "dim_customer": ("cust_id TEXT PRIMARY KEY, credit_limit REAL", [("C1", 500.0)])})
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    ok, why = _v("WITH tot AS (SELECT SUM(credit_limit) AS s FROM dim_customer) SELECT SUM(o.amount) / (SELECT s FROM tot) "
                 "FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert ok, why


def test_semi_joins_do_not_count_as_a_second_child_table():
    ok, why = _p("SELECT SUM(t.hours) FROM orders o JOIN tasks t ON t.order_no = o.order_no "
                 "WHERE EXISTS (SELECT 1 FROM occupancy q WHERE q.order_no = o.order_no)")
    assert ok, why
    ok, why = _p("SELECT SUM(t.hours) FROM tasks t WHERE EXISTS (SELECT 1 FROM occupancy q WHERE q.order_no = t.order_no)")
    assert ok, why
    ok, why = _p("SELECT COUNT(DISTINCT t.task_id) FROM orders o JOIN tasks t ON t.order_no = o.order_no "
                 "JOIN occupancy q ON q.order_no = o.order_no WHERE o.order_no IN "
                 "(SELECT order_no FROM tasks GROUP BY order_no HAVING COUNT(*) > 1)")
    assert ok, why


def test_the_same_child_table_joined_twice_also_multiplies():
    ok, why = _p("SELECT SUM(t1.hours) FROM orders o JOIN tasks t1 ON t1.order_no = o.order_no "
                 "JOIN tasks t2 ON t2.order_no = o.order_no")
    assert not ok and "扇出风险" in why


SELF = {
    "objects": [{"id": "emp", "table": "employee"}, {"id": "sheet", "table": "timesheet"}],
    "links": [{"source": "emp", "target": "emp", "status": "verified", "evidence": {"child_key": "manager_id", "parent_key": "id"}},
              {"source": "sheet", "target": "emp", "status": "verified", "evidence": {"child_key": "emp_id", "parent_key": "id"}}],
}


def test_a_self_reference_takes_the_parent_side_from_the_key_not_from_the_join_order():
    sql = ("SELECT m.name, SUM(t.hours), SUM(e.salary) FROM employee e JOIN employee m ON e.manager_id = m.id "
           "JOIN timesheet t ON t.emp_id = e.id GROUP BY m.name")
    ok, why = server._validate_sql_ontology(sql.replace(", SUM(e.salary)", ""), SELF, strict=True)
    assert ok, why                                   # 每个员工只有一位上级,工时不会重复
    ok, why = server._validate_sql_ontology(sql.replace("SUM(t.hours), SUM(e.salary)", "SUM(m.budget)"), SELF, strict=True)
    assert not ok and "扇出风险" in why               # 上级一行对应多名下属


def test_the_rewrite_suggested_by_the_block_message_is_itself_accepted():
    ok, why = _v("SELECT c.region, SUM(x.amount) FROM dim_customer c JOIN (SELECT cust_no, SUM(amount) AS amount "
                 "FROM fact_sales_order GROUP BY cust_no) x ON c.cust_id = x.cust_no GROUP BY 1")
    assert ok, why


# ── DR-132:子查询别名不能顶替真实的表;条件里的列不算被聚合的列;UNION 各支、关联子查询各自判断 ──────────

def test_a_subquery_or_cte_named_like_a_real_table_does_not_switch_off_the_key_check(make_sqlite, monkeypatch):
    db = make_sqlite({"fact_sales_order": ("order_id INTEGER, cust_id TEXT, amount REAL", []),
                      "dim_customer": ("cust_id TEXT, credit_limit REAL", []), "secret_table": ("x INTEGER", [])})
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    monkeypatch.setattr(server, "table_list", lambda *a, **k: [{"name": n} for n in ("fact_sales_order", "dim_customer", "secret_table")])
    bad = "SELECT o.order_id FROM fact_sales_order o JOIN dim_customer c ON o.amount = c.credit_limit"
    assert not _v(bad)[0]
    for tail in (" WHERE EXISTS (SELECT 1 FROM (SELECT 1 AS z) dim_customer)", " LEFT JOIN (SELECT 1 AS z) dim_customer ON 1=1",
                 " WHERE EXISTS (SELECT 1 FROM (SELECT 1 AS z) c)"):
        ok, why = _v(bad + tail)
        assert not ok and "JOIN 键" in why, tail
    ok, why = _v("WITH w AS (SELECT 1 z) " + bad + " WHERE EXISTS (SELECT 1 FROM w c)")
    assert not ok and "JOIN 键" in why
    ok, why = _v("SELECT * FROM secret_table WHERE EXISTS (SELECT 1 FROM (SELECT 1 AS z) secret_table)")
    assert not ok and "secret_table" in why
    ok, why = _v("SELECT 1 FROM (secret_table s JOIN dim_customer c ON s.x = c.cust_id)")
    assert not ok and "secret_table" in why


def test_a_parent_column_summed_under_a_child_side_condition_is_still_blocked():
    base = " FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id"
    ok, why = _v("SELECT SUM(CASE WHEN o.status = 'A' THEN c.credit_limit END)" + base)
    assert not ok and "扇出风险" in why
    assert _v("SELECT SUM(CASE WHEN c.region = '华东' THEN o.amount ELSE 0 END)" + base)[0]


def test_union_branches_and_correlated_subqueries_are_separate_blocks():
    ok, why = _v("SELECT SUM(c.credit_limit) FROM dim_customer c UNION ALL "
                 "SELECT SUM(o.amount) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert ok, why
    ok, why = _v("SELECT o.order_id, (SELECT SUM(c.credit_limit) FROM dim_customer c WHERE c.cust_id = o.cust_id) "
                 "FROM fact_sales_order o")
    assert ok, why
    ok, why = _v("SELECT 1 FROM dim_customer c UNION ALL SELECT SUM(c.credit_limit) FROM fact_sales_order o "
                 "JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert not ok and "扇出风险" in why


# ── DR-133:按语法解析后,USING / NATURAL JOIN 也能看出父侧;解析不了的写法退回原来的做法 ─────────────

def test_using_and_natural_joins_are_seen_as_joins():
    ok, why = _v("SELECT SUM(c.credit_limit) FROM fact_sales_order o JOIN dim_customer c USING (cust_id)")
    assert not ok and "扇出风险" in why
    ok, why = _v("SELECT SUM(c.credit_limit) FROM fact_sales_order o NATURAL JOIN dim_customer c")
    assert not ok and "扇出风险" in why
    assert _v("SELECT SUM(o.amount) FROM fact_sales_order o JOIN dim_customer c USING (cust_id)")[0]
    ok, why = _p("SELECT SUM(t.hours) FROM orders o JOIN tasks t USING (order_no) JOIN occupancy q USING (order_no)")
    assert not ok and "扇出风险" in why


def test_a_statement_the_parser_cannot_read_falls_back_to_the_previous_checks(monkeypatch):
    import sql_structure
    def boom(_sql):
        raise sql_structure.ParseError("不支持")
    monkeypatch.setattr(sql_structure, "analyze", boom)
    ok, why = _v("SELECT o.id FROM fact_sales_order o JOIN dim_customer c ON o.amount = c.credit_limit")
    assert not ok and "JOIN 键" in why
    ok, why = _v("SELECT SUM(c.credit_limit) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert not ok and "扇出风险" in why


def test_the_reason_names_the_child_tables_of_the_block_even_when_aliases_are_reused():
    # 别名 t 在前一个查询块里指向 occupancy,在出问题的查询块里指向 tasks:原因里的表名要按出问题的那一块
    ok, why = _p("WITH x AS (SELECT t.order_no FROM orders t) SELECT SUM(t.hours) FROM orders o "
                 "JOIN tasks t ON t.order_no = o.order_no JOIN occupancy q ON q.order_no = o.order_no")
    assert not ok and "扇出风险" in why and "tasks" in why and "orders 的每一行" not in why

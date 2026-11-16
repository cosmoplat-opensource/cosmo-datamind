# -*- coding: utf-8 -*-
"""SELECT 语句的结构解析(DR-133):作用域、别名、连接条件、聚合。只解析结构,不连数据库。"""
import pytest

import sql_structure as S


def _tables(sql):
    return sorted(S.analyze(sql)["tables"])


def test_tables_and_aliases_of_a_plain_join():
    out = S.analyze("SELECT c.region, SUM(o.amount) FROM fact_sales_order o JOIN dim_customer AS c ON o.cust_id = c.cust_id GROUP BY 1")
    assert out["cores"] == [{"id": 0, "tables": [("o", "fact_sales_order"), ("c", "dim_customer")]}]
    assert out["pairs"] == [{"a": "o", "ca": "cust_id", "b": "c", "cb": "cust_id", "on": True,
                             "ta": "fact_sales_order", "tb": "dim_customer", "core": 0, "joined": True}]
    assert out["aggs"] == [("sum", "o.amount", 0)]


def test_cte_and_derived_names_are_not_tables_and_are_scoped():
    assert _tables("WITH x AS (SELECT * FROM orders) SELECT * FROM x JOIN customers c ON c.id = x.cid") == ["customers", "orders"]
    assert _tables("SELECT * FROM (SELECT 1 AS z) dim_customer") == []
    # 子查询里定义的 CTE 只在子查询里可见:外层的 secret 是真实的表
    assert _tables("SELECT * FROM secret WHERE EXISTS (WITH secret AS (SELECT 1) SELECT 1 FROM secret)") == ["secret"]
    assert _tables('SELECT * FROM "orders" o, up.sheet1 s WHERE o.id = s.id') == ["orders", "up.sheet1"]
    assert _tables("SELECT 1 FROM (secret_table s JOIN dim_customer c ON s.x = c.cust_id)") == ["dim_customer", "secret_table"]


def test_an_alias_reused_in_a_subquery_does_not_change_the_outer_binding():
    out = S.analyze("WITH w AS (SELECT 1 z) SELECT o.id FROM fact_sales_order o JOIN dim_customer c ON o.amount = c.credit_limit "
                    "WHERE EXISTS (SELECT 1 FROM w c WHERE c.z = o.id)")
    assert [(p["ta"], p["ca"], p["tb"], p["cb"]) for p in out["pairs"]] == [("fact_sales_order", "amount", "dim_customer", "credit_limit")]


def test_correlated_references_resolve_to_the_outer_block_and_are_not_joins():
    out = S.analyze("SELECT o.id, (SELECT SUM(c.credit_limit) FROM dim_customer c WHERE c.cust_id = o.cust_id) FROM fact_sales_order o")
    (pair,) = out["pairs"]
    assert (pair["ta"], pair["tb"], pair["joined"]) == ("dim_customer", "fact_sales_order", False) and pair["core"] != 0
    assert out["aggs"] == [("sum", "c.credit_limit", pair["core"])]


def test_union_branches_are_separate_blocks():
    out = S.analyze("SELECT SUM(c.credit_limit) FROM dim_customer c UNION ALL "
                    "SELECT SUM(o.amount) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id")
    assert [a[2] for a in out["aggs"]] == [0, 1] and [c["tables"] for c in out["cores"]][0] == [("c", "dim_customer")]


def test_using_and_natural_joins_are_reported():
    out = S.analyze("SELECT SUM(c.credit_limit) FROM fact_sales_order o JOIN dim_customer c USING (cust_id)")
    assert out["usings"] == [{"core": 0, "alias": "c", "table": "dim_customer", "cols": ["cust_id"], "earlier": [("o", "fact_sales_order")]}]
    out = S.analyze("SELECT 1 FROM fact_sales_order o NATURAL JOIN dim_customer c")
    assert out["usings"][0]["cols"] is None and out["usings"][0]["earlier"] == [("o", "fact_sales_order")]


def test_aggregate_arguments_keep_expressions_and_drop_subqueries():
    out = S.analyze("SELECT SUM(CASE WHEN c.region = '华东' THEN o.amount ELSE 0 END), COUNT(*), COUNT(DISTINCT o.id), "
                    "AVG(o.qty * (SELECT 1)) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id = c.cust_id "
                    "HAVING SUM(o.amount) > 0")
    args = [a[1] for a in out["aggs"]]
    assert args == ["CASE WHEN c.region = '华东' THEN o.amount ELSE 0 END", "*", "DISTINCT o.id", "o.qty *", "o.amount"]


def test_strings_comments_and_quoted_names_do_not_confuse_the_parser():
    sql = "SELECT * FROM orders o -- from secret\n WHERE o.title = 'x from experts join y' /* join z */ AND o.\"from\" = 1"
    assert _tables(sql) == ["orders"]


@pytest.mark.parametrize("sql", ["DELETE FROM orders", "SELECT * FROM orders; DROP TABLE x", "SELECT (1", "SELECT * FROM", ""])
def test_what_cannot_be_parsed_is_rejected_not_guessed(sql):
    with pytest.raises(S.ParseError):
        S.analyze(sql)


def test_other_select_shapes_parse():
    for sql in ("SELECT 1", "SELECT * FROM t LIMIT 5", "SELECT a, COUNT(*) FROM t GROUP BY a ORDER BY 2 DESC",
                "SELECT * FROM t LEFT OUTER JOIN u ON t.id = u.tid AND u.kind = 'x' CROSS JOIN v",
                "SELECT x FROM t WHERE id IN (SELECT tid FROM u) AND y BETWEEN 1 AND 2",
                "SELECT SUM(x) OVER (PARTITION BY g ORDER BY d) FROM t",
                "SELECT COUNT(*) FILTER (WHERE x > 0) FROM t",
                "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5) SELECT n FROM r",
                "(SELECT a FROM t) UNION (SELECT a FROM u) ORDER BY 1",
                "SELECT * FROM t, json_each(t.payload) j", "SELECT 月份, SUM(金额) FROM 销售 GROUP BY 月份"):
        S.analyze(sql)

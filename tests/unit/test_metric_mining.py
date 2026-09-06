# -*- coding: utf-8 -*-
"""指标反解(DR-054):从历史 SQL / 口径表 / 沉淀 SQL / 参考基准确定性抽取候选与参照。"""
import metric_mining as MM

TAB = {"fact_sales_order": [("order_id", "INTEGER"), ("amount", "REAL"), ("status", "TEXT"), ("order_date", "DATE")],
       "dim_customer": [("cust_id", "INTEGER"), ("region", "TEXT")]}


def test_parse_single_table_aggregate_with_filters_and_monthly_bucket():
    sql = ("-- 月度销售\nCREATE VIEW v_sales_monthly AS SELECT substr(order_date,1,7) AS 月, "
           "SUM(amount) AS 销售金额 FROM fact_sales_order WHERE status <> 'cancelled' GROUP BY 1;")
    cands, skipped = MM.parse_sql(sql, "v_sales_monthly.sql", TAB)
    assert not skipped and len(cands) == 1
    c = cands[0]
    assert c["name"] == "销售金额" and c["measure"] == {"col": "amount", "agg": "sum"}
    assert c["filters"] == [{"col": "status", "op": "!=", "value": "cancelled"}]
    assert c["time"] == {"col": "order_date", "grain": ["month"]}
    assert c["references"][0]["grain"] == "month" and c["provenance"][0]["kind"] == "sql"


def test_join_statements_are_skipped_not_guessed():
    sql = "SELECT c.region, SUM(o.amount) FROM fact_sales_order o JOIN dim_customer c ON o.cust_id=c.cust_id GROUP BY 1"
    cands, skipped = MM.parse_sql(sql, "x.sql", TAB)
    assert not cands and "JOIN" in skipped[0]["reason"]


def test_unparseable_where_drops_filters_and_reference():
    sql = "SELECT COUNT(*) FROM fact_sales_order WHERE status IN ('a','b') OR amount > 1"
    cands, _ = MM.parse_sql(sql, "x.sql", TAB)
    assert cands[0]["filters"] == [] and cands[0]["references"] == []
    assert "未作为过滤" in cands[0]["provenance"][0]["note"]


def test_unknown_table_is_skipped_when_catalog_given():
    cands, skipped = MM.parse_sql("SELECT SUM(x) FROM nowhere", "x.sql", TAB)
    assert not cands and "不在当前数据源" in skipped[0]["reason"]


def test_count_star_and_distinct():
    cands, _ = MM.parse_sql("SELECT COUNT(*) AS 订单数, COUNT(DISTINCT cust_id) FROM fact_sales_order", "x.sql", TAB)
    assert {c["measure"]["agg"] for c in cands} == {"count", "count_distinct"}
    assert cands[0]["name"] == "订单数" and cands[0]["time"]["col"] == "order_date"


def test_glossary_rows_become_unbound_candidates():
    rows = [{"指标名称": "投入产出比", "指标说明": "收入/支出", "计算逻辑": "销售收入 / 采购支出", "业务维度": "经营", "来源": "BI"},
            {"指标名称": "计划产量", "指标说明": "月计划"}]
    out = MM.parse_glossary(rows, "看板.xlsx")
    assert out[0]["layer"] == "derived" and out[0]["formula"] == "销售收入 / 采购支出"
    assert out[1]["layer"] == "atomic" and out[1]["measure"]["agg"] == ""


def test_collect_dedups_same_caliber_and_merges_references():
    gold = [{"id": "Q1", "gold_sql": "SELECT sum(amount) FROM fact_sales_order", "gold": 200.0, "tol": 0.01}]
    skills = [{"question": "总销售额", "analyses": [{"sql": "SELECT SUM(amount) AS s FROM fact_sales_order"}]}]
    out = MM.collect(TAB, {"v.sql": "SELECT SUM(amount) FROM fact_sales_order"}, None, skills, gold)
    assert out["sources"] == {"sql": 1, "dashboard": 0, "qa_skill": 1, "gold": 1}
    assert len(out["candidates"]) == 1
    c = out["candidates"][0]
    kinds = [r["kind"] for r in c["references"]]
    assert kinds.count("sql") == 3 and kinds.count("value") == 1
    assert len(c["provenance"]) == 3

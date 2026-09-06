# -*- coding: utf-8 -*-
"""IR → 关系型语义层投影(DR-049)的隔离单测:确定性、状态不提升、证据随行。"""
import sqlite3
import ir_relational as R

IR = {
    "objects": [
        {"id": "sales_order", "name": "sales_order", "cn": "销售订单", "kind": "event",
         "bfo": "Process", "table": "fact_sales_order", "pk": "order_id",
         "definition": "销售订单是一种记录客户购买承诺的业务单据", "isPrimitive": False,
         "example": "SO-2026-001", "counterExample": "报价单", "maturity": "Provisional",
         "attrs": [{"col": "order_id", "cn": "订单号", "type": "TEXT", "sources": ["db", "comment"]}]},
        {"id": "customer", "name": "customer", "cn": "客户", "kind": "object",
         "bfo": "MaterialEntity", "table": "dim_customer", "pk": "cust_id",
         "isPrimitive": True, "candidate": True, "attrs": []},
    ],
    "links": [
        {"source": "sales_order", "target": "customer", "verb": "归属", "status": "verified",
         "founded_relation": "continuantPartOfAtAllTimes", "temporal": "atAllTimes",
         "evidence": {"child_key": "cust_id", "parent_key": "cust_id", "overlap": 100.0,
                      "sources": ["key_overlap"]}},
        {"source": "customer", "target": "sales_order", "verb": "关联", "status": "candidate",
         "candidate": True, "note": "弱重叠送审", "evidence": {"overlap": 31.0}},
    ],
    "metric_layers": {"atomic": [{"id": "m1", "name": "订单量", "type": "计数",
                                  "table": "fact_sales_order", "value_col": "order_id",
                                  "unit": "单"}]},
}


def _mem(ir):
    con = sqlite3.connect(":memory:")
    stat = R.project(ir, con)
    return con, stat


def test_counts_match_ir():
    con, stat = _mem(IR)
    assert stat == {"ont_object": 2, "ont_attribute": 1, "ont_relation": 2,
                    "ont_evidence": 2, "ont_metric": 1}
    con.close()


def test_deterministic_bytes():
    """同一 IR 反复投影结果必须逐字节一致——否则无法做 diff 回归。"""
    assert R.project_to_sql(IR) == R.project_to_sql(IR)


def test_status_not_promoted():
    """投影是只读派生物:candidate 不得在投影层被提升为 verified。"""
    con, _ = _mem(IR)
    rows = dict(con.execute("SELECT verb,status FROM ont_relation").fetchall())
    assert rows["归属"] == "verified" and rows["关联"] == "candidate"
    con.close()


def test_evidence_travels_with_relation():
    """裁决证据(子键/父键/重叠率)必须随关系带出,否则下游无从判断可信度。"""
    con, _ = _mem(IR)
    r = con.execute("SELECT child_key,parent_key,overlap FROM ont_evidence e "
                    "JOIN ont_relation r ON r.rel_id=e.rel_id WHERE r.verb='归属'").fetchone()
    assert r == ("cust_id", "cust_id", 100.0)
    con.close()


def test_verified_join_view_excludes_candidate():
    """v_verified_join 供下游直接生成 JOIN,只能出 verified 且键齐全的边。"""
    con, _ = _mem(IR)
    rows = con.execute("SELECT source_table,child_key,target_table,parent_key FROM v_verified_join").fetchall()
    assert rows == [("fact_sales_order", "cust_id", "dim_customer", "cust_id")]
    con.close()


def test_primitive_and_candidate_flags():
    con, _ = _mem(IR)
    d = dict(con.execute("SELECT obj_id,is_primitive FROM ont_object").fetchall())
    c = dict(con.execute("SELECT obj_id,is_candidate FROM ont_object").fetchall())
    assert d["customer"] == 1 and d["sales_order"] == 0 and c["customer"] == 1
    con.close()


def test_non_scalar_fields_serialized():
    """attrs.sources 是列表,必须序列化后入库(sqlite 不接受 list)。"""
    con, _ = _mem(IR)
    s = con.execute("SELECT sources FROM ont_attribute WHERE col='order_id'").fetchone()[0]
    assert s == "db,comment"
    con.close()


def test_contract_columns_and_consumable_view():
    """DR-054:契约指标带状态/聚合/编译 SQL 入表;只有 verified/certified 且可编译者进入消费视图。"""
    ir = {"objects": [], "links": [], "metric_layers": {"atomic": [
        {"id": "m1", "name": "销售金额", "table": "fact_sales_order", "value_col": "amount",
         "measure": {"col": "amount", "agg": "sum"}, "filters": [{"col": "status", "op": "!=", "value": "x"}],
         "time": {"col": "order_date", "grain": ["month"]}, "status": "verified"},
        {"id": "m2", "name": "计划产量", "table": "T", "value_col": "p", "candidate": True},
    ]}}
    con, stat = _mem(ir)
    assert stat["ont_metric"] == 2
    row = con.execute("SELECT status, agg, time_col, compiled_sql FROM ont_metric WHERE metric_id='m1'").fetchone()
    assert row[0] == "verified" and row[1] == "sum" and row[2] == "order_date" and row[3].startswith("SELECT SUM")
    assert con.execute("SELECT status, agg FROM ont_metric WHERE metric_id='m2'").fetchone() == ("candidate", None)
    assert [r[0] for r in con.execute("SELECT name FROM v_consumable_metric")] == ["销售金额"]
    con.close()

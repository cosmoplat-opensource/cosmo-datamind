# -*- coding: utf-8 -*-
"""指标契约(DR-054):编译确定性、只读执行、参照比对、状态裁决与证据审计。"""
import pytest

import metric_contract as M


def _db(make_sqlite):
    return make_sqlite({
        "fact_sales_order": (
            "order_id INTEGER PRIMARY KEY, cust_id INTEGER, amount REAL, status TEXT, order_date TEXT",
            [(1, 10, 100.0, "done", "2025-03-01"), (2, 10, 50.0, "done", "2025-03-15"),
             (3, 11, 30.0, "cancelled", "2025-04-02"), (4, 12, 20.0, "done", "2025-04-20")]),
        "dim_customer": ("cust_id INTEGER PRIMARY KEY, region TEXT", [(10, "华东"), (11, "华北"), (12, "华南")]),
    })


IR = {
    "objects": [
        {"id": "sales_order", "cn": "销售订单", "table": "fact_sales_order",
         "attrs": [{"col": "order_id"}, {"col": "cust_id"}, {"col": "amount"}, {"col": "status"}, {"col": "order_date"}]},
        {"id": "customer", "cn": "客户", "table": "dim_customer", "attrs": [{"col": "cust_id"}, {"col": "region"}]},
    ],
    "links": [{"source": "sales_order", "target": "customer", "verb": "归属", "status": "verified",
               "evidence": {"child_key": "cust_id", "parent_key": "cust_id", "overlap": 100.0}}],
}

CONTRACT = {
    "id": "m.sales_amount", "name": "销售金额", "entity": "sales_order",
    "measure": {"col": "amount", "agg": "sum"},
    "filters": [{"col": "status", "op": "!=", "value": "cancelled"}],
    "time": {"col": "order_date", "grain": ["month"]},
    "unit": "元",
}


def test_normalize_binds_table_from_entity_and_mirrors_legacy_fields():
    c = M.normalize(CONTRACT, IR)
    assert c["table"] == "fact_sales_order" and c["value_col"] == "amount"
    assert c["status"] == "candidate" and c["measure"]["agg"] == "sum"


def test_normalize_keeps_legacy_shape_without_agg():
    c = M.normalize({"name": "计划产量", "table": "DWS_PRODUCTION_DAILY", "value_col": "planned_quantity"})
    assert not M.is_contract(c)
    assert c["table"] == "DWS_PRODUCTION_DAILY"


@pytest.mark.parametrize("bad", [
    {"name": "x", "measure": {"col": "amount", "agg": "median"}},
    {"name": "x", "measure": {"col": "amount; drop table t", "agg": "sum"}},
    {"name": "x", "measure": {"col": "amount", "agg": "sum"}, "filters": [{"col": "status", "op": "like", "value": "a"}]},
    {"name": "x", "measure": {"col": "amount", "agg": "sum"}, "filters": [{"col": "status", "op": "in", "value": []}]},
])
def test_normalize_rejects_structurally_invalid(bad):
    with pytest.raises(ValueError):
        M.normalize(bad)


def test_compile_is_deterministic_and_shapes_follow_arguments():
    c = M.normalize(CONTRACT, IR)
    scalar = M.compile_sql(c)
    assert scalar == M.compile_sql(c)
    assert scalar.startswith('SELECT SUM("amount") AS "销售金额" FROM "fact_sales_order" WHERE "status" != \'cancelled\'')
    monthly = M.compile_sql(c, grain="month")
    assert 'substr("order_date",1,7)' in monthly and "GROUP BY 1 ORDER BY 1" in monthly
    by_status = M.compile_sql(c, dimensions=["status"])
    assert '"status", SUM("amount")' in by_status and "GROUP BY 1" in by_status
    with pytest.raises(ValueError):
        M.compile_sql(c, dimensions=["status; --"])


def test_compile_requires_time_column_for_grain():
    c = M.normalize({**CONTRACT, "time": {}}, IR)
    with pytest.raises(ValueError):
        M.compile_sql(c, grain="month")


def test_execute_is_readonly_and_reports_errors(make_sqlite):
    db = _db(make_sqlite)
    assert M.execute(db, "DELETE FROM fact_sales_order").get("error")
    out = M.execute(db, "SELECT count(*) FROM fact_sales_order")
    assert out["rows"] == [[4]]


def test_compare_scalar_tolerance_and_rowsets():
    ok, _ = M.compare_results({"rows": [[100.4]]}, {"rows": [[100.0]]}, tol=0.01)
    assert ok
    ok, why = M.compare_results({"rows": [[103.0]]}, {"rows": [[100.0]]}, tol=0.01)
    assert not ok and "数值不一致" in why
    a = {"rows": [["2025-03", 150.0], ["2025-04", 20.0]]}
    b = {"rows": [["2025-04", 20.0], ["2025-03", 150.0]]}
    assert M.compare_results(a, b)[0]
    assert not M.compare_results(a, {"rows": [["2025-03", 150.0]]})[0]


def test_adjudicate_verified_only_with_matching_reference(make_sqlite):
    db = _db(make_sqlite)
    c = M.normalize(CONTRACT, IR)
    ref = {"kind": "sql", "sql": "SELECT sum(amount) FROM fact_sales_order WHERE status<>'cancelled'", "source": "gold:Q1"}
    out = M.adjudicate(db, c, IR, [ref])
    assert out["status"] == "verified" and out["evidence"]["match"] is True
    assert out["evidence"]["value"] == 170.0
    # 无参照:可执行但仍是 candidate——可执行不等于口径正确
    out2 = M.adjudicate(db, c, IR, [])
    assert out2["status"] == "candidate" and out2["evidence"]["executed"] is True and out2["evidence"]["match"] is None
    # 参照不一致:candidate,并把差异记进证据
    bad = {"kind": "sql", "sql": "SELECT sum(amount) FROM fact_sales_order", "source": "view:v_all"}
    out3 = M.adjudicate(db, c, IR, [bad])
    assert out3["status"] == "candidate" and out3["evidence"]["match"] is False


def test_adjudicate_grouped_reference_recompiles_to_reference_shape(make_sqlite):
    db = _db(make_sqlite)
    c = M.normalize(CONTRACT, IR)
    ref = {"kind": "sql", "source": "view:v_monthly", "grain": "month",
           "sql": "SELECT substr(order_date,1,7) m, sum(amount) FROM fact_sales_order WHERE status<>'cancelled' GROUP BY 1"}
    out = M.adjudicate(db, c, IR, [ref])
    assert out["status"] == "verified"


def test_adjudicate_records_error_and_never_promotes_on_missing_column(make_sqlite):
    db = _db(make_sqlite)
    c = M.normalize({**CONTRACT, "measure": {"col": "amt", "agg": "sum"}}, IR)
    out = M.adjudicate(db, c, IR, [{"kind": "value", "value": 170.0}])
    assert out["status"] == "candidate" and out["evidence"]["executed"] is False and out["evidence"]["error"]


def test_adjudicate_does_not_touch_human_status(make_sqlite):
    db = _db(make_sqlite)
    c = M.normalize({**CONTRACT, "status": "certified", "certified_by": "张三"}, IR)
    out = M.adjudicate(db, c, IR, [{"kind": "value", "value": 1.0}])   # 参照明显不符
    assert out["status"] == "certified" and out["evidence"]["match"] is False


def test_audit_flags_verified_without_evidence_and_certified_without_reviewer():
    ir = {"metric_layers": {"atomic": [
        {"name": "a", "status": "verified", "measure": {"col": "x", "agg": "sum"}},
        {"name": "b", "status": "certified", "measure": {"col": "x", "agg": "sum"}},
    ]}}
    types = {i["type"] for i in M.audit(ir)}
    assert types == {"verified_metric_missing_evidence", "certified_metric_missing_reviewer"}


def test_upsert_keeps_higher_human_status_and_merges_provenance():
    ir = {"metric_layers": {"atomic": [{"id": "m1", "name": "销售金额", "status": "certified", "certified_by": "李四",
                                        "measure": {"col": "amount", "agg": "sum"}, "provenance": [{"kind": "manual"}]}]}}
    new = M.normalize({"id": "m1", "name": "销售金额", "status": "verified", "measure": {"col": "amount", "agg": "sum"},
                       "provenance": [{"kind": "sql", "source": "v.sql"}]})
    layers, stat = M.upsert_layers(ir, [new])
    assert stat == {"added": 0, "updated": 0, "kept": 1}
    m = layers["atomic"][0]
    assert m["status"] == "certified" and len(m["provenance"]) == 2


def test_allowed_dimensions_follow_verified_relations_and_flag_fan_out():
    c = M.normalize(CONTRACT, IR)
    dims = M.allowed_dimensions(c, IR)
    assert "status" in dims["columns"]
    assert dims["objects"] == [{"object": "customer", "table": "dim_customer", "child_key": "cust_id",
                                "parent_key": "cust_id", "status": "verified"}]
    parent_side = M.normalize({"name": "客户数", "entity": "customer", "measure": {"col": "cust_id", "agg": "count"}}, IR)
    d2 = M.allowed_dimensions(parent_side, IR)
    assert d2["objects"][0]["fan_out_risk"] is True

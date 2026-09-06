# -*- coding: utf-8 -*-
"""指标编排(DR-054):反解→核验→并入,verified 只来自与参照一致的执行结果。"""
import metric_pipeline as MP
import metric_contract as MC


def _db(make_sqlite):
    return make_sqlite({
        "fact_sales_order": (
            "order_id INTEGER PRIMARY KEY, amount REAL, status TEXT, order_date TEXT",
            [(1, 100.0, "done", "2025-03-01"), (2, 50.0, "done", "2025-03-15"),
             (3, 30.0, "cancelled", "2025-04-02"), (4, 20.0, "done", "2025-04-20")]),
    })


IR = {"objects": [{"id": "sales_order", "cn": "销售订单", "table": "fact_sales_order",
                   "attrs": [{"col": "amount"}, {"col": "status"}, {"col": "order_date", "type": "DATE"}]}],
      "links": [],
      "metric_layers": {"atomic": [{"id": "示例.0001", "name": "计划产量", "table": "DWS_X", "value_col": "p", "candidate": True}]}}
TAB = {"fact_sales_order": [("order_id", "INTEGER"), ("amount", "REAL"), ("status", "TEXT"), ("order_date", "DATE")]}


def test_run_verifies_metric_backed_by_gold_and_keeps_legacy_entries(make_sqlite):
    db = _db(make_sqlite)
    gold = [{"id": "Q1", "gold_sql": "SELECT sum(amount) FROM fact_sales_order WHERE status<>'cancelled'",
             "gold": 170.0, "tol": 0.01}]
    sql = {"v_monthly.sql": "SELECT substr(order_date,1,7) m, SUM(amount) AS 销售金额 FROM fact_sales_order "
                            "WHERE status <> 'cancelled' GROUP BY 1"}
    out = MP.run(db, IR, TAB, sql, None, None, gold)
    rep = out["report"]
    assert rep["verified"] == 1 and rep["contracts"] == 1
    atomic = out["metric_layers"]["atomic"]
    assert any(m["name"] == "计划产量" for m in atomic)            # 旧形状指标原样保留
    m = next(x for x in atomic if x["name"] == "销售金额")
    assert m["status"] == "verified" and m["evidence"]["reference"]["match"] is True
    assert m["value_col"] == "amount"                              # 旧消费方字段镜像


def test_run_keeps_unverifiable_as_candidate_and_reports_why(make_sqlite):
    db = _db(make_sqlite)
    sql = {"v.sql": "SELECT COUNT(*) FROM fact_sales_order WHERE status IN ('done','x')"}
    out = MP.run(db, IR, TAB, sql)
    m = out["metric_layers"]["atomic"][-1]
    assert m["status"] == "candidate" and m["evidence"]["executed"] is True and m["evidence"]["match"] is None
    assert out["report"]["executable_unverified"] == 1


def test_readjudicate_reuses_saved_reference(make_sqlite):
    db = _db(make_sqlite)
    first = MP.run(db, IR, TAB, {"v.sql": "SELECT SUM(amount) AS s FROM fact_sales_order"})
    ir = dict(IR); ir["metric_layers"] = first["metric_layers"]
    again = MP.readjudicate(db, ir)
    assert again["report"]["verified"] == 1
    assert MC.counts({"metric_layers": again["metric_layers"]})["verified"] == 1

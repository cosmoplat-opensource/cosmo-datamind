# -*- coding: utf-8 -*-
"""查数结果的三种状态与实际取数日期(DR-130)。

此前查询失败的那条分析直接从答案里消失,返回 0 行与「业务上没有」不作区分;依据卡只有全库的数据日期,
看不出这条查询实际读到哪一天。全部在合成库上运行,不调模型。"""
import sqlite3

import pytest

import qa_query_status as qs
import server


@pytest.fixture
def db(make_sqlite):
    path = make_sqlite({
        "orders": ("order_no TEXT PRIMARY KEY, cust_id TEXT, order_date TEXT, amount REAL",
                   [("S1", "C1", "2026-10-03", 100.0), ("S2", "C2", "2026-11-08", 250.0)]),
        "returns": ("return_no TEXT PRIMARY KEY, order_no TEXT, created_at TEXT", []),
        "customers": ("cust_id TEXT PRIMARY KEY, name TEXT", [("C1", "甲"), ("C2", "乙")]),
    })
    return path


def _connect(path):
    return lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _res(title, sql, rows):
    return {"title": title, "sql": sql, "data": {"columns": list(rows[0]) if rows else [], "rows": rows}}


def test_the_tables_a_query_reads_are_found_by_the_database_itself(db):
    sql = 'WITH x AS (SELECT * FROM "orders") SELECT c.name FROM x JOIN customers c ON c.cust_id = x.cust_id'
    assert qs.tables_read(sql, _connect(db)) == ["customers", "orders"]


def test_an_empty_result_says_whether_the_table_or_the_filter_is_empty(db):
    r1 = _res("12 月订单", "SELECT * FROM orders WHERE order_date >= '2026-12-01'", [])
    r2 = _res("退货", "SELECT * FROM returns", [])
    out = qs.annotate([r1, r2], [], _connect(db))
    assert r1["status"] == "empty" and r1["coverage"] == [{"table": "orders", "rows": 2}]
    assert r2["status"] == "empty" and r2["coverage"] == [{"table": "returns", "rows": 0}]
    flags = " ".join(out["flags"])
    assert "12 月订单" in flags and "筛选条件没有命中" in flags and "orders 共 2 行" in flags
    assert "退货" in flags and "returns 表里没有数据" in flags
    assert "不能据此" in flags                                   # 空结果不等于业务上没有


def test_a_failed_query_is_reported_and_kept_apart_from_an_empty_one(db):
    ok = _res("订单", "SELECT order_no FROM orders", [{"order_no": "S1"}, {"order_no": "S2"}])
    steps = [{"step": "exec_sql", "ok": True, "info": "订单 → 2行"},
             {"step": "exec_sql", "ok": False, "info": "退货金额: no such column: amount"},
             {"step": "ontology_gate", "ok": False, "info": "本体约束拦截:表 x 不在本体/数据目录中"}]
    out = qs.annotate([ok], steps, _connect(db))
    assert ok["status"] == "data"
    assert out["status"] == {"data": 1, "empty": 0, "failed": 2}
    assert [f["kind"] for f in out["failed"]] == ["error", "blocked"]
    flags = " ".join(out["flags"])
    assert "2 条查询没有取到数据" in flags and "no such column" in flags and "不代表" in flags


def test_each_result_says_how_recent_the_data_it_read_is(db):
    r = _res("近期订单", "SELECT order_no, amount FROM orders WHERE order_date >= '2026-10-01'", [{"order_no": "S1", "amount": 100.0}])
    out = qs.annotate([r], [], _connect(db))
    assert r["data_as_of"] == [{"table": "orders", "column": "order_date", "latest": "2026-11-08"}]
    assert ("实际取数截至", "orders.order_date 最新 2026-11-08") in out["kv"]


def test_a_declared_time_column_is_used_when_the_query_does_not_name_one(db):
    r = _res("订单金额", "SELECT SUM(amount) AS s FROM orders", [{"s": 350.0}])
    ir = {"objects": [{"name": "订单", "table": "orders", "time_col": "order_date"}]}
    qs.annotate([r], [], _connect(db), ir)
    assert r["data_as_of"] == [{"table": "orders", "column": "order_date", "latest": "2026-11-08"}]
    r2 = _res("客户", "SELECT name FROM customers", [{"name": "甲"}])
    qs.annotate([r2], [], _connect(db), ir)
    assert "data_as_of" not in r2                                # 没有时间列的表不编日期


def test_results_without_sql_and_broken_connections_are_left_alone(db):
    r = _res("规则判定汇总", "", [{"规则": "拖期", "命中": 1}])
    out = qs.annotate([r], [], _connect(db))
    assert r["status"] == "data" and out["flags"] == []

    def broken():
        raise sqlite3.OperationalError("unable to open database file")
    r = _res("订单", "SELECT * FROM orders", [])
    out = qs.annotate([r], [], broken)
    assert r["status"] == "empty" and "coverage" not in r       # 核对不了就不说,不编


# ── 接入问数 ────────────────────────────────────────────────────────

def test_an_answer_shows_failed_and_empty_queries_on_its_card(db, monkeypatch):
    plan = {"analyses": [{"title": "订单", "sql": "SELECT order_no, amount FROM orders WHERE order_date >= '2026-10-01'"},
                         {"title": "12 月订单", "sql": "SELECT order_no FROM orders WHERE order_date >= '2026-12-01'"},
                         {"title": "退货金额", "sql": "SELECT SUM(amount) FROM returns"}]}
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: plan)
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *a, **k: "订单见下表。")
    monkeypatch.setattr(server, "_verify_answer", lambda *a, **k: None)
    monkeypatch.setattr(server, "_qa_validate_sql", lambda *a, **k: (True, ""))
    server._QA_CACHE.clear()
    d = server.app.test_client().post("/api/chat", json={"q": "订单与退货情况", "nocache": True}).get_json()
    by = {r["title"]: r for r in d["results"]}
    assert by["订单"]["status"] == "data" and by["订单"]["data_as_of"][0]["latest"] == "2026-11-08"
    assert by["12 月订单"]["status"] == "empty"
    rec = d["answer_record"]["query_status"]
    assert rec["status"] == {"data": 1, "empty": 1, "failed": 1} and rec["failed"][0]["title"] == "退货金额"
    flags = " ".join(d["evidence_card"]["flags"])
    assert "退货金额" in flags and "筛选条件没有命中" in flags
    assert ["实际取数截至", "orders.order_date 最新 2026-11-08"] in d["evidence_card"]["kv"]


def test_a_month_column_also_counts_as_a_time_column(make_sqlite):
    path = make_sqlite({"sales": ("cust_id TEXT, month TEXT, profit REAL", [("C1", "2026-09", 1.0), ("C1", "2026-10", 2.0)])})
    r = _res("利润", "SELECT cust_id, profit FROM sales WHERE month = '2026-10'", [{"cust_id": "C1", "profit": 2.0}])
    qs.annotate([r], [], _connect(path))
    assert r["data_as_of"] == [{"table": "sales", "column": "month", "latest": "2026-10"}]

# -*- coding: utf-8 -*-
"""问数校验的几处漏洞(DR-098):连接写法绕过键校验、注释误拦、探查查询越出所选范围、
不存在的表或列通过校验、降级答案进缓存、空结果投票胜出、后台任务出错后卡死。"""
import sqlite3

import pytest

import server

IR = {
    "objects": [{"id": "order", "table": "fact_sales_order"}, {"id": "customer", "table": "dim_customer"},
                {"id": "product", "table": "dim_product"}],
    "links": [{"source": "order", "target": "customer", "status": "verified",
               "evidence": {"child_key": "cust_id", "parent_key": "cust_id"}}],
}
UNVERIFIED = "JOIN 键"       # 拦截原因里的固定用语


def _v(sql):
    return server._validate_sql_ontology(sql, IR, strict=True)


# ── 连接条件的各种写法都要过键校验 ──────────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT * FROM fact_sales_order o JOIN dim_product p ON (o.prod_ref = p.code)",
    "SELECT * FROM fact_sales_order o JOIN dim_product p ON o.id = p.id AND o.prod_ref = p.code",
    "SELECT * FROM fact_sales_order o, dim_product p WHERE o.prod_ref = p.code",
    "SELECT * FROM fact_sales_order o CROSS JOIN dim_product p WHERE o.prod_ref = p.code",
    'SELECT * FROM "fact_sales_order" o JOIN "dim_product" p ON o.prod_ref = p.code',
])
def test_unverified_join_is_blocked_however_it_is_written(sql):
    ok, why = _v(sql)
    assert not ok and UNVERIFIED in why, why


@pytest.mark.parametrize("sql", [
    "SELECT * FROM fact_sales_order o, dim_customer c WHERE o.cust_id = c.cust_id",
    "SELECT * FROM fact_sales_order o JOIN dim_customer c ON (o.cust_id = c.cust_id) WHERE o.amount > 0",
    "SELECT o.amount FROM fact_sales_order o WHERE o.amount = o.paid_amount",        # 同一张表内的列比较不是连接
    "SELECT * FROM fact_sales_order o WHERE o.status = 'a.x = b.y'",                  # 字符串里的等式不算
])
def test_legitimate_queries_still_pass(sql):
    ok, why = _v(sql)
    assert ok, why


def test_fabricated_table_in_a_comma_join_or_quoted_is_blocked():
    for sql in ("SELECT * FROM fact_sales_order o, ghost g WHERE o.id = g.id",
                'SELECT * FROM "fact_sales_order" o JOIN "ghost" g ON o.id = g.id'):
        ok, why = _v(sql)
        assert not ok and "ghost" in why, (sql, why)


def test_fanout_is_detected_with_a_parenthesised_on():
    ok, why = _v("SELECT SUM(c.credit_limit) FROM fact_sales_order o JOIN dim_customer c ON (o.cust_id = c.cust_id)")
    assert not ok and "扇出风险" in why


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM fact_sales_order -- data came from legacy_sys\n",
    "SELECT COUNT(*) /* join ghost later */ FROM fact_sales_order",
])
def test_table_names_inside_comments_do_not_block(sql):
    ok, why = _v(sql)
    assert ok, why


# ── 不存在的表或列在校验阶段拦下 ────────────────────────────────────

def test_missing_column_is_reported_by_the_gate_not_at_execution(make_sqlite, monkeypatch):
    db = make_sqlite({"orders": ("id INTEGER, amount REAL", [(1, 2.0)])})
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    ir = {"objects": [{"id": "orders", "table": "orders"}], "links": []}
    ok, why = server._qa_validate_sql("SELECT total_amt FROM orders", ir)
    assert not ok and "total_amt" in why
    assert server._qa_validate_sql("SELECT amount FROM orders", ir) == (True, "")


# ── 探查查询受所选范围约束 ──────────────────────────────────────────

def test_probe_outside_the_selected_tables_is_refused(make_sqlite, monkeypatch):
    db = make_sqlite({"orders": ("id INTEGER", [(1,)]), "salary": ("emp TEXT, pay REAL", [("张三", 9.0)])})
    monkeypatch.setattr(server, "_qa_db", lambda: db)
    monkeypatch.setattr(server, "q", lambda sql, **k: {"columns": ["x"], "rows": [{"x": 1}]})
    ir = {"objects": [{"id": "orders", "table": "orders"}], "links": []}
    run = server._probe_runner(scope=lambda sql: server._qa_scope_check(sql, ir, strict=True))
    assert run("SELECT * FROM salary").startswith("拒绝")
    assert "拒绝" not in run("SELECT id FROM orders")


# ── 投票 ────────────────────────────────────────────────────────────

def test_empty_results_do_not_outvote_an_answer_with_rows():
    cands = [{"rows": []}, {"rows": []}, {"rows": [{"n": 7}]}]
    pick, info = server._vote_candidates("q", cands, chooser=lambda q, g: 0)
    assert pick == 2 and info["votes"] == 1


def test_all_empty_results_still_return_a_candidate():
    pick, _ = server._vote_candidates("q", [{"rows": []}, {"rows": []}], chooser=lambda q, g: 0)
    assert pick == 0


# ── 降级答案不进缓存 ────────────────────────────────────────────────

def test_template_answer_after_engine_timeout_is_not_cached(monkeypatch):
    monkeypatch.setattr(server, "agent_sql_plan", lambda *a, **k: None)
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *a, **k: "ok")
    server._QA_CACHE.clear()
    body = {"q": "各月收入趋势"}
    first = server.app.test_client().post("/api/chat", json=body).get_json()
    assert first.get("results") and first.get("degraded") is True
    second = server.app.test_client().post("/api/chat", json=body).get_json()
    assert second.get("cached") is not True


# ── 后台任务出错后不再卡在「运行中」 ────────────────────────────────

def test_eval_job_is_released_when_the_run_raises(monkeypatch):
    monkeypatch.setattr(server, "_eval_items", lambda: [{"id": "q1", "q": "x", "gold": 1}])
    monkeypatch.setattr(server, "_active_ir", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    server.EVAL_JOB.update(running=True, progress="")
    server._run_eval_thread()
    assert server.EVAL_JOB["running"] is False and "boom" in server.EVAL_JOB["progress"]


def test_skill_compare_job_is_released_when_the_run_raises(monkeypatch):
    monkeypatch.setattr(server, "_gather_evidence", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("boom")))
    server.SKILL_CMP_JOB.update(running=True)
    server._run_skill_compare("需求", [["ontology-semi-auto"]])
    assert server.SKILL_CMP_JOB["running"] is False

"""Offline QA must answer the selected table question, in sync and SSE flows."""
import copy
import json

import pytest

import server
import usage_stat


@pytest.fixture
def offline_qa(make_sqlite, monkeypatch):
    db = make_sqlite({
        "customer_register": ("id INTEGER", [(i,) for i in range(25)]),
        "fact_sales_order": ("order_date TEXT, amount REAL, gross_profit_actual REAL",
                             [("2026-01-01", 100, 10), ("2026-02-01", 200, 20)]),
        "unmodeled": ("id INTEGER", [(99,)]),
    })
    ir = {"objects": [
        {"name": "customers", "cn": "客户维度表", "table": "customer_register"},
        {"name": "sales", "cn": "销售订单事实表", "table": "fact_sales_order"},
    ], "relations": []}
    monkeypatch.setattr(server, "DB", db)
    monkeypatch.setattr(server, "_anchor_ir", lambda _keys=None: (copy.deepcopy(ir), ["custom"], ""))
    monkeypatch.setattr(server, "agent_sql_plan", lambda *_args, **_kw: None)
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *_args, **_kw: None)
    monkeypatch.setattr(usage_stat, "record", lambda *_args, **_kw: None)
    return server.app.test_client()


def _ask(client, route, question, tables):
    response = client.post(route, json={"q": question, "graphs": ["custom"],
                                       "tables": tables, "nocache": True})
    assert response.status_code == 200
    if route.endswith("/stream"):
        events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines()
                  if line.startswith("data: ")]
        return next(event for event in events if event.get("type") == "done")
    return response.get_json()


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/stream"])
def test_offline_row_count_uses_selected_ontology_table_in_both_flows(offline_qa, route):
    answer = _ask(offline_qa, route, "客户维度表有多少条记录？", ["customer_register"])
    assert answer["anchor"]["ontology"]["keys"] == ["custom"]
    assert answer["anchor"]["scoped"] is True
    assert len(answer["results"]) == 1
    result = answer["results"][0]
    assert result["data"]["rows"] == [{"记录数": 25}]
    assert "customer_register" in result["sql"]
    assert "fact_sales_order" not in result["sql"]


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/stream"])
def test_unknown_offline_intent_does_not_return_unrelated_sales(offline_qa, route):
    answer = _ask(offline_qa, route, "客户未来三年的信用风险怎么预测？", ["customer_register"])
    assert answer["results"] == []
    assert "内置" in answer["narrative"] and "覆盖" in answer["narrative"]


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/stream"])
@pytest.mark.parametrize("sql", ["SELECT COUNT(*) FROM fact_sales_order",
                                'WITH picked AS (SELECT * FROM "fact_sales_order") SELECT COUNT(*) FROM picked'])
def test_explicit_table_scope_also_constrains_reused_skill_sql(offline_qa, monkeypatch, route, sql):
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: {
        "analyses": [{"title": "wrong table", "sql": sql}]})
    answer = _ask(offline_qa, route, "客户维度表有多少条记录？", ["customer_register"])
    assert answer["results"] == []
    assert any(step["step"] == "ontology_gate" and not step["ok"] for step in answer["steps"])


@pytest.mark.parametrize("question", ["最近一个月客户维度表有多少条记录？", "活跃客户维度表有多少条记录？",
                                      "客户维度表有多少条记录属于高风险？"])
def test_row_count_does_not_drop_unimplemented_business_filters(offline_qa, question):
    answer = _ask(offline_qa, "/api/chat", question, ["customer_register"])
    assert answer["results"] == []
    assert "未覆盖" in answer["narrative"]


def test_count_can_use_english_only_quick_build_binding():
    ir = {"objects": [{"name": "dim_customer", "table": "dim_customer"}]}
    plan = server.fallback_plan("客户维度表有多少条记录？", ir=ir, focus_tables=["dim_customer"])
    assert plan["analyses"][0]["sql"] == 'SELECT COUNT(*) AS "记录数" FROM "dim_customer"'


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/stream"])
@pytest.mark.parametrize("sql", [
    'SELECT COUNT(*) FROM "unmodeled"',
    'WITH picked AS (SELECT * FROM "unmodeled") SELECT COUNT(*) FROM picked',
])
def test_graph_scope_rejects_quoted_unmodeled_tables_without_explicit_focus(offline_qa, monkeypatch, route, sql):
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: {
        "analyses": [{"title": "outside selected graph", "sql": sql}]})
    answer = _ask(offline_qa, route, "客户维度表有多少条记录？", [])
    assert answer["anchor"]["ontology"]["keys"] == ["custom"]
    assert answer["results"] == []
    assert any(step["step"] == "ontology_gate" and not step["ok"] for step in answer["steps"])


@pytest.mark.parametrize("route", ["/api/chat", "/api/chat/stream"])
def test_graph_scope_keeps_quoted_cte_queries_inside_selected_ontology(offline_qa, monkeypatch, route):
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: {"analyses": [{
        "title": "customer count",
        "sql": 'WITH picked AS (SELECT * FROM "customer_register") SELECT COUNT(*) AS n FROM picked',
    }]})
    answer = _ask(offline_qa, route, "客户维度表有多少条记录？", [])
    assert answer["results"][0]["data"]["rows"] == [{"n": 25}]


def test_explicit_focus_cannot_add_tables_outside_selected_graph(offline_qa, monkeypatch):
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: {"analyses": [{
        "title": "outside selected graph", "sql": 'SELECT COUNT(*) FROM "unmodeled"',
    }]})
    answer = _ask(offline_qa, "/api/chat", "这张表有多少条记录？", ["unmodeled"])
    assert answer["results"] == []

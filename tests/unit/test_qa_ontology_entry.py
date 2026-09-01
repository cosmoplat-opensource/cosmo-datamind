# -*- coding: utf-8 -*-
"""从本体新建深度问数的作用域与可执行性回归。"""
import json
import pathlib
import sqlite3

import server
import usage_stat


ROOT = pathlib.Path(__file__).resolve().parents[2]
UI = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
QA_UI = (ROOT / "ui" / "modules" / "qa-ontology.js").read_text(encoding="utf-8")
QA_HISTORY = (ROOT / "ui" / "modules" / "qa-history.js").read_text(encoding="utf-8")


def _db(path, tables):
    con = sqlite3.connect(path)
    for table in tables:
        con.execute(f'CREATE TABLE "{table}" (id INTEGER, amount REAL)')
    con.commit(); con.close()


def test_graph_profile_distinguishes_main_upload_and_unavailable(tmp_path, monkeypatch):
    main, uploads = tmp_path / "main.db", tmp_path / "uploads.db"
    _db(main, ["fact_order"]); _db(uploads, ["upload_order"])
    monkeypatch.setattr(server, "DB", str(main))
    monkeypatch.setattr(server, "UPLOAD_DB", str(uploads))
    ir = {"objects": [
        {"id": "a", "table": "fact_order"},
        {"id": "b", "tables": ["upload_order"]},
        {"id": "c", "table": "external_order"},
    ]}
    profile = server._qa_graph_profile(ir)
    assert profile["queryable"] is True
    assert profile["available_tables"] == ["fact_order", "up.upload_order"]
    assert profile["unavailable_tables"] == ["external_order"]
    summary = server._qa_graph_summary(ir)
    assert summary["available_table_count"] == 2
    assert summary["unavailable_table_count"] == 1
    assert "available_tables" not in summary


def test_qa_anchor_qualifies_upload_and_removes_unavailable_binding(tmp_path, monkeypatch):
    main, uploads = tmp_path / "main.db", tmp_path / "uploads.db"
    _db(main, ["fact_order"]); _db(uploads, ["upload_order"])
    monkeypatch.setattr(server, "DB", str(main))
    monkeypatch.setattr(server, "UPLOAD_DB", str(uploads))
    ir = {"objects": [
        {"id": "a", "table": "fact_order"},
        {"id": "b", "table": "upload_order"},
        {"id": "c", "table": "external_order"},
    ], "links": []}
    monkeypatch.setattr(server, "_anchor_ir", lambda keys: (json.loads(json.dumps(ir)), ["custom"], ""))
    actual, keys, fallback, profile = server._qa_anchor_ir(["custom"])
    assert (keys, fallback, profile["queryable"]) == (["custom"], "", True)
    assert [o.get("table") for o in actual["objects"]] == ["fact_order", "up.upload_order", None]
    assert actual["objects"][2]["qa_unavailable_table"] == "external_order"


def test_selected_unqueryable_ontology_stops_before_llm(monkeypatch):
    ir = {"objects": [{"id": "x", "cn": "外部订单", "table": "not_connected_table"}], "links": []}
    monkeypatch.setattr(server, "_anchor_ir", lambda keys: (json.loads(json.dumps(ir)), ["custom"], ""))
    client = server.app.test_client()
    sync = client.post("/api/chat", json={"q": "订单有多少", "graphs": ["custom"], "nocache": True})
    assert sync.status_code == 422
    assert sync.get_json()["code"] == "ontology_not_queryable"
    stream = client.post("/api/chat/stream", json={"q": "订单有多少", "graphs": ["custom"], "nocache": True})
    body = stream.get_data(as_text=True)
    assert '"code": "ontology_not_queryable"' in body
    assert "尚未接入当前问数连接" in body
    assert "plan_ready" not in body


def test_selected_ontology_strict_gate_rejects_other_catalog_table():
    ir = {"objects": [{"id": "a", "table": "allowed_table"}], "links": []}
    ok, why = server._validate_sql_ontology("SELECT * FROM other_table", ir, strict=True)
    assert ok is False and "不在本体" in why


def test_fallback_answers_customer_ranking_instead_of_monthly_sales():
    plan = server.fallback_plan("各客户的销售订单金额排名")
    assert len(plan["analyses"]) == 1
    analysis = plan["analyses"][0]
    assert analysis["title"] == "客户销售订单金额排名"
    assert "JOIN dim_customer" in analysis["sql"]
    assert "GROUP BY c.cust_id" in analysis["sql"]


def test_selected_built_ontology_executes_correct_fallback_end_to_end(monkeypatch):
    monkeypatch.setattr(server, "agent_sql_plan", lambda *_a, **_kw: None)
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: None)
    monkeypatch.setattr(server, "narrative_llm", lambda *_a, **_kw: None)
    monkeypatch.setattr(usage_stat, "record", lambda *_a, **_kw: None)
    server._QA_CACHE.clear()
    response = server.app.test_client().post("/api/chat", json={
        "q": "各客户的销售订单金额排名", "graphs": ["built_qsdemo"], "nocache": True})
    assert response.status_code == 200
    data = response.get_json()
    assert data["anchor"]["ontology"]["keys"] == ["built_qsdemo"]
    assert data["results"][0]["title"] == "客户销售订单金额排名"
    assert data["results"][0]["data"]["rows"]
    assert set(data["results"][0]["data"]["columns"]) == {"客户", "订单金额"}


def test_ui_has_direct_entry_dynamic_scope_and_conversation_snapshot():
    assert 'id="g_ask"' in UI and "dqStartFromOntology($('#g_sel').value)" in UI
    assert "/assets/modules/qa-ontology.js" in UI
    assert "dqStartFromOntology" in QA_UI
    assert "scope: dqScopeSnapshot()" in QA_HISTORY
    assert "dqRestoreScope(c)" in QA_HISTORY
    assert "graphNames: dqScopeNames(DQ_DS)" in QA_UI
    assert "availableCount: dqScopeAvailable(DQ_DS)" in QA_UI
    assert "anchor.ontology.names" in QA_UI
    assert "新对话将持续使用该本体" in QA_UI
    assert "全选可问数图谱" in UI

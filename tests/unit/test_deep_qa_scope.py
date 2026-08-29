# -*- coding: utf-8 -*-
"""深度问数同步/流式端点的本体作用域一致性（DR-033）。"""
import server
import usage_stat


def test_nonstream_chat_uses_selected_graph_and_tables(monkeypatch):
    calls = {}
    ir = {"objects": [{"id": "supplier", "cn": "供应商", "table": "dim_supplier"}], "relations": []}

    def fake_anchor(keys):
        calls["anchor"] = list(keys or [])
        return ir, list(keys or ["demo"]), ""

    def fake_context(question, focus_tables=None, trace=None, graph_keys=None):
        calls["context"] = {"question": question, "tables": list(focus_tables or []),
                            "graphs": list(graph_keys or [])}
        return "表 dim_supplier(supplier_id, supplier_name)"

    monkeypatch.setattr(server, "_anchor_ir", fake_anchor)
    monkeypatch.setattr(server, "build_context", fake_context)
    monkeypatch.setattr(server, "_graph_name", lambda key: "严格作用域图谱" if key == "built_scoped" else key)
    monkeypatch.setattr(server, "_carryover", lambda q, _h, _ir: (q, ""))
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: {
        "question": "供应商数量", "analyses": [{"title": "供应商数量", "sql": "SELECT 1 AS n"}]})
    monkeypatch.setattr(server, "_validate_sql_ontology", lambda _sql, _ir: (True, ""))
    monkeypatch.setattr(server, "q", lambda _sql, **_kw: {"columns": ["n"], "rows": [{"n": 1}]})
    monkeypatch.setattr(server, "narrative_llm", lambda *_a, **_kw: "1 家供应商")
    monkeypatch.setattr(server, "_metric_cards", lambda *_a, **_kw: [])
    monkeypatch.setattr(usage_stat, "record", lambda *_a, **_kw: None)
    server._QA_CACHE.clear()

    response = server.app.test_client().post("/api/chat", json={
        "q": "供应商有多少家？", "graphs": ["built_scoped"],
        "tables": ["dim_supplier"], "nocache": True})
    assert response.status_code == 200
    body = response.get_json()
    assert calls["anchor"] == ["built_scoped"]
    assert calls["context"]["graphs"] == ["built_scoped"]
    assert calls["context"]["tables"] == ["dim_supplier"]
    assert "严格作用域图谱" in body["steps"][0]["info"]

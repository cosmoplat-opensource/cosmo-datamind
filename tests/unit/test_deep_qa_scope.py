# -*- coding: utf-8 -*-
"""深度问数同步/流式端点的本体作用域一致性（DR-033）。"""
import server
import usage_stat
import os
import sqlite3


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
    # 新增的可执行性守卫(_qa_graph_profile)会核对绑定表是否在当前连接里;
    # 单元环境没有真库,声明 dim_supplier 可用以进入被测路径,守卫本身另有专测。
    monkeypatch.setattr(server, "_qa_table_inventory", lambda: ({"dim_supplier"}, set()))
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


def test_cache_bypass_removes_old_value_and_does_not_write_result(monkeypatch):
    """交互式强制刷新必须是不读、不写，并清掉同问题的陈旧缓存。"""
    monkeypatch.setattr(server, "_qa_anchor_ir", lambda _keys: (
        {"objects": [], "links": []}, ["demo"], "", {"queryable": True}))
    monkeypatch.setattr(server, "_carryover", lambda question, _history, _ir: (question, ""))
    monkeypatch.setattr(server, "build_context", lambda *_a, **_kw: "表 fact_sales_order(order_date)")
    monkeypatch.setattr(server, "_match_qa_skill", lambda _q: {
        "question": "最近几个月毛利率的变化趋势",
        "analyses": [{"title": "真实重算", "sql": "SELECT 2 AS fresh"}]})
    monkeypatch.setattr(server, "_qa_validate_sql", lambda *_a, **_kw: (True, ""))
    monkeypatch.setattr(server, "q", lambda *_a, **_kw: {
        "columns": ["fresh"], "rows": [{"fresh": 2}]})
    monkeypatch.setattr(server, "narrative_llm", lambda *_a, **_kw: "本次重新计算")
    monkeypatch.setattr(server, "_metric_cards", lambda *_a, **_kw: [])

    question = "最近几个月毛利率的变化趋势"
    key = server._qa_key(question, [], [], [])
    server._QA_CACHE.clear()
    server._QA_CACHE[key] = {"results": [{"title": "陈旧缓存"}], "narrative": "旧答案"}
    response = server.app.test_client().post("/api/chat", json={
        "q": question, "cache_policy": "bypass"})

    assert response.status_code == 200
    body = response.get_json()
    assert body.get("cached") is not True
    assert body["results"][0]["title"] == "真实重算"
    assert key not in server._QA_CACHE

    # UI 实际使用 SSE 端点；同一策略必须覆盖流式链路，而不只是同步 API。
    server._QA_CACHE[key] = {"results": [{"title": "陈旧缓存"}], "narrative": "旧答案"}
    streamed = server.app.test_client().post("/api/chat/stream", json={
        "q": question, "cache_policy": "bypass"}).get_data(as_text=True)
    assert '"cached": false' in streamed
    assert "真实重算" in streamed
    assert "陈旧缓存" not in streamed
    assert key not in server._QA_CACHE


def test_cache_policy_parses_legacy_and_explicit_values():
    assert server._qa_bypass_cache({"cache_policy": "bypass"}) is True
    assert server._qa_bypass_cache({"nocache": "false"}) is False
    assert server._qa_bypass_cache({"nocache": "1"}) is True


def test_answer_cache_tracks_source_updates_and_wal(make_sqlite):
    db = make_sqlite({'facts': ('value INTEGER', [(1,)])})
    token = server._QA_DB.set(db)
    try:
        with sqlite3.connect(db) as con:
            con.execute('PRAGMA journal_mode=WAL')
            before = server._qa_key('数值是多少', [])
            con.execute('UPDATE facts SET value=2')
            con.commit()
            assert server._qa_key('数值是多少', []) != before
        other = make_sqlite({'facts': ('value INTEGER', [(3,)])}, fname='other.db')
        previous = server._qa_key('数值是多少', [])
        second = server._QA_DB.set(other)
        try:
            assert server._qa_key('数值是多少', []) != previous
        finally:
            server._QA_DB.reset(second)
    finally:
        server._QA_DB.reset(token)


def test_answer_cache_tracks_graph_and_subsecond_edits(monkeypatch, tmp_path):
    graph = tmp_path / 'built_test.json'
    edits = tmp_path / 'built_test.edits.json'
    graph.write_text('{}')
    edits.write_text('[]')
    monkeypatch.setattr(server, 'WORK', str(tmp_path))
    monkeypatch.setattr(server, '_edits_path', lambda key: str(edits))
    before = server._qa_key('数量', [], graphs=['built_test'])
    graph.write_text('{"objects": []}')
    after_graph = server._qa_key('数量', [], graphs=['built_test'])
    assert after_graph != before
    stat = edits.stat()
    os.utime(edits, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    assert server._qa_key('数量', [], graphs=['built_test']) != after_graph


def test_answer_cache_distinguishes_summary_and_cleaning_policy():
    before = server._qa_key('它有多少', [{'q': '客户', 'summary': '客户 A'}])
    assert server._qa_key('它有多少', [{'q': '客户', 'summary': '客户 B'}]) != before
    assert server._qa_key('数量', [], clean=False) != server._qa_key('数量', [], clean=True)

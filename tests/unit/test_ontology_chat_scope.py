# -*- coding: utf-8 -*-
"""本体对话的图谱作用域回归锁（DR-027）。"""
import pathlib

import server


UI = (pathlib.Path(__file__).resolve().parents[2] / "ui" / "index.html").read_text(encoding="utf-8")


def test_chat_ui_has_explicit_graph_selector_and_no_demo_hardcode():
    assert 'id="claw_graph"' in UI
    claw_code = UI[UI.index("let CLAW_ID"):UI.index("async function library")]
    assert "clawGraph()" in claw_code
    assert "graph:'demo'" not in claw_code
    assert "graph=demo" not in claw_code
    assert "/api/ont/audit/demo" not in claw_code


def test_chat_ui_refreshes_session_and_audit_after_mutation():
    claw_code = UI[UI.index("let CLAW_ID"):UI.index("async function library")]
    apply_body = claw_code[claw_code.index("async function clawApply"):claw_code.index("async function clawDel")]
    undo_body = claw_code[claw_code.index("async function clawUndo"):claw_code.index("function clawSaveWho")]
    ask_body = claw_code[claw_code.index("async function clawAsk"):claw_code.index("async function clawApply")]
    assert "clawAudit()" in apply_body
    assert "clawAudit()" in undo_body
    assert "clawSessions()" in ask_body


def test_chat_sessions_are_created_and_filtered_by_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CHATS_F", str(tmp_path / "chats.json"))
    client = server.app.test_client()

    created = client.post("/api/ont/chats/new", json={"graph": "built_regress"})
    assert created.status_code == 200
    cid = created.get_json()["id"]
    session = client.get(f"/api/ont/chats/{cid}").get_json()
    assert session["graph"] == "built_regress"

    assert [row["id"] for row in client.get("/api/ont/chats?graph=built_regress").get_json()] == [cid]
    assert client.get("/api/ont/chats?graph=demo").get_json() == []


def test_legacy_chat_without_scope_is_treated_as_demo(tmp_path, monkeypatch):
    chats_file = tmp_path / "chats.json"
    chats_file.write_text('{"legacy":{"title":"旧会话","messages":[]}}', encoding="utf-8")
    monkeypatch.setattr(server, "CHATS_F", str(chats_file))
    client = server.app.test_client()
    rows = client.get("/api/ont/chats?graph=demo").get_json()
    assert rows[0]["graph"] == "demo"

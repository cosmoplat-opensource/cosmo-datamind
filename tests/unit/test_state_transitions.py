"""Persistence across capacity limits and overlapping, legitimate user actions."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest

import server


@pytest.mark.parametrize("count", [50, 51])
def test_board_collection_preserves_acknowledged_saves(count, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_VIZ_BOARDS_F", str(tmp_path / "boards.json"))
    client = server.app.test_client()
    accepted = {}
    for i in range(count):
        chart = {"title": f"measure {i}", "sql": f"SELECT {i} AS n", "type": "kpi",
                 "src": "demo", "x": "n", "ys": ["n"]}
        name = f"board {i}"
        response = client.post("/api/viz/save", json={"name": name, "charts": [chart]})
        if i == 50 and 400 <= response.status_code < 500:
            assert response.json.get("error")
        else:
            assert response.status_code == 200 and response.json["ok"]
            accepted[response.json["id"]] = (name, [chart])
    stored = client.get("/api/viz/boards").json
    assert {row["id"]: (row["name"], row["charts"]) for row in stored} == accepted


@pytest.mark.parametrize("count", [30, 31])
def test_registered_sources_still_resolve_after_next_registration(count, make_sqlite, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    client = server.app.test_client()
    accepted = {}
    for i in range(count):
        path = make_sqlite({"reading": ("value INTEGER", [(i,)])}, fname=f"source-{i}.db")
        response = client.post("/api/build/connect", json={"kind": "sqlite", "name": f"source {i}", "path": str(path)})
        if i == 30 and 400 <= response.status_code < 500:
            assert response.json.get("error")
        else:
            assert response.status_code == 200 and response.json["ok"]
            accepted[response.json["conn"]["id"]] = i
    listed = {source["id"] for source in client.get("/api/build/sources").json["sources"]}
    for source_id, value in accepted.items():
        assert source_id in listed, f"previously registered source {value} disappeared"
        result = client.post("/api/query", json={"src": source_id, "sql": "SELECT value FROM reading"})
        assert result.json["rows"] == [{"value": value}]


@pytest.mark.parametrize("overlap", ["different-chat", "same-chat", "delete-chat"])
def test_chat_completed_during_another_turn_is_durable(overlap, tmp_path, monkeypatch):
    import agent_runtime

    monkeypatch.setattr(server, "CHATS_F", str(tmp_path / "chats.json"))
    client = server.app.test_client()
    first = client.post("/api/ont/chats/new", json={"graph": "demo"}).json["id"]
    second = (client.post("/api/ont/chats/new", json={"graph": "demo"}).json["id"]
              if overlap == "different-chat" else first)
    answering, release = Event(), Event()

    class LocalRuntime:
        def run_turn(self, cid, prompt, timeout):
            if "用户: first question" in prompt:
                answering.set()
                assert release.wait(10)
            return True, "offline fixture reply"

    monkeypatch.setattr(agent_runtime, "available", lambda: ["fixture"])
    monkeypatch.setattr(server, "_drv_order", lambda: ["fixture"])
    monkeypatch.setattr(server, "runtime_cached", lambda driver: LocalRuntime())

    def post(path, body):
        with server.app.test_client() as local:
            response = local.post(path, json=body)
            return response.status_code, response.json

    def turn(ident, message):
        return post("/api/ont/chat", {"id": ident, "graph": "demo", "message": message})

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(turn, first, "first question")
        try:
            assert answering.wait(5)
            if overlap == "delete-chat":
                other = pool.submit(post, "/api/ont/chats/delete", {"id": second})
            else:
                other = pool.submit(turn, second, "second question")
            try:
                other.result(timeout=2)  # A serializing implementation may still be processing the first turn.
            except TimeoutError:
                pass
        finally:
            release.set()
        first_status, first_data = slow.result(timeout=5)
        other_status, other_data = other.result(timeout=5)

    if overlap == "delete-chat":
        if other_status == 409:
            assert other_data.get("error") and client.get("/api/ont/chats/" + first).status_code == 200
            other_status, other_data = post("/api/ont/chats/delete", {"id": first})
        assert other_status == 200 and other_data.get("ok")
        assert client.get("/api/ont/chats/" + first).status_code == 404
        return
    accepted = {}
    for ident, question, status, data in [(first, "first question", first_status, first_data),
                                           (second, "second question", other_status, other_data)]:
        if status == 409 and overlap == "same-chat":
            assert data.get("error")
            continue
        assert status == 200 and data.get("reply")
        accepted.setdefault(ident, []).append(question)
    assert accepted
    for ident, questions in accepted.items():
        messages = client.get("/api/ont/chats/" + ident).json["messages"]
        assert sorted(m["text"] for m in messages if m["role"] == "user") == sorted(questions)
        assert len([m for m in messages if m["role"] == "ai"]) == len(questions)

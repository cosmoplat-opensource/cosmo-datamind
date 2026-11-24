"""Check persisted results when one successful workflow is followed by another."""
from io import BytesIO
import json
import sqlite3

import pytest
import server

from tests.result_scope_support import action_ledger
from tests.unit.test_result_scope import api_sources, client, fetch

__all__ = ["action_ledger", "api_sources", "client"]


@pytest.mark.parametrize("filename", ["shift.csv", "api_daily.csv"])
@pytest.mark.parametrize("first", ["file", "api"])
def test_upload_and_api_source_preserve_both_datasets(client, api_sources, tmp_path, monkeypatch, filename, first):
    register, db_path = api_sources
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    cid = register("daily", [{"units": 22}])

    def upload():
        response = client.post("/api/build/upload", data={"files": (BytesIO(b"units\n11\n"), filename)})
        assert response.status_code == 200 and len(response.json["tables"]) == 1, response.json
        record = response.json["tables"][0]
        if record.get("error"):
            assert first == "api" and filename == "api_daily.csv" and not response.json.get("saved")
            return None
        return record["table"]

    def materialize():
        status, result = fetch(client, cid)
        if status == 409:
            assert first == "file" and filename == "api_daily.csv" and result.get("error")
            return None
        assert status == 200, result
        return result["table"].removeprefix("up.")

    if first == "file":
        file_table, api_table = upload(), materialize()
    else:
        api_table, file_table = materialize(), upload()
    with sqlite3.connect(db_path) as db:
        imported = [(table, units) for table, units in [(file_table, 11), (api_table, 22)] if table]
        values = [int(db.execute(f'SELECT units FROM "{table}"').fetchone()[0]) for table, _ in imported]
    assert values == [units for _, units in imported], {"file_table": file_table, "api_table": api_table, "values": values}


def test_deleting_uploaded_file_keeps_api_table_with_long_suffix(client, api_sources, tmp_path, monkeypatch):
    register, db_path = api_sources
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    status, result = fetch(client, register("daily_long", [{"units": 22}]))
    assert status == 200 and result["table"] == "up.api_daily_long"
    name = "api_daily.csv"
    uploaded = client.post("/api/build/upload", data={"files": (BytesIO(b"units\n11\n"), name)})
    assert uploaded.json["saved"] == [name]
    deleted = client.post("/api/build/asset/delete", json={"name": name})
    assert deleted.json["dropped_table"] == "api_daily"
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT units FROM api_daily_long").fetchall() == [("22",)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'api_daily'").fetchall() == []


@pytest.mark.parametrize("replacement", ["weekly", "daily"])
def test_new_connection_needs_its_own_first_fetch(client, api_sources, replacement):
    register, db_path = api_sources
    original = register("daily", [{"units": 11}])
    status, first = fetch(client, original)
    assert status == 200
    assert client.post("/api/build/connect/delete", json={"id": original}).json["ok"]
    current = register(replacement, [{"units": 22}])
    assert current != original
    sources = {s["id"]: s for s in client.get("/api/build/sources").json["sources"]}
    assert sources[current]["ready"] is False
    status, second = fetch(client, current)
    assert status == 200
    with sqlite3.connect(db_path) as db:
        for result, expected in [(first, "11"), (second, "22")]:
            table = result["table"].removeprefix("up.")
            assert db.execute(f'SELECT units FROM "{table}"').fetchall() == [(expected,)]


def test_api_binding_write_failure_rolls_back_and_can_retry(client, api_sources, monkeypatch):
    register, db_path = api_sources
    rows = [{"units": 11}]
    cid = register("daily", rows)
    assert fetch(client, cid)[0] == 200
    rows[0]["units"] = 22

    def disk_full(*args):
        raise OSError("simulated full disk")

    with monkeypatch.context() as fail:
        fail.setattr(server, "_atomic_json", disk_full)
        assert fetch(client, cid)[0] == 502
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT units FROM api_daily").fetchall() == [("11",)]
    assert fetch(client, cid)[0] == 200
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT units FROM api_daily").fetchall() == [("22",)]


@pytest.mark.parametrize("late_column_at", [5000, 5001])
def test_import_total_columns_includes_fields_first_seen_after_row_limit(client, api_sources, late_column_at):
    register, _ = api_sources
    rows = [{"units": i} for i in range(1, 5002)]
    rows[late_column_at - 1]["adjustment"] = 33
    status, result = fetch(client, register("hourly", rows))
    assert status == 200 and result["total_rows"] == 5001, result
    assert result["total_columns"] == len(set().union(*(row.keys() for row in rows))), {
        "first_adjustment_row": late_column_at, "total_columns": result["total_columns"], "imported": result["columns"]}


@pytest.mark.parametrize("newer", [198, 199, 999])
def test_unknown_execution_remains_available_for_manual_resolution(client, action_ledger, newer):
    path = action_ledger(newer)
    rows = json.loads(path.read_text())
    rows[-1]["status"] = "unknown"
    path.write_text(json.dumps(rows), encoding="utf-8")
    latest = client.post("/api/action/invoke", json={"action_id": "act_scope", "operator": "班长", "params": {}})
    assert latest.status_code == 200
    pending = client.get("/api/actions").json["unknown"]
    listed = any(row["id"] == "evt_0" for row in client.get("/api/action/log").json["items"])
    result = client.post("/api/action/resolve", json={"id": "evt_0", "outcome": "failed",
                                                      "operator": "核对员", "comment": "已核对业务回执"})
    assert (pending, listed, result.status_code) == (1, True, 200), {
        "unknown_count": pending, "reachable_in_log": listed, "manual_resolution": result.status_code}


@pytest.mark.parametrize("open_count", [999, 1000])
def test_successful_record_is_readable_when_approval_queue_is_full(client, action_ledger, open_count):
    path = action_ledger(open_count - 1)
    rows = json.loads(path.read_text())
    for row in rows:
        row["status"] = "pending"
    path.write_text(json.dumps(rows), encoding="utf-8")
    response = client.post("/api/action/invoke", json={"action_id": "act_scope", "operator": "班长", "params": {}})
    stored = json.loads(path.read_text())
    assert {r["id"] for r in stored if r["status"] == "pending"} == {r["id"] for r in rows}
    if response.status_code == 409:
        assert response.json.get("error")
    else:
        assert response.status_code == 200 and response.json["ok"]
        assert any(r["id"] == response.json["id"] for r in stored), {
            "receipt": response.json["id"], "stored": len(stored), "pending_before": open_count}

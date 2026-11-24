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


@pytest.mark.parametrize("replacement", ["weekly", "daily"])
def test_new_connection_needs_its_own_first_fetch(client, api_sources, replacement):
    register, _ = api_sources
    original = register("daily", [{"units": 11}])
    assert fetch(client, original)[0] == 200
    assert client.post("/api/build/connect/delete", json={"id": original}).json["ok"]
    current = register(replacement, [{"units": 22}])
    assert current != original
    sources = {s["id"]: s for s in client.get("/api/build/sources").json["sources"]}
    assert sources[current]["ready"] is False


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

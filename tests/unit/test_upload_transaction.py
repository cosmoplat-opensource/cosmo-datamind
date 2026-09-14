"""Real-deployment regression: failed CSV refresh must preserve file and table."""
from io import BytesIO
import sqlite3

import pytest
import server


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "uploads.db"))
    client = server.app.test_client()

    def send(raw, name="orders.csv"):
        return client.post("/api/build/upload", data={"files": (BytesIO(raw), name)},
                           content_type="multipart/form-data").get_json()

    return tmp_path, send


@pytest.mark.parametrize("invalid", [b"id,id\n3,99\n", b"item-id,item id\n3,99\n", b"id,amount\n3,99,extra\n"])
def test_invalid_refresh_preserves_original_file_and_table(uploads, invalid):
    directory, send = uploads
    original = b"id,amount\n1,10\n2,20\n"
    assert send(original)["saved"] == ["orders.csv"]
    result = send(invalid)
    assert result["tables"][0].get("error")
    assert result["saved"] == []
    assert (directory / "uploads_orders.csv").read_bytes() == original
    with sqlite3.connect(directory / "uploads.db") as con:
        assert con.execute("SELECT * FROM orders ORDER BY id").fetchall() == [("1", "10"), ("2", "20")]


def test_file_write_failure_rolls_back_csv_replacement(uploads, monkeypatch):
    directory, send = uploads
    original = b"id,amount\n1,10\n"
    send(original)

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(server, "_atomic_bytes", fail)
    result = send(b"id,amount\n2,20\n")
    assert result["tables"][0].get("error")
    assert (directory / "uploads_orders.csv").read_bytes() == original
    with sqlite3.connect(directory / "uploads.db") as con:
        assert con.execute("SELECT * FROM orders").fetchall() == [("1", "10")]


def test_distinct_filenames_cannot_overwrite_same_normalized_table(uploads):
    directory, send = uploads
    send(b"id,amount\n1,10\n", "order-data.csv")
    result = send(b"id,amount\n2,20\n", "order_data.csv")
    assert result["tables"][0].get("error")
    assert not (directory / "uploads_order_data.csv").exists()
    with sqlite3.connect(directory / "uploads.db") as con:
        assert con.execute("SELECT * FROM order_data").fetchall() == [("1", "10")]

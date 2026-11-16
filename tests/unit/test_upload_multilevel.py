# -*- coding: utf-8 -*-
"""上传带多级表头的 CSV(DR-135):合并成一行列名入表;上层是期间时另存一张长表,可按期间查数。"""
import io
import sqlite3

import server

CSV = "地区,2025年,,2026年,\n,收入,成本,收入,成本\n华东,10,6,12,7\n华北,8,5,9,5.5\n"


def test_upload_creates_the_wide_and_the_long_table(tmp_path, monkeypatch):
    up = tmp_path / "uploads.db"
    monkeypatch.setattr(server, "UPLOAD_DB", str(up))
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    r = server.app.test_client().post("/api/build/upload", data={"files": (io.BytesIO(CSV.encode()), "regional.csv")},
                                      content_type="multipart/form-data")
    body = r.get_json()
    info = body["tables"][0]
    assert info["header_rows"] == 2 and info["rows"] == 2 and info["long_table"] == info["table"] + "_long"
    con = sqlite3.connect(up)
    cols = [c[1] for c in con.execute(f'PRAGMA table_info("{info["table"]}")')]
    assert cols == ["地区", "2025年_收入", "2025年_成本", "2026年_收入", "2026年_成本"]
    assert con.execute(f'SELECT "2026年_成本" FROM "{info["table"]}" WHERE "地区"=\'华北\'').fetchone() == (5.5,)
    long = con.execute(f'SELECT 期间, SUM(值) FROM "{info["long_table"]}" WHERE 项目=\'收入\' GROUP BY 1 ORDER BY 1').fetchall()
    assert long == [("2025年", 18.0), ("2026年", 21.0)]


def test_deleting_the_upload_also_removes_the_long_table(tmp_path, monkeypatch):
    up = tmp_path / "uploads.db"
    monkeypatch.setattr(server, "UPLOAD_DB", str(up))
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    c = server.app.test_client()
    info = c.post("/api/build/upload", data={"files": (io.BytesIO(CSV.encode()), "regional.csv")},
                  content_type="multipart/form-data").get_json()["tables"][0]
    assert c.post("/api/build/asset/delete", json={"name": "regional.csv"}).status_code == 200
    names = {r[0] for r in sqlite3.connect(up).execute("SELECT name FROM sqlite_master")}
    assert info["table"] not in names and info["long_table"] not in names


def test_a_plain_csv_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "uploads.db"))
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    info = server.app.test_client().post("/api/build/upload", data={"files": (io.BytesIO("a,b\n1,2\n".encode()), "p.csv")},
                                         content_type="multipart/form-data").get_json()["tables"][0]
    assert "header_rows" not in info and "long_table" not in info

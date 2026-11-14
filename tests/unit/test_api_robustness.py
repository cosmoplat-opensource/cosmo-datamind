# -*- coding: utf-8 -*-
"""探索性测试查出的后端缺陷(DR-111):单元格无大小上限、入参类型不对返回 500、上传不认常见的中文文件、
SPARQL 的 ASK / CONSTRUCT 结果不对、无效的构建请求也建作业、缺安全响应头。"""
import sqlite3
from io import BytesIO

import pytest

import server

client = server.app.test_client()


# ── 查询结果的大小 ──────────────────────────────────────────────────

def test_a_huge_value_is_refused_by_the_engine(make_sqlite):
    db = make_sqlite({"t": ("id INTEGER", [(1,)])})
    with pytest.raises(sqlite3.Error):
        server.q("SELECT hex(zeroblob(200000000))", db=db)


def test_long_cells_are_truncated_and_flagged(make_sqlite):
    db = make_sqlite({"t": ("id INTEGER, note TEXT, raw BLOB", [(1, "长" * 50000, b"\x01" * 50000), (2, "短", b"ab")])})
    out = server.q("SELECT id, note, raw FROM t ORDER BY id", db=db)
    big, small = out["rows"]
    assert len(big["note"]) < 25000 and big["note"].startswith("长" * 100) and "50000" in big["note"]
    assert small["note"] == "短" and out["truncated_cells"] == 2
    server.app.json.dumps(out)                                    # 二进制列也能序列化
    assert "truncated_cells" not in server.q("SELECT id FROM t", db=db)


# ── 入参类型不对时是 400,不是 500 ───────────────────────────────────

@pytest.mark.parametrize("path,body", [
    ("/api/ont/apply", {"graph": "demo", "op": "x"}),
    ("/api/ont/apply", {"graph": ["demo"], "op": {"op": "confirm_relation"}}),
    ("/api/ont/chats/delete", {"id": {"a": 1}}),
    ("/api/ont/forged/delete", {"id": {"a": 1}}),
    ("/api/ont/forged/delete", {"id": None}),
    ("/api/ont/chain", {"graph": ["demo"], "chain": ["a", "b"]}),
    ("/api/ont/cq", {"graph": ["demo"]}),
    ("/api/build/delete", {"key": None}),
    ("/api/build/delete", {"key": ["built_x"]}),
    ("/api/viz/delete", {"id": {"a": 1}}),
])
def test_wrongly_typed_fields_are_a_client_error(path, body):
    r = client.post(path, json=body)
    assert r.status_code == 400 and r.is_json, (path, r.status_code, r.data[:120])


def test_an_unexpected_failure_on_an_api_route_is_reported_as_json(monkeypatch):
    monkeypatch.setattr(server, "table_list", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret detail")))
    server.app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        r = client.get("/api/tables")
    finally:
        server.app.config["PROPAGATE_EXCEPTIONS"] = None
    assert r.status_code == 500 and r.is_json
    assert "secret detail" not in r.get_data(as_text=True) and r.get_json()["error"]


# ── 上传 ────────────────────────────────────────────────────────────

@pytest.fixture
def upload(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "uploads.db"))

    def send(raw, name):
        r = client.post("/api/build/upload", data={"files": (BytesIO(raw), name)}, content_type="multipart/form-data")
        return r.get_json()

    def rows(sql):
        con = sqlite3.connect(str(tmp_path / "uploads.db"))
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()
    return send, rows


def test_gbk_file_is_imported(upload):
    send, rows = upload
    d = send("编号,名称\n1,张三\n2,李四\n".encode("gbk"), "people.csv")
    assert d["tables"] == [{"table": "people", "rows": 2, "encoding": "gb18030"}]
    assert rows('SELECT "名称" FROM people ORDER BY "编号"') == [("张三",), ("李四",)]


def test_semicolon_separated_file_is_split_into_columns(upload):
    send, rows = upload
    d = send(b"id;name;amt\n1;a;3.5\n2;b;4\n", "semi.csv")
    assert d["tables"][0]["rows"] == 2 and d["tables"][0]["delimiter"] == ";"
    assert rows("SELECT name, amt FROM semi ORDER BY id") == [("a", 3.5), ("b", 4.0)]


def test_a_comma_file_with_semicolons_in_values_is_left_alone(upload):
    send, rows = upload
    send("id,note\n1,甲;乙\n2,丙;丁\n".encode(), "notes.csv")
    assert rows("SELECT note FROM notes ORDER BY id") == [("甲;乙",), ("丙;丁",)]


def test_chinese_file_name_keeps_a_readable_table_name(upload):
    send, rows = upload
    d = send(b"id,amt\n1,10\n", "订单 表(1).csv")
    assert d["tables"][0]["table"] == "订单_表_1_"
    assert rows('SELECT amt FROM "订单_表_1_"') == [(10,)]
    assert send(b"id\n1\n", "order-data v2.csv")["tables"][0]["table"] == "order_data_v2"     # 英文文件名的表名不变


def test_deleting_an_upload_also_removes_a_table_made_under_the_old_naming_rule(upload, tmp_path):
    send, rows = upload
    send(b"id,amt\n1,10\n", "订单 表(1).csv")
    con = sqlite3.connect(str(tmp_path / "uploads.db"))
    con.execute('CREATE TABLE "_____1_" (id TEXT)'); con.commit(); con.close()      # 升级前建的表
    r = client.post("/api/build/asset/delete", json={"name": "订单 表(1).csv"}).get_json()
    assert r["ok"] and r["dropped_table"] == "订单_表_1_"
    assert rows("SELECT name FROM sqlite_master WHERE type='table'") == []


def test_numeric_columns_get_numeric_types_and_codes_stay_text(upload):
    send, rows = upload
    send(b"id,amt,code,mixed,empty_ok\n1,10.5,007,1,\n2,3,010,x,5\n", "typed.csv")
    types = {r[1]: r[2] for r in rows("PRAGMA table_info(typed)")}
    assert types == {"id": "INTEGER", "amt": "REAL", "code": "TEXT", "mixed": "TEXT", "empty_ok": "INTEGER"}
    assert rows("SELECT typeof(id), typeof(amt), code, typeof(empty_ok) FROM typed ORDER BY id") == [
        ("integer", "real", "007", "null"), ("integer", "real", "010", "integer")]


def test_unparsed_files_and_bad_files_say_what_happened(upload):
    send, _rows = upload
    d = send(b"PK\x03\x04not-a-table", "book.xlsx")
    assert d["saved"] == ["book.xlsx"] and d["notes"] == [{"file": "book.xlsx", "note": "已存档,未解析为表(只有 CSV/TSV 会建表)"}]
    wide = (",".join(f"c{i}" for i in range(3000)) + "\n" + ",".join("1" for _ in range(3000)) + "\n").encode()
    err = send(wide, "wide.csv")["tables"][0]["error"]
    assert "列" in err and "too many" not in err
    err = send(b"\xff\xfe\x00\x00\x81", "bin.csv")["tables"][0]["error"]
    assert "编码" in err and "codec" not in err


# ── SPARQL ──────────────────────────────────────────────────────────

def test_ask_returns_a_boolean():
    d = client.post("/api/sparql", json={"graph": "demo", "query": "ASK { ?s ?p ?o }"}).get_json()
    assert d["type"] == "ASK" and d["boolean"] is True
    d = client.post("/api/sparql", json={"graph": "demo", "query": "ASK { <urn:x:none> <urn:x:p> <urn:x:o> }"}).get_json()
    assert d["boolean"] is False


def test_construct_returns_triples():
    d = client.post("/api/sparql", json={"graph": "demo",
                                         "query": "CONSTRUCT { ?s ?p ?o } WHERE { ?s ?p ?o } LIMIT 3"}).get_json()
    assert d["type"] == "CONSTRUCT" and d["vars"] == ["s", "p", "o"] and d["total"] == 3
    assert all(r["s"] and r["p"] and r["o"] for r in d["rows"])


def test_select_is_unchanged():
    d = client.post("/api/sparql", json={"graph": "demo", "query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 2"}).get_json()
    assert d["type"] == "SELECT" and d["vars"] == ["s"] and d["total"] == 2


# ── 构建请求 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,word", [
    ({"q": "", "source": "demo"}, "诉求"), ({"q": "   ", "source": "demo"}, "诉求"),
    ({"q": "建本体", "source": "no_such_source"}, "数据源"), ({"q": ["x"], "source": "demo"}, "诉求"),
])
def test_an_invalid_build_request_does_not_create_a_job(body, word):
    before = len(server._BUILD_JOBS)
    r = client.post("/api/build/jobs", json=body)
    assert r.status_code == 400 and word in r.get_json()["error"] and len(server._BUILD_JOBS) == before


# ── 响应头 ──────────────────────────────────────────────────────────

def test_responses_carry_baseline_security_headers():
    for path in ("/", "/api/health"):
        h = client.get(path).headers
        assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "SAMEORIGIN"
        assert h["Referrer-Policy"] == "same-origin" and "frame-ancestors 'self'" in h["Content-Security-Policy"]

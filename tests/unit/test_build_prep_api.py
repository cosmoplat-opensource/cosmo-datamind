# -*- coding: utf-8 -*-
"""构建前预处理接入流水线与接口:体检接口、只读抽样接口的输入契约、分轮范围、库表注释证据。"""
import json

import pytest

server = pytest.importorskip("server")


@pytest.fixture
def work(tmp_path, monkeypatch):
    w = tmp_path / "work"; w.mkdir()
    monkeypatch.setattr(server, "WORK", str(w))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(w / "build_connections.json"))
    return w


def _conns(work, items):
    (work / "build_connections.json").write_text(json.dumps(items), encoding="utf-8")


class TestPrepEndpoint:
    def test_prep_on_registered_sqlite(self, make_sqlite, work):
        db = make_sqlite({"meta__dict_type": ("id INTEGER PRIMARY KEY, code TEXT, is_del INTEGER", [(1, "a", 0)]),
                          "ems__energy_bill": ("id INTEGER PRIMARY KEY, obj_type INTEGER, obj_no TEXT", [(1, 0, "D1")])})
        _conns(work, [{"id": "conn_x", "name": "测试库", "kind": "sqlite", "path": db}])
        (work / "uploads_d.md").write_text("### energy_bill —— 能耗账单\n| a | b |", encoding="utf-8")
        r = server.app.test_client().post("/api/build/prep", json={"source": "conn_x"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["source"]["name"] == "测试库" and d["summary"]["tables"] == 2
        assert d["docs"]["described"] == 1
        assert d["plan"] and d["plan"][0]["tables"][0] == "meta__dict_type"

    def test_external_source_is_told_to_sample_first(self, work):
        _conns(work, [{"id": "conn_e", "name": "外部", "kind": "external", "dsn": "jdbc:mysql://h:3306/db"}])
        r = server.app.test_client().post("/api/build/prep", json={"source": "conn_e"})
        assert r.status_code == 400 and r.get_json()["external"] is True
        assert "只读抽样" in r.get_json()["error"]

    def test_invalid_max_tables(self, work):
        r = server.app.test_client().post("/api/build/prep", json={"source": "demo", "max_tables": "x"})
        assert r.status_code == 400


class TestSampleEndpointContract:
    """抽样接口在触网之前就要把不合法的请求挡掉。"""

    def test_rejects_non_external_and_unknown(self, make_sqlite, work):
        db = make_sqlite({"t": ("id INTEGER", [])})
        _conns(work, [{"id": "conn_s", "name": "本地", "kind": "sqlite", "path": db}])
        c = server.app.test_client()
        assert c.post("/api/build/connect/sample", json={"ids": ["conn_s"]}).status_code == 400
        assert c.post("/api/build/connect/sample", json={"ids": ["nope"]}).status_code == 400
        assert c.post("/api/build/connect/sample", json={"ids": []}).status_code == 400
        assert c.post("/api/build/connect/sample", json={"ids": "conn_s"}).status_code == 400

    def test_rejects_unsupported_kind(self, work):
        _conns(work, [{"id": "conn_h", "name": "Hive", "kind": "external", "dsn": "jdbc:hive2://h:10000/db"}])
        r = server.app.test_client().post("/api/build/connect/sample", json={"ids": ["conn_h"]})
        assert r.status_code == 400 and "MySQL/Doris" in r.get_json()["error"]


class TestRoundScope:
    def test_scope_limits_schema_text_but_keeps_all_tables_for_adjudication(self, make_sqlite, work):
        db = make_sqlite({"a_order": ("id INTEGER PRIMARY KEY, cust_id INTEGER", []),
                          "b_customer": ("id INTEGER PRIMARY KEY", []),
                          "c_other": ("id INTEGER PRIMARY KEY", [])})
        ev = server._gather_evidence(db, focus=["a_order"])
        assert "a_order(" in ev["schema"] and "c_other(" not in ev["schema"]
        assert set(ev["tab_cols"]) == {"a_order", "b_customer", "c_other"}
        assert ev["scope"] == ["a_order"]

    def test_inquire_rejects_malformed_tables(self):
        r = server.app.test_client().post("/api/build/inquire", json={"q": "x", "tables": "a,b"})
        assert r.status_code == 400 and r.get_json()["field"] == "tables"


def test_sampled_comments_become_retrievable_data_dictionary(make_sqlite, work):
    """只读抽样保留的库表注释,构建时作为「库表注释」证据按表锚定进提示词。"""
    db = make_sqlite({
        "ems__alarm_info": ("id INTEGER PRIMARY KEY, alarm_id INTEGER", []),
        "_dm_tables": ("table_name TEXT, source_table TEXT, comment TEXT, rows_est INTEGER",
                       [("ems__alarm_info", "alarm_info", "告警信息", 0)]),
        "_dm_columns": ("table_name TEXT, column_name TEXT, type TEXT, comment TEXT",
                        [("ems__alarm_info", "alarm_id", "int", "告警id")]),
    })
    ev = server._gather_evidence(db)
    assert "_dm_tables" not in ev["tab_cols"] and "_dm_tables" not in ev["schema"]
    assert "ems__alarm_info —— 告警信息" in ev["docs"] and "alarm_id(int):告警id" in ev["docs"]
    assert any(f["name"] == "库表注释" and f["consumed_as_text"] for f in ev["files"])
    assert "_dm_tables" not in server._sqlite_tables(db)


def test_ui_wires_prep_sampling_and_round_building():
    html = open(server.HERE + "/ui/index.html", encoding="utf-8").read()
    prep = open(server.HERE + "/ui/modules/build-prep.js", encoding="utf-8").read()
    stream = open(server.HERE + "/ui/modules/build-stream.js", encoding="utf-8").read()
    assert 'onclick="bcPrep()"' in html and 'id="bc_sample_modal"' in html
    assert "/assets/modules/build-prep.js" in html and 'id="bcc_pwd"' in html
    assert "前 3500 字符" not in html
    assert "/api/build/prep" in prep and "/api/build/connect/sample" in prep
    assert "bcInquire({q, tables: p.tables" in prep
    assert "request.tables=opts.tables" in stream and "return done;" in stream
    # 示例主库的默认诉求与默认名不能被带到别的数据源的分轮构建里(实测曾把本体命名为「示例企业本体」)
    assert "BC.def.build_query" in prep and "BC.def.build_name" in prep

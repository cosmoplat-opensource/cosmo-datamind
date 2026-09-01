# -*- coding: utf-8 -*-
"""数据目录接口的语义标签与资源释放回归。"""
import pytest
import server


def test_table_list_includes_case_insensitive_chinese_name(make_sqlite, monkeypatch):
    db = make_sqlite({"fact_sales_order": ("id INTEGER PRIMARY KEY", [(1,), (2,)])})
    monkeypatch.setattr(server, "load_ir_edited", lambda _key: {
        "objects": [{"table": "FACT_SALES_ORDER", "cn": "销售订单"}],
    })

    assert server.table_list(db) == [{
        "name": "fact_sales_order", "cn": "销售订单", "rows": 2, "cols": 1,
    }]


def test_table_list_closes_connection_when_introspection_fails(monkeypatch):
    class BrokenConnection:
        closed = False

        def execute(self, sql):
            if "sqlite_master" in sql:
                return [("bad_table",)]
            raise RuntimeError("introspection failed")

        def close(self):
            self.closed = True

    con = BrokenConnection()
    monkeypatch.setattr(server, "ro_connect", lambda _db: con)
    monkeypatch.setattr(server, "load_ir_edited", lambda _key: {"objects": []})

    with pytest.raises(RuntimeError, match="introspection failed"):
        server.table_list("ignored.db")
    assert con.closed is True


def test_first_party_assets_are_allowlisted_and_traversal_safe():
    client = server.app.test_client()
    js = client.get("/assets/modules/catalog.js")
    refs = client.get("/assets/modules/build-references.js")
    css = client.get("/assets/styles/responsive.css")
    assert js.status_code == 200 and b"function catalog" in js.data
    assert refs.status_code == 200 and b"bcRefsPayload" in refs.data
    assert css.status_code == 200 and b"@media" in css.data
    assert client.get("/assets/index.html").status_code == 404
    assert client.get("/assets/modules/%2e%2e/index.html").status_code == 404

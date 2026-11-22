"""Check public responses against the registered database and parsed export artifacts."""
from collections import Counter
from decimal import Decimal
from io import BytesIO
import json
import sqlite3
from urllib.parse import quote

import pytest
from rdflib import Graph, OWL, RDF, RDFS

import server


@pytest.fixture
def client():
    return server.app.test_client()


@pytest.fixture
def source(tmp_path, monkeypatch, client):
    db = tmp_path / "meters.db"
    names = ("meter", "月度读数", "shift log", 'meter "A"')
    with sqlite3.connect(db) as conn:
        for table in names:
            ident = '"' + table.replace('"', '""') + '"'
            conn.execute(f"CREATE TABLE {ident} (reading INTEGER, label TEXT)")
            conn.executemany(f"INSERT INTO {ident} VALUES (?,?)", [(11, "上午"), (22, "晚班")])
        conn.execute("CREATE TABLE observations (seq INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO observations VALUES (?)", [(i,) for i in range(1, 502)])
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "sources.json"))
    result = client.post("/api/build/connect", json={"kind": "sqlite", "name": "测试采集库", "path": str(db)})
    assert result.status_code == 200
    return result.json["conn"]["id"]


@pytest.mark.parametrize("name", ["meter", "月度读数", "shift log", 'meter "A"'],
                         ids=["ascii", "chinese", "space", "quoted-label"])
def test_two_inventories_agree_with_physical_row_and_column_counts(client, source, name):
    headers = {"X-DataMind-Source": source}
    catalog = client.get("/api/tables", headers=headers).json
    connection = client.get("/api/conn/tables", query_string={"src": source}).json["tables"]
    for inventory in (catalog, connection):
        table = next(t for t in inventory if t["name"] == name)
        assert (table["rows"], table["cols"]) == (2, 2), table


@pytest.mark.parametrize("name", ["meter", "月度读数", "shift log"], ids=["ascii", "chinese", "space"])
@pytest.mark.parametrize("surface", ["detail", "info", "preview"])
def test_listed_table_opens_with_its_original_name(client, source, name, surface):
    headers = {"X-DataMind-Source": source}
    assert name in {r["name"] for r in client.get("/api/tables", headers=headers).json}
    path = "/api/table/" + quote(name, safe="")
    if surface == "preview":
        response = client.get("/api/conn/preview", query_string={"table": name, "src": source})
    else:
        response = client.get(path + ("/info" if surface == "info" else ""), headers=headers)
    assert response.status_code == 200 and "error" not in response.json, response.json
    data = response.json
    if surface == "info":
        assert (data["rows"], data["cols"]) == (2, 2)
    else:
        preview = data["preview"] if surface == "detail" else data
        assert preview["columns"] == ["reading", "label"]
        assert sorted((r["reading"], r["label"]) for r in preview["rows"]) == [(11, "上午"), (22, "晚班")]


@pytest.mark.parametrize("size", [500, 501])
@pytest.mark.parametrize("endpoint", ["/api/query", "/api/viz/run"], ids=["sql", "chart"])
def test_bounded_queries_distinguish_complete_result_from_preview(client, source, size, endpoint):
    response = client.post(endpoint, json={"src": source, "sql": f"SELECT seq FROM observations WHERE seq <= {size} ORDER BY seq"})
    assert response.status_code == 200
    data = response.json
    values = [r["seq"] for r in data["rows"]]
    assert values and values == list(range(1, len(values) + 1))
    partial = len(values) < size
    # The service may return all rows, a completion flag, the true total or a visible warning.
    signalled = (data.get("truncated") is True or data.get("has_more") is True
                 or data.get("complete") is False or data.get("total", 0) > len(values)
                 or any(word in str(data.get("warning") or "") for word in ("截断", "部分", "更多")))
    assert bool(signalled) == partial, (size, len(values), data.keys())


@pytest.mark.parametrize("encoding,delimiter", [("utf-8-sig", ","), ("gb18030", ","), ("utf-8", "\t")])
def test_uploaded_table_round_trips_codes_quotes_and_lines(client, tmp_path, monkeypatch, encoding, delimiter):
    import csv
    from io import StringIO

    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "uploads.db"))
    stream = StringIO(newline="")
    out = csv.writer(stream, delimiter=delimiter)
    out.writerow(["批次", "备注", "数量"])
    out.writerows([["0007", '中文,"甲"\n乙', -11], ["0010", "", 0]])
    name = "批次." + ("tsv" if delimiter == "\t" else "csv")
    saved = client.post("/api/build/upload", data={"files": (BytesIO(stream.getvalue().encode(encoding)), name)}).json
    assert saved["saved"] == [name], saved
    table = saved["tables"][0]["table"]
    rows = client.post("/api/query", json={"src": "demo", "sql": f'SELECT * FROM up."{table}" ORDER BY "批次"'}).json["rows"]
    assert rows == [{"批次": "0007", "备注": '中文,"甲"\n乙', "数量": -11}, {"批次": "0010", "备注": "", "数量": 0}]


@pytest.fixture
def ontology(tmp_path, monkeypatch):
    def register(objects):
        path = tmp_path / "fixture.json"
        path.write_text(json.dumps({"objects": objects, "links": []}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setitem(server.IR_SOURCES, "fidelity", {"name": "字段导出测试", "paths": [str(path)]})
        return "fidelity"
    return register


@pytest.mark.parametrize("fmt,parser", [("ttl", "turtle"), ("jsonld", "json-ld"), ("owl", "xml")])
@pytest.mark.parametrize("shape", ["ordinary", "sixty", "wide", "overlapping-names"])
def test_exported_properties_match_each_object_detail(client, ontology, fmt, parser, shape):
    attrs = {"plant": ["code", "label"]}
    if shape in ("sixty", "wide"):
        width = 60 if shape == "sixty" else 61
        attrs = {"plant": [f"reading_{i:02}" for i in range(width)]}
    if shape == "overlapping-names":
        attrs = {"plant": ["line_code"], "plant_line": ["code"]}
    objects = [{"id": obj, "name": obj, "cn": obj, "kind": "object",
                "attrs": [{"col": col, "cn": col, "type": "TEXT"} for col in cols]} for obj, cols in attrs.items()]
    key = ontology(objects)
    for obj, cols in attrs.items():
        detail = client.get(f"/api/ont/object/{key}/{obj}")
        assert detail.status_code == 200 and [a["col"] for a in detail.json["attrs"]] == cols
    response = client.get(f"/api/graph/{key}/export.{fmt}")
    assert response.status_code == 200, response.data[:180]
    graph = Graph().parse(data=response.data, format=parser)
    fields = set(graph.subjects(RDF.type, OWL.DatatypeProperty))
    assert len(fields) == sum(map(len, attrs.values())), (len(fields), shape)
    actual = Counter()
    for field in fields:
        owners = list(graph.objects(field, RDFS.domain))
        assert len(owners) == 1
        owner, = graph.objects(owners[0], RDFS.label)
        label, = graph.objects(field, RDFS.label)
        actual[str(owner), str(label)] += 1
    assert actual == Counter((obj, col) for obj, cols in attrs.items() for col in cols)


@pytest.mark.parametrize("values", [
    ("12.25", "12.50"), ("1234567890123456.01", "1234567890123456.02")
], ids=["ordinary-decimal", "large-decimal"])
def test_external_fixed_point_values_survive_json_round_trip(values):
    rows = [(Decimal(v),) for v in values]
    payload = server.app.json.dumps(server._jsonable_rows(["amount"], rows))
    actual = [Decimal(str(row["amount"])) for row in json.loads(payload, parse_float=Decimal)]
    assert actual == [Decimal(v) for v in values]


@pytest.mark.parametrize("driver_type", [bytes, memoryview], ids=["mysql-bytes", "postgres-buffer"])
def test_external_binary_fields_are_identifiable_and_stable(driver_type):
    def encode():
        rows = [(driver_type(bytes.fromhex(v)),) for v in ("fe01", "ff01")]
        converted = server._jsonable_rows(["fingerprint"], rows)
        return [r["fingerprint"] for r in json.loads(server.app.json.dumps(converted))]

    initial = encode()
    assert initial[0] != initial[1], initial
    assert not any("memory at " in str(v) or "\ufffd" in str(v) for v in initial), initial
    assert initial == encode()


@pytest.mark.parametrize("column", ["base", "doubled"])
def test_schema_describes_every_column_including_generated_preview_values(client, tmp_path, monkeypatch, column):
    db = tmp_path / "generated.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE meter(base INTEGER, doubled INTEGER GENERATED ALWAYS AS (base * 2) STORED)")
        conn.execute("INSERT INTO meter(base) VALUES (21)")
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "sources.json"))
    sid = client.post("/api/build/connect", json={"kind": "sqlite", "name": "计算读数", "path": str(db)}).json["conn"]["id"]
    headers = {"X-DataMind-Source": sid}
    data = client.get("/api/table/meter", headers=headers).json
    assert data["preview"]["rows"] == [{"base": 21, "doubled": 42}]
    names = [c["col"] for c in data["columns"]]
    if column == "base":
        assert column in names
    else:
        info = client.get("/api/table/meter/info", headers=headers).json
        listing = client.get("/api/tables", headers=headers).json
        table = next(t for t in listing if t["name"] == "meter")
        assert (names, info["cols"], table["cols"]) == (["base", "doubled"], 2, 2)


@pytest.mark.parametrize("path", ["/api/query", "/api/viz/run"], ids=["sql", "chart"])
def test_numeric_payload_precedes_browser_formatting(client, path):
    sql = ("SELECT 9007199254740992 AS work_id, 123456.1234561 AS reading, NULL AS note "
           "UNION ALL SELECT 9007199254740993, 123456.1234562, ''")
    response = client.post(path, json={"src": "demo", "sql": sql})
    assert response.status_code == 200
    rows = json.loads(response.data, parse_float=Decimal)["rows"]
    assert [(str(r["work_id"]), Decimal(str(r["reading"])), r["note"]) for r in rows] == [
        ("9007199254740992", Decimal("123456.1234561"), None),
        ("9007199254740993", Decimal("123456.1234562"), ""),
    ]

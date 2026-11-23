"""Follow a human-entered or selected name to the physical entity that actually changes."""
import csv
from io import BytesIO, StringIO
import json
import sqlite3

import pytest
from rdflib import Graph, OWL, RDF, RDFS
import server

from tests.discovery_support import named_graph
from tests.unit.test_deepqa_application_http import ROUTES, ask, qa_http

__all__ = ["named_graph", "qa_http"]


# 已确认、尚未修复的产品缺陷:按用例标为严格预期失败,修复后会以 XPASS 报错,提醒删去对应条目。
OPEN_DEFECTS = {  # pytest 节点名:非 ASCII 参数按 pytest 默认写成 \uXXXX 转义
    r"test_single_business_table_can_register[sqliteLedger]",
    r"test_business_table_stays_discoverable_beside_control[catalog]",
    r"test_business_table_stays_discoverable_beside_control[connections]",
    r"test_business_table_stays_discoverable_beside_control[build-prep]",
    r"test_delimited_upload_preserves_column_boundaries[Location, City-;]",
    r"test_delimited_upload_preserves_column_boundaries[Location, City-|]",
    r"test_alias_edit_never_guesses_between_shared_names[east-first-obj:\u5171\u7528\u5de5\u5355]",
    r"test_alias_edit_never_guesses_between_shared_names[west-first-obj:\u5171\u7528\u5de5\u5355]",
    r"test_aliases_entered_as_business_text_remain_separately_searchable[\uff0c]",
    r"test_aliases_entered_as_business_text_remain_separately_searchable[\uff1b]",
    r"test_exact_business_label_finds_its_profile[\u6cf5]",
    r"test_object_detail_resolves_selected_node_id[True]",
    r"test_metric_id_selects_the_same_contract_and_persisted_status[True-contract]",
    r"test_metric_id_selects_the_same_contract_and_persisted_status[True-status]",
    r"test_offline_count_follows_explicit_business_table[\u7ef4\u4fee\u5de5\u5355-/api/chat]",
    r"test_offline_count_follows_explicit_business_table[\u7ef4\u4fee\u5de5\u5355-/api/chat/stream]",
    r"test_rdf_terms_keep_identity_after_reordering[similar-classes-ttl-turtle]",
    r"test_rdf_terms_keep_identity_after_reordering[similar-classes-jsonld-json-ld]",
    r"test_rdf_terms_keep_identity_after_reordering[similar-classes-owl-xml]",
    r"test_rdf_terms_keep_identity_after_reordering[relations-ttl-turtle]",
    r"test_rdf_terms_keep_identity_after_reordering[relations-jsonld-json-ld]",
    r"test_rdf_terms_keep_identity_after_reordering[relations-owl-xml]",
}


@pytest.fixture(autouse=True)
def _open_defect(request):
    if request.node.name in OPEN_DEFECTS:
        request.applymarker(pytest.mark.xfail(strict=True, reason="已确认的业务名称检索缺陷,待修复"))


@pytest.fixture
def client():
    return server.app.test_client()


@pytest.mark.parametrize("table", ["orderLedger", "sqliteLedger"])
def test_single_business_table_can_register(client, tmp_path, monkeypatch, table):
    path = tmp_path / "ledger.db"
    with sqlite3.connect(path) as db:
        db.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY AUTOINCREMENT)')
        db.execute(f'INSERT INTO "{table}" DEFAULT VALUES')
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    response = client.post("/api/build/connect", json={"kind": "sqlite", "name": "账本", "path": str(path)})
    assert response.status_code == 200, response.json
    src = response.json["conn"]["id"]
    catalog = client.get("/api/conn/tables", query_string={"src": src}).json
    assert [row["name"] for row in catalog["tables"]] == [table]


@pytest.mark.parametrize("surface", ["catalog", "connections", "build-prep", "sql", "chart"])
def test_business_table_stays_discoverable_beside_control(client, tmp_path, monkeypatch, surface):
    path = tmp_path / "pair.db"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE ordinary(v INTEGER PRIMARY KEY AUTOINCREMENT);"
                         "CREATE TABLE sqliteLedger(v INTEGER); INSERT INTO sqliteLedger VALUES(73);")
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    src = client.post("/api/build/connect", json={"kind": "sqlite", "name": "双表", "path": str(path)}).json["conn"]["id"]
    if surface == "build-prep":
        summary = client.post("/api/build/prep", json={"source": src}).json["summary"]
        assert (summary["tables"], summary["rows"]) == (2, 1)
    elif surface in ("sql", "chart"):
        path = "/api/query" if surface == "sql" else "/api/viz/run"
        assert client.post(path, json={"src": src, "sql": "SELECT v FROM sqliteLedger"}).json["rows"] == [{"v": 73}]
    else:
        if surface == "catalog":
            tables = client.get("/api/tables", headers={"X-DataMind-Source": src}).json
        else:
            tables = client.get("/api/conn/tables", query_string={"src": src}).json["tables"]
        assert {row["name"] for row in tables} == {"ordinary", "sqliteLedger"}


@pytest.mark.parametrize("delimiter", [",", ";", "|"])
@pytest.mark.parametrize("label", ["Location", "Location, City"])
def test_delimited_upload_preserves_column_boundaries(client, tmp_path, monkeypatch, delimiter, label):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "UPLOAD_DB", str(tmp_path / "uploads.db"))
    stream = StringIO()
    csv.writer(stream, delimiter=delimiter, quoting=csv.QUOTE_ALL).writerows(
        [[label, "units"], ["Paris, FR", 11], ["Tokyo, JP", 22]])
    result = client.post("/api/build/upload", data={"files": (BytesIO(stream.getvalue().encode()), "cities.csv")}).json
    assert result["saved"] == ["cities.csv"], result["tables"]
    data = client.post("/api/query", json={"src": "uploads", "sql": "SELECT * FROM cities ORDER BY rowid"}).json
    assert "error" not in data, data
    assert len(data["columns"]) == 2 and data["columns"][1] == "units", data["columns"]
    assert [[row[col] for col in data["columns"]] for row in data["rows"]] == [["Paris, FR", 11], ["Tokyo, JP", 22]]


def profiles(client, key):
    result = client.get("/api/ont/profile", query_string={"graph": key})
    assert result.status_code == 200
    return {profile["key"]: profile["aliases"] for profile in result.json["profiles"]}


@pytest.mark.parametrize("target", ["obj:east", "obj:共用工单"])
@pytest.mark.parametrize("order", ["east-first", "west-first"])
def test_alias_edit_never_guesses_between_shared_names(client, named_graph, target, order):
    objects = [{"id": direction, "name": direction, "cn": label, "aliases": ["共用工单"]}
               for direction, label in [("east", "东厂工单"), ("west", "西厂工单")]]
    key = named_graph(objects if order == "east-first" else objects[::-1])
    before = profiles(client, key)
    r = client.post("/api/ont/apply", json={"graph": key, "op": {
        "op": "set_alias", "target": target, "params": {"aliases": ["返修工单"]}}})
    after = profiles(client, key)
    if target == "obj:east":
        assert r.status_code == 200 and after == {**before, "east": ["返修工单"]}
    else:
        assert after == before, (r.status_code, after)
        assert r.json.get("error") or r.json.get("candidates"), r.json


@pytest.mark.parametrize("separator", [",", "、", "，", "；"])
def test_aliases_entered_as_business_text_remain_separately_searchable(client, named_graph, separator):
    key = named_graph([{"id": "orders", "name": "orders", "cn": "订单"}])
    r = client.post("/api/ont/apply", json={"graph": key, "op": {
        "op": "set_alias", "target": "obj:orders", "params": {"aliases": separator.join(["工票", "派工单"])}}})
    assert r.status_code == 200, r.json
    assert profiles(client, key)["orders"] == ["工票", "派工单"]
    for alias in ("工票", "派工单"):
        hits = client.get("/api/ont/profile", query_string={"graph": key, "q": alias}).json["hits"]
        assert [hit["key"] for hit in hits] == ["orders"]


@pytest.mark.parametrize("label", ["水泵", "泵"])
def test_exact_business_label_finds_its_profile(client, named_graph, label):
    key = named_graph([{"id": "P01", "name": "P01", "cn": label},
                       {"id": "U02", "name": "U02", "cn": "油箱"}])
    result = client.get("/api/ont/profile", query_string={"graph": key, "q": label})
    assert result.status_code == 200
    assert [hit["key"] for hit in result.json["hits"]] == ["P01"]


@pytest.mark.parametrize("collision", [False, True])
def test_object_detail_resolves_selected_node_id(client, named_graph, collision):
    key = named_graph([{"id": "first", "name": "P02" if collision else "EastPump", "cn": "东泵"},
                       {"id": "P02", "name": "WestPump", "cn": "西泵"}])
    graph = client.get(f"/api/graph/{key}").json
    selected = next(n for n in graph["nodes"] if n["id"] == "P02")
    detail = client.get(f"/api/ont/object/{key}/{selected['id']}").json
    assert (detail["id"], detail["cn"]) == ("P02", "西泵")


@pytest.mark.parametrize("surface", ["contract", "status"])
@pytest.mark.parametrize("collision", [False, True])
def test_metric_id_selects_the_same_contract_and_persisted_status(client, named_graph, surface, collision):
    metrics = [{"id": "first", "name": "M02" if collision else "东区产量", "status": "candidate"},
               {"id": "M02", "name": "西区产量", "status": "candidate"}]
    key = named_graph(metrics=metrics)
    if surface == "contract":
        response = client.get("/api/metric/contract", query_string={"graph": key, "name": "M02"})
        if collision and response.status_code in (400, 409, 422):
            assert response.json.get("error")
        else:
            assert response.status_code == 200 and response.json["metric"]["id"] == "M02", response.json
    else:
        result = client.post("/api/metric/status", json={"graph": key, "metric": "M02", "status": "deprecated"})
        expected = {"first": "candidate", "M02": "deprecated"}
        if collision and result.status_code in (400, 409, 422):
            assert result.json.get("error")
            expected["M02"] = "candidate"
        else:
            assert result.status_code == 200, result.json
        listed = client.get("/api/metrics", query_string={"graph": key}).json
        assert {m["id"]: m["status"] for m in listed["atomic"]} == expected


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("table", ["repair_orders", "维修工单"])
def test_offline_count_follows_explicit_business_table(qa_http, route, table):
    client, work, db, ir, _ = qa_http
    ir["objects"] = [{"id": "repairs", "name": "repairs", "cn": "维修工单", "table": table}]
    (work / "built_scope.json").write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
    with sqlite3.connect(db) as conn:
        conn.execute(f'CREATE TABLE "{table}" (ticket INTEGER)')
        conn.executemany(f'INSERT INTO "{table}" VALUES (?)', [(101,), (105,), (106,), (109,)])
    sql = client.post("/api/query", json={"src": "demo", "sql": f'SELECT COUNT(*) AS n FROM "{table}"'})
    assert sql.json["rows"] == [{"n": 4}]
    answer, _, _ = ask(qa_http, route, q=f"{table}这个表有多少条记录？", nocache=True)
    assert [r["data"]["rows"] for r in answer.get("results", [])] == [[{"记录数": 4}]]
    assert answer["anchor"]["used"] == [table]


@pytest.mark.parametrize("fmt,parser", [("ttl", "turtle"), ("jsonld", "json-ld"), ("owl", "xml")])
@pytest.mark.parametrize("reorder", ["distinct-classes", "similar-classes", "relations"])
def test_rdf_terms_keep_identity_after_reordering(client, named_graph, fmt, parser, reorder):
    east = "pump-east"
    west = "pump east" if reorder == "similar-classes" else "pump_west"
    objects = [{"id": east, "cn": "东泵"}, {"id": west, "cn": "西泵"}, {"id": "plant", "cn": "总厂"}]
    links = [{"source": east, "target": "plant", "verb": "供水", "status": "asserted"},
             {"source": west, "target": "plant", "verb": "排水", "status": "asserted"}]
    def export(order, edges):
        key = named_graph(order, links=edges)
        response = client.get(f"/api/graph/{key}/export.{fmt}")
        assert response.status_code == 200
        graph = Graph().parse(data=response.data, format=parser)
        kind = OWL.ObjectProperty if reorder == "relations" else OWL.Class
        labels = {"供水", "排水"} if reorder == "relations" else {"东泵", "西泵", "总厂"}
        terms = {str(label): str(term) for term in graph.subjects(RDF.type, kind)
                 for label in graph.objects(term, RDFS.label) if str(label) in labels}
        assert set(terms) == labels
        return terms
    before = export(objects, links)
    after = export(objects, links[::-1]) if reorder == "relations" else export(objects[::-1], links)
    assert before == after


def test_same_name_boards_reopen_with_independent_cards(client, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_VIZ_BOARDS_F", str(tmp_path / "boards.json"))
    def chart(n):
        return {"title": str(n), "sql": f"SELECT {n} AS n", "type": "kpi", "src": "demo", "x": "n", "ys": ["n"]}
    ids = []
    for n in (11, 22):
        resp = client.post("/api/viz/save", json={"name": "班报", "charts": [chart(n)]})
        assert resp.status_code == 200
        ids.append(resp.json["id"])
    assert ids[0] != ids[1]
    resp = client.post("/api/viz/save", json={"id": ids[1], "name": "班报", "charts": [chart(33)]})
    assert resp.status_code == 200
    stored = {b["id"]: b["charts"] for b in client.get("/api/viz/boards").json}
    assert stored == {ids[0]: [chart(11)], ids[1]: [chart(33)]}

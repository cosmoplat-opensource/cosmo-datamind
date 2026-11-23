"""Compare the reported scope with the source records and the persisted business state."""
from io import BytesIO
import json
import sqlite3
import time

import pytest
from rdflib import Graph, OWL, RDF

import server
from tests.discovery_support import named_graph
from tests.result_scope_support import action_ledger, review_graph

__all__ = ["action_ledger", "named_graph", "review_graph"]


@pytest.fixture
def client():
    return server.app.test_client()


@pytest.fixture
def api_sources(client, tmp_path, monkeypatch):
    database = tmp_path / "upload.db"
    monkeypatch.setattr(server, "UPLOAD_DB", str(database))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    feeds = {}

    def open_fixture(url, timeout):
        return BytesIO(json.dumps(feeds[url]).encode())

    monkeypatch.setattr(server, "_open_api_url", open_fixture)

    def register(name, rows):
        url = f"https://192.0.2.15/feed/{len(feeds)}"
        feeds[url] = rows
        response = client.post("/api/build/connect", json={"kind": "api", "name": name, "url": url})
        assert response.status_code == 200, response.json
        return response.json["conn"]["id"]

    return register, database


def fetch(client, cid):
    response = client.post("/api/conn/api_fetch", json={"id": cid})
    return response.status_code, response.json


@pytest.mark.parametrize("dimension,size", [("rows", 5000), ("rows", 5001), ("columns", 40), ("columns", 41)])
def test_api_import_reports_the_actual_scope(client, api_sources, dimension, size):
    register, database = api_sources
    rows = ([{"v": i} for i in range(size)] if dimension == "rows"
            else [{f"field_{i}": i for i in range(size)}])
    cid = register("daily", rows)
    status, data = fetch(client, cid)
    if status in (400, 413, 422) and size in (5001, 41):
        assert data.get("error")
        return
    assert status == 200, data
    table = data["table"].removeprefix("up.")
    with sqlite3.connect(database) as db:
        persisted = db.execute(f'SELECT * FROM "{table}"').fetchall()
        fields = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
    assert data["rows"] == len(persisted) and data["columns"] == fields
    observed = len(persisted) if dimension == "rows" else len(fields)
    if observed == size:
        assert {tuple(int(v) for v in row) for row in persisted} == {tuple(row.values()) for row in rows}
    else:
        assert data.get("truncated") or data.get(f"total_{dimension}") == size, {
            "input": size, "persisted": observed, "reported": sorted(data)}


@pytest.mark.parametrize("names", [("east_daily", "west_daily"), ("入库日报", "出库日报"), ("line-a", "line a")])
def test_fetching_one_source_preserves_the_other_source(client, api_sources, names):
    register, database = api_sources
    a, b = [register(n, [{"v": v}]) for n, v in zip(names, [11, 22])]
    assert a != b
    status_a, first = fetch(client, a)
    assert status_a == 200, first
    sources = {s["id"]: s for s in client.get("/api/build/sources").json["sources"]}
    status_b, second = fetch(client, b)
    assert status_b == 200, second
    with sqlite3.connect(database) as db:
        values = []
        for data in [first, second]:
            table = data["table"].removeprefix("up.")
            values.append(int(db.execute(f'SELECT v FROM "{table}"').fetchone()[0]))
    assert (sources[b]["ready"], values) == (False, [11, 22]), {
        "ready_before_fetch": sources[b]["ready"], "tables": [first["table"], second["table"]], "values": values}


@pytest.mark.parametrize("size", [500, 501])
@pytest.mark.parametrize("kind", ["SELECT", "CONSTRUCT"])
def test_sparql_reports_full_cardinality_or_discloses_a_partial_result(client, named_graph, size, kind):
    key = named_graph(objects=[{"id": f"unit_{i}", "name": f"unit_{i}"} for i in range(size)])
    export = client.get(f"/api/graph/{key}/export.ttl")
    assert export.status_code == 200
    graph = Graph().parse(data=export.data, format="turtle")
    objects = {s for s in graph.subjects(RDF.type, OWL.Class) if "unit_" in str(s)}
    assert len(objects) == size
    pattern = 'WHERE { ?s a <http://www.w3.org/2002/07/owl#Class> . FILTER(CONTAINS(STR(?s), "unit_")) }'
    query = ("SELECT DISTINCT ?s " if kind == "SELECT" else "CONSTRUCT { ?s <urn:scope:has> ?s } ") + pattern
    response = client.post("/api/sparql", json={"graph": key, "query": query})
    assert response.status_code == 200, response.json
    data = response.json
    actual = {row["s"] for row in data["rows"]}
    assert actual <= {str(s) for s in objects}
    assert len(actual) == size or data["total"] == size or data.get("has_more") or data.get("truncated"), {
        "expected": size, "returned": len(actual), "total": data["total"]}


@pytest.mark.parametrize("completed", [998, 999])
def test_new_action_preserves_unresolved_approval(client, action_ledger, completed):
    path = action_ledger(completed)
    assert client.get("/api/actions").json["pending"] == 1
    response = client.post("/api/action/invoke", json={"action_id": "act_scope", "operator": "班长", "params": {}})
    assert response.status_code in (200, 409), response.json
    pending = [row["id"] for row in json.loads(path.read_text()) if row["status"] == "pending"]
    assert (pending, client.get("/api/actions").json["pending"]) == (["evt_0"], 1)


@pytest.mark.parametrize("invalid_last", [False, True])
def test_batch_review_preserves_selected_scope_and_atomicity(client, review_graph, invalid_last):
    key = review_graph("built_scope_bulk", "班组", 51)
    ops = [{"target": f"rel:unit_{i:03d}->plant", "op": "confirm_relation"} for i in range(1, 51)]
    if invalid_last:
        ops[-1]["target"] = "rel:retired_unit->plant"
    response = client.post("/api/ont/apply", json={"graph": key, "source": "review", "reviewer": "验收员", "ops": ops})
    assert response.status_code == (400 if invalid_last else 200), response.json
    rows = client.get("/api/ont/review", query_string={"graph": key}).json["rows"]
    assert len(rows) == 51
    assert {r["s"] for r in rows if r["human_review"] == "approved"} == (
        set() if invalid_last else {f"unit_{i:03d}" for i in range(1, 51)})


@pytest.mark.parametrize("later_qa_events", [4999, 5000])
def test_recent_build_filter_finds_latest_build_in_todays_log(client, tmp_path, monkeypatch, later_qa_events):
    monkeypatch.setenv("DATAMIND_OBS_DIR", str(tmp_path))
    directory = tmp_path / "obs"
    directory.mkdir()
    target = {"kind": "build", "run_id": "scope-build", "event": "finish"}
    qa = json.dumps({"kind": "qa", "event": "finish"}) + "\n"
    path = directory / time.strftime("events-%Y%m%d.jsonl")
    path.write_text(json.dumps(target) + "\n" + qa * later_qa_events, encoding="utf-8")
    query = {"kind": "build", "limit": 1}
    response = client.get("/api/obs/runs", query_string=query)
    assert response.status_code == 200
    assert response.json == [target]

"""T02 RED: explicit effective-IR registration without legacy migration — DR-059 / DR-065."""
from copy import deepcopy
import hashlib
import importlib
import json

import pytest

from tests.asset_support import data_ir, legacy_api, store_at


def callbacks(snapshot, provenance):
    return {"enumerate_graphs": lambda: [{"key": "built_parts", "name": "装配数据"}],
            "load_effective": lambda key: deepcopy(snapshot),
            "provenance": lambda key: deepcopy(provenance)}


def test_effective_edits_are_registered_without_mutating_callback_inputs(tmp_path):
    store = store_at(tmp_path / "assets")
    register = legacy_api()
    snapshot = data_ir()
    snapshot["objects"][0]["aliases"] = ["装配件", "工件"]
    provenance = {"source_sha256": "a" * 64, "edits_sha256": "b" * 64}
    before = deepcopy(snapshot)
    rows = register(store, **callbacks(snapshot, provenance))
    assert len(rows) == 1
    row = rows[0]
    assert row["legacy_key"] == "built_parts"
    assert store.load_data_ir(row["ontology_id"], row["version_id"]) == before
    saved = store.get_version(row["ontology_id"], row["version_id"])
    assert saved["metadata"]["legacy_key"] == "built_parts"
    assert saved["metadata"]["provenance"] == provenance
    assert snapshot == before
    snapshot["objects"][0]["aliases"].append("later")
    assert store.load_data_ir(row["ontology_id"], row["version_id"]) == before


def test_repeated_registration_is_idempotent_and_changes_create_child_version(tmp_path):
    store = store_at(tmp_path / "assets")
    register = legacy_api()
    snapshot = data_ir()
    provenance = {"source_sha256": "a" * 64, "edits_sha256": "b" * 64}
    kwargs = callbacks(snapshot, provenance)
    first = register(store, **kwargs)[0]
    repeated = register(store_at(tmp_path / "assets"), **kwargs)[0]
    aid, vid = first["ontology_id"], first["version_id"]
    assert repeated["ontology_id"] == aid and repeated["version_id"] == vid
    assert len(store.list_assets()) == len(store.list_versions(aid)) == 1
    snapshot["objects"][0]["aliases"] = ["新别名"]
    provenance["edits_sha256"] = "c" * 64
    # Source/callback changes alone must not silently alter the old registered version.
    assert store.load_data_ir(aid, vid) == data_ir()
    second = register(store, **kwargs)[0]
    assert second["ontology_id"] == aid and second["version_id"] != vid
    assert store.get_version(aid, second["version_id"])["parent_version_id"] == vid
    assert store.load_data_ir(aid, second["version_id"]) == snapshot
    assert store.load_data_ir(aid, vid) == data_ir()
    assert len(store.list_versions(aid)) == 2


def test_provenance_change_is_not_hidden_by_identical_effective_payload(tmp_path):
    store = store_at(tmp_path / "assets")
    register = legacy_api()
    snapshot = data_ir()
    provenance = {"source_sha256": "a" * 64, "edits_sha256": "b" * 64}
    kwargs = callbacks(snapshot, provenance)
    first = register(store, **kwargs)[0]
    provenance["source_sha256"] = "c" * 64
    second = register(store, **kwargs)[0]
    assert first["ontology_id"] == second["ontology_id"]
    assert first["version_id"] != second["version_id"]
    saved = store.get_version(second["ontology_id"], second["version_id"])
    assert saved["metadata"]["provenance"]["source_sha256"] == "c" * 64
    assert store.get_version(first["ontology_id"], first["version_id"])["metadata"]["provenance"][
        "source_sha256"] == "a" * 64


@pytest.mark.parametrize("invalid", [None, [], {"objects": "broken"}])
def test_invalid_legacy_snapshot_is_not_replaced_with_demo_data(tmp_path, invalid):
    store = store_at(tmp_path / "assets")
    register = legacy_api()
    with pytest.raises(ValueError):
        register(store, **callbacks(invalid, {}))
    assert store.list_assets() == []


def test_empty_legacy_inventory_does_not_create_default_asset(tmp_path):
    store = store_at(tmp_path / "assets")
    register = legacy_api()

    def never_called(key):
        raise AssertionError("Empty inventory must not load a default graph")

    assert register(store, enumerate_graphs=lambda: [], load_effective=never_called,
                    provenance=never_called) == []
    assert store.list_assets() == []


def test_actual_server_effective_ir_is_snapshotted_and_legacy_files_unchanged(tmp_path, monkeypatch):
    store = store_at(tmp_path / "assets")
    register = legacy_api()
    server = importlib.import_module("server")
    work = tmp_path / "legacy"
    work.mkdir()
    source = work / "built_parts.json"
    edits = work / "edits_built_parts.json"
    source.write_text(json.dumps(data_ir(), ensure_ascii=False))
    edits.write_text(json.dumps({"version": 1, "ops": [
        {"op": "set_alias", "target": "part", "params": {"aliases": "装配件,工件"}}]}, ensure_ascii=False))
    chats = work / "ont_chats.json"
    chats.write_text('{"existing":{"messages":[{"role":"user","content":"保留消息"}]}}')
    monkeypatch.setattr(server, "WORK", str(work))
    monkeypatch.setattr(server, "CHATS_F", str(chats))
    before_files = {p: p.read_bytes() for p in work.iterdir()}
    before_jobs = deepcopy(server.JOBS)
    effective = server.load_ir_edited("built_parts")
    assert effective["objects"][0]["aliases"] == ["装配件", "工件"]
    provenance = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "edits_sha256": hashlib.sha256(edits.read_bytes()).hexdigest()}
    rows = register(store, enumerate_graphs=lambda: [{"key": "built_parts", "name": "装配数据"}],
                    load_effective=server.load_ir_edited, provenance=lambda key: provenance)
    row = rows[0]
    assert store.load_data_ir(row["ontology_id"], row["version_id"]) == effective
    assert {p: p.read_bytes() for p in work.iterdir()} == before_files
    assert server.JOBS == before_jobs
    assert server.load_ir_edited("built_parts") == effective
    with server.app.test_client() as client:
        response = client.get("/api/graph/built_parts")
        assert response.status_code == 200
        assert response.get_json() == server.ir_to_graph("built_parts", effective)
        response = client.get("/api/ont/chats")
        assert response.status_code == 200
        assert any(row["id"] == "existing" and row["n"] == 1 for row in response.get_json())

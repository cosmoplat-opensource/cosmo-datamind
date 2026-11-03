"""T04: schema and fixed-asset integrity at the run boundary — DR-059 / DR-064 / DR-065."""
from copy import deepcopy
import sqlite3

import pytest

from tests.asset_support import add_data, data_bytes, store_at
from tests.run_support import outputs, result, runs_at, setup_run


@pytest.mark.parametrize("factory", ["assets", "runs"])
def test_unknown_database_schema_is_not_reinitialized(tmp_path, factory):
    root = tmp_path / "storage"
    assets = store_at(root)
    asset, version = add_data(assets)
    db = root / "domain.sqlite3"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA user_version=999")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    constructor = store_at if factory == "assets" else runs_at
    with pytest.raises(ValueError):
        constructor(root)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    with sqlite3.connect(db) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
        assert connection.execute("SELECT COUNT(*) FROM assets WHERE ontology_id=?", (asset["ontology_id"],)).fetchone()[0] == 1
    assert (root / version["path"]).read_bytes() == data_bytes()


@pytest.mark.parametrize("damage", ["changed", "missing", "symlink_escape"])
def test_new_run_does_not_pin_a_damaged_ontology_file(tmp_path, damage):
    root, assets, runs, _, args = setup_run(tmp_path)
    version = assets.get_version(args["ontology_id"], args["version_id"])
    path = root / version["path"]
    if damage == "changed":
        path.write_bytes(data_bytes("tampered"))
    elif damage == "missing":
        path.unlink()
    else:
        outside = tmp_path / "outside.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    before = deepcopy(runs.list_runs())
    with pytest.raises((ValueError, FileNotFoundError)):
        runs.create_run(**args)
    assert runs.list_runs() == before


def test_snapshot_damage_after_start_prevents_successful_completion(tmp_path):
    root, assets, runs, run, args = setup_run(tmp_path)
    rid = run["run_id"]
    runs.start(rid)
    path = root / assets.get_version(args["ontology_id"], args["version_id"])["path"]
    path.write_bytes(data_bytes("tampered after start"))
    before, events = deepcopy(runs.get_run(rid)), deepcopy(runs.events(rid))
    with pytest.raises(ValueError):
        runs.finish(rid, result=result(), artifacts=outputs())
    assert runs.get_run(rid) == before and runs.events(rid) == events
    assert runs.artifacts(rid) == []


def test_run_schema_initialization_preserves_existing_asset_versions(tmp_path):
    root = tmp_path / "storage"
    assets = store_at(root)
    asset, version = add_data(assets)
    before = deepcopy(assets.get_version(asset["ontology_id"], version["version_id"]))
    runs_at(root)
    reopened = store_at(root)
    assert reopened.get_version(asset["ontology_id"], version["version_id"]) == before
    assert reopened.read_payload(asset["ontology_id"], version["version_id"]) == data_bytes()
    assert len(list(root.rglob("*.sqlite3"))) == 1, "Run storage must share the asset metadata database"

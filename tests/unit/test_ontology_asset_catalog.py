"""T02 RED: typed catalog, source integrity and data access isolation — DR-059 / DR-065."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.asset_support import add_data, add_robotics, data_ir, store_at


def test_catalog_lists_both_domains_and_rejects_robotics_as_data(tmp_path):
    store = store_at(tmp_path / "assets")
    data, dv = add_data(store)
    robot, rv, _, _ = add_robotics(store, tmp_path)
    rows = {r["ontology_id"]: r for r in store.list_assets()}
    assert set(rows) == {data["ontology_id"], robot["ontology_id"]}
    assert rows[data["ontology_id"]]["domain"] == "data"
    assert rows[robot["ontology_id"]]["domain"] == "robotics"
    assert store.get_version(data["ontology_id"], dv["version_id"])["payload_kind"] == "datamind_ir"
    assert store.get_version(robot["ontology_id"], rv["version_id"])["payload_kind"] == "robotics_rdf"
    assert store.load_data_ir(data["ontology_id"], dv["version_id"]) == data_ir()
    with pytest.raises(ValueError):
        store.load_data_ir(robot["ontology_id"], rv["version_id"])
    with pytest.raises(KeyError):
        store.load_data_ir("missing", "missing")


def test_empty_catalog_and_unknown_ids_do_not_fall_back(tmp_path):
    store = store_at(tmp_path / "assets")
    assert store.list_assets() == []
    with pytest.raises(KeyError):
        store.get_asset("unknown")
    with pytest.raises(KeyError):
        store.get_version("unknown", "unknown")


def test_sources_preserve_bytes_roles_and_hashes_after_reopen(tmp_path):
    root = tmp_path / "assets"
    store = store_at(root)
    content = b'<?xml version="1.0"?>\r\n<source name="A"/>\r\n'
    first = store.put_source(content, role="behavior_tree", media_type="application/xml")
    second = store.put_source(content, role="node_models", media_type="application/xml")
    store = store_at(root)
    for record, role in ((first, "behavior_tree"), (second, "node_models")):
        saved = store.get_source(record["source_asset_id"])
        assert saved["sha256"] == hashlib.sha256(content).hexdigest()
        assert saved["role"] == role and saved["media_type"] == "application/xml"
        assert store.read_source(record["source_asset_id"]) == content


@pytest.mark.parametrize("domain", ["", "unknown", None])
def test_unknown_domain_cannot_create_catalog_entry(tmp_path, domain):
    store = store_at(tmp_path / "assets")
    with pytest.raises(ValueError):
        store.create_asset(name="invalid", domain=domain)
    assert store.list_assets() == []


def test_catalog_can_be_read_in_a_new_process_without_server_import(tmp_path):
    root = tmp_path / "assets"
    store = store_at(root)
    asset, version = add_data(store)
    repo = Path(__file__).resolve().parents[2]
    code = """
import json, sys
from ontology_assets import AssetStore
store = AssetStore(sys.argv[1])
assert 'server' not in sys.modules
print(json.dumps({'assets': store.list_assets(),
                 'ir': store.load_data_ir(sys.argv[2], sys.argv[3])}))
"""
    run = subprocess.run([sys.executable, "-c", code, str(root), asset["ontology_id"], version["version_id"]],
                         cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(repo)),
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert [r["ontology_id"] for r in result["assets"]] == [asset["ontology_id"]]
    assert result["ir"] == data_ir()

"""Fresh robotics assets and independent persistence assertions — DR-064 / DR-065."""
from functools import lru_cache
import hashlib
import importlib
import json
from pathlib import Path
import tempfile

from ontology_assets import AssetStore
from domain_runs import RunStore
from tests.asset_support import add_data
from tests.robotics_support import FIXTURES, SOURCE, MODELS, api, build

ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT = FIXTURES / "demo_supplement.json"
SESSION = {"namespace": "ont_chats", "id": "storage-integration"}


def pipeline():
    # Missing production entry is a normal RED failure, not a collection error.
    return importlib.import_module("domain_runs.robotics").generate_from_version


def sha(content):
    return hashlib.sha256(content).hexdigest()


@lru_cache(maxsize=2)
def fresh_payload(mode):
    # Built from the two frozen XML inputs, never a prebuilt agent OWL fixture.
    graph = build()
    if mode == "demo":
        graph = api("demo").apply_demo_supplement(graph, SUPPLEMENT)["graph"]
    with tempfile.TemporaryDirectory(prefix="storage-test-publish-") as raw:
        target = Path(raw) / "fresh.owl"
        api("publisher").publish_ontology(graph, target, mode=mode)
        return target.read_bytes()


def setup_assets(tmp_path, mode="demo"):
    root = tmp_path / "storage"
    assets = AssetStore(root)
    add_data(assets)
    inputs = [(SOURCE, "behavior_tree", "application/xml"), (MODELS, "node_models", "application/xml")]
    if mode == "demo":
        inputs.append((SUPPLEMENT, "demo_supplement", "application/json"))
    sources = [assets.put_source(p.read_bytes(), role=role, media_type=media) for p, role, media in inputs]
    asset = assets.create_asset(name="fresh robot", domain="robotics")
    version = assets.add_version(asset["ontology_id"], fresh_payload(mode), payload_kind="robotics_rdf",
                                 source_asset_ids=[s["source_asset_id"] for s in sources],
                                 metadata={"profile_id": "ur_pick_place", "build_recipe": "bt_xml"})
    version = assets.publish_version(asset["ontology_id"], version["version_id"], mode=mode)
    return root, assets, asset, version, sources


def generate(call, root, asset, version, task, mode="demo"):
    return call(root, ontology_id=asset["ontology_id"], version_id=version["version_id"],
                task=task, mode=mode, session_ref=SESSION)


def check_saved(root, run, expected):
    runs = RunStore(root)
    saved = runs.get_run(run["run_id"])
    assert saved == run
    assert saved["status"] == ("failed" if expected == "error" else "completed")
    assert saved["result"]["status"] == expected
    assert saved["result"]["execution_readiness"] == "not_verified"
    assert "candidate_path" not in saved["result"], "Temporary generator paths cannot be durable download references"
    manifest = runs.manifest(run["run_id"])
    assert manifest["result"] == saved["result"] and manifest["task"] == saved["task"]
    artifacts = runs.artifacts(run["run_id"])
    assert len([a for a in artifacts if a["kind"] == "manifest"]) == 1
    reports = [a for a in artifacts if a["kind"] == "validation"]
    assert len(reports) == 1
    assert json.loads(runs.read_artifact(run["run_id"], reports[0]["artifact_id"])) == saved["result"]
    candidates = [a for a in artifacts if a["kind"] == "candidate"]
    assert len(candidates) == (1 if expected == "generated" else 0)
    for artifact in artifacts:
        raw = runs.read_artifact(run["run_id"], artifact["artifact_id"])
        assert sha(raw) == artifact["sha256"] and len(raw) == artifact["byte_size"]
    events = runs.events(run["run_id"])
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert [e["payload"]["status"] for e in events if e["kind"] == "state"] == ["queued", "running", saved["status"]]
    return runs, candidates

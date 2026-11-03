"""T06 RED: real robotics generation through durable version/run services — DR-059 / DR-064 / DR-065."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from rdflib import Graph, Literal, XSD

from domain_runs import RunStore
from tests.robotics_generation_support import task
from tests.robotics_support import S, api, SOURCE, MODELS, trees
from tests.storage_pipeline_support import (ROOT, SESSION, SUPPLEMENT, check_saved, generate,
                                            pipeline, setup_assets, sha)


def test_fresh_published_version_generates_and_persists_exact_kernel_outputs(tmp_path, monkeypatch):
    call = pipeline()
    root, assets, asset, version, sources = setup_assets(tmp_path)
    observed = []
    original = api("generation").generate_task

    def observe(context, incoming, *, mode, output_dir):
        result = original(context, incoming, mode=mode, output_dir=output_dir)
        observed.append((context["validation"], incoming, mode, result,
                         Path(result["candidate_path"]).read_bytes()))
        return result

    monkeypatch.setattr(api("generation"), "generate_task", observe)
    run = generate(call, root, asset, version, task())
    runs, candidates = check_saved(root, run, "generated")
    assert len(observed) == 1
    validation, incoming, mode, kernel, content = observed[0]
    assert incoming == task() and mode == "demo"
    assert validation["ontology_sha256"] == version["payload_sha256"]
    assert run["result"] == {k: v for k, v in kernel.items() if k != "candidate_path"}
    assert runs.read_artifact(run["run_id"], candidates[0]["artifact_id"]) == content
    assert b"DEMO ONLY" in content and b"__UNRESOLVED_" not in content
    # Independent six-slot expectation: do not call the compiler to produce expected XML.
    config = json.loads(SUPPLEMENT.read_text())["config"]
    expected = ET.parse(SOURCE).getroot()
    main = expected.find("BehaviorTree[@ID='main']/Sequence")
    main[1].set("value", config["Perception"]["position"])
    for field in ("exposure_time", "object_name", "model_name", "threshold"):
        main[4].set(field, config["Perception"][field])
    main[8].set("place_pose", config["Place"]["place_pose"])
    assert trees(ET.fromstring(content)) == trees(expected)
    assert len(run["result"]["parameter_sources"]) == 6
    assert run["result"]["validation"]["symbolic_validation"]["valid"] is True
    assert run["operation"] == "generate_bt" and run["session_ref"] == SESSION
    assert run["ontology_snapshot"]["payload_sha256"] == version["payload_sha256"]
    assert run["ontology_snapshot"]["source_asset_ids"] == [s["source_asset_id"] for s in sources]
    assert [s["sha256"] for s in run["ontology_snapshot"]["sources"]] == [
        sha(p.read_bytes()) for p in (SOURCE, MODELS, SUPPLEMENT)]
    assert run["components"] == {
        "profile_id": "ur_pick_place", "compiler_version": "sha256:" + sha(Path(api("compiler").__file__).read_bytes()),
        "rule_digest": validation["validation_shapes_sha256"], "prompt_version": None, "model": {"invoked": False}}
    assert {a["domain"] for a in assets.list_assets()} == {"data", "robotics"}
    assert assets.read_payload(asset["ontology_id"], version["version_id"])


def test_generation_pins_old_version_even_when_new_parameters_are_published(tmp_path):
    call = pipeline()
    root, assets, asset, old, _ = setup_assets(tmp_path)
    old_bytes = assets.read_payload(asset["ontology_id"], old["version_id"])
    graph = Graph().parse(data=old_bytes, format="xml")
    graph.set((S.Perception_Current, S.threshold, Literal("0.61", datatype=XSD.string)))
    newer = assets.add_version(asset["ontology_id"], graph.serialize(format="xml", encoding="utf-8"),
        payload_kind="robotics_rdf", parent_version_id=old["version_id"], source_asset_ids=old["source_asset_ids"],
        metadata=old["metadata"])
    assets.publish_version(asset["ontology_id"], newer["version_id"], mode="demo")
    for version, expected_threshold in ((old, "0.72"), (newer, "0.61")):
        run = generate(call, root, asset, version, task())
        runs, candidates = check_saved(root, run, "generated")
        xml = ET.fromstring(runs.read_artifact(run["run_id"], candidates[0]["artifact_id"]))
        assert xml.find("BehaviorTree[@ID='main']/Sequence")[4].get("threshold") == expected_threshold
        assert run["version_id"] == version["version_id"]
        assert runs.manifest(run["run_id"])["ontology_snapshot"]["payload_sha256"] == version["payload_sha256"]
    assert assets.read_payload(asset["ontology_id"], old["version_id"]) == old_bytes
    assert len(RunStore(root).list_runs(session_ref=SESSION)) == 2


@pytest.mark.parametrize("change,expected", [
    ({}, "needs_information"), ({"part": None}, "needs_clarification"),
    ({"operation": "weld"}, "unsupported"), ({"unsupported_requests": ["添加打磨步骤"]}, "unsupported"),
    ({"unexpected": "invalid field"}, "error"),
])
def test_real_rejections_remain_reports_without_candidates(tmp_path, change, expected):
    call = pipeline()
    root, _, asset, version, _ = setup_assets(tmp_path, mode="real")
    run = generate(call, root, asset, version, task(**change), mode="real")
    check_saved(root, run, expected)
    assert run["result"]["issues"] and run["result"]["candidate_written"] is False
    assert not list((root / "artifacts").rglob("*.xml"))


@pytest.mark.parametrize("bad", ["unpublished", "wrong_mode", "data", "missing", "damaged"])
def test_invalid_version_is_rejected_before_generation_or_run_creation(tmp_path, monkeypatch, bad):
    call = pipeline()
    root, assets, asset, version, _ = setup_assets(tmp_path)
    mode = "demo"
    if bad == "unpublished":
        version = assets.add_version(asset["ontology_id"], assets.read_payload(asset["ontology_id"], version["version_id"]),
                                     payload_kind="robotics_rdf")
    elif bad == "wrong_mode":
        mode = "real"
    elif bad == "data":
        asset = next(a for a in assets.list_assets() if a["domain"] == "data")
        version = assets.list_versions(asset["ontology_id"])[0]
    elif bad == "missing":
        version = {**version, "version_id": "ver_missing"}
    else:
        (root / version["path"]).write_bytes(b"changed ontology")

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid input reached the generation kernel")

    monkeypatch.setattr(api("generation"), "generate_task", forbidden)
    with pytest.raises((KeyError, ValueError)):
        generate(call, root, asset, version, task(), mode=mode)
    assert RunStore(root).list_runs() == []


def test_unexpected_kernel_exception_leaves_failed_run_with_report(tmp_path, monkeypatch):
    call = pipeline()
    root, _, asset, version, _ = setup_assets(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("injected integration failure")

    monkeypatch.setattr(api("generation"), "generate_task", broken)
    run = generate(call, root, asset, version, task())
    check_saved(root, run, "error")
    assert run["result"]["issues"]


def test_failed_storage_commit_cannot_be_reported_as_success(tmp_path, monkeypatch):
    call = pipeline()
    root, _, asset, version, _ = setup_assets(tmp_path)
    original = RunStore.finish

    def fail_commit(self, *args, **kwargs):
        raise OSError("injected durable storage failure")

    monkeypatch.setattr(RunStore, "finish", fail_commit)
    with pytest.raises(OSError):
        generate(call, root, asset, version, task())
    monkeypatch.setattr(RunStore, "finish", original)
    runs = RunStore(root)
    pending = runs.list_runs()
    assert len(pending) == 1 and pending[0]["status"] == "running"
    assert runs.artifacts(pending[0]["run_id"]) == []
    assert runs.recover_interrupted() == [pending[0]["run_id"]]


def test_new_process_reads_actual_generated_history_and_verifies_bytes(tmp_path):
    call = pipeline()
    root, assets, asset, version, _ = setup_assets(tmp_path)
    run = generate(call, root, asset, version, task())
    runs, _ = check_saved(root, run, "generated")
    code = '''import json, sys
from ontology_assets import AssetStore
from domain_runs import RunStore
r = RunStore(sys.argv[1]); rid = sys.argv[2]
artifacts = r.artifacts(rid)
assert all(r.read_artifact(rid, a['artifact_id']) for a in artifacts)
assert 'server' not in sys.modules
print(json.dumps({'run': r.get_run(rid), 'events': r.events(rid), 'manifest': r.manifest(rid),
                  'assets': AssetStore(sys.argv[1]).list_assets()}))
'''
    process = subprocess.run([sys.executable, "-c", code, str(root), run["run_id"]], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"run": run, "events": runs.events(run["run_id"]),
        "manifest": runs.manifest(run["run_id"]), "assets": assets.list_assets()}

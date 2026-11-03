"""T06 RED: offline storage demo, real process boundaries and verifiable history — DR-064 / DR-065."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from domain_runs import RunStore
from ontology_assets import AssetStore
from tests.robotics_support import FIXTURES, SOURCE, MODELS
from tests.storage_pipeline_support import ROOT, sha

SCRIPT = ROOT / "scripts/demo_domain_storage.py"
EXPECTED = {"demo-success": "generated", "real-missing-evidence": "needs_information",
            "interrupted-recovery": "interrupted"}

# sitecustomize is inherited by Python child processes, including the verifier.
GUARD = '''import json, os, sys
with open(os.environ['STORAGE_TEST_PROCESS_LOG'], 'a') as stream:
    stream.write(json.dumps({'pid': os.getpid(), 'ppid': os.getppid()}) + '\\n')
def audit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise AssertionError('Offline storage demo attempted network access')
    if event == 'open':
        name = str(args[0]).replace('\\\\', '/')
        if 'yang_implementation/' in name or '/fixtures/robotics/owl/' in name:
            raise AssertionError('Demo must build fresh OWL, not read archive/prebuilt OWL')
    if event == 'import' and args[0] == 'server':
        raise AssertionError('Offline storage must not import the live server')
sys.addaudithook(audit)
'''


def run_cli(output, *, case="all", extra=(), verify=None):
    assert SCRIPT.is_file(), "T07 must deliver scripts/demo_domain_storage.py"
    guard = output.parent / ".storage-test-guard"
    guard.mkdir(exist_ok=True)
    (guard / "sitecustomize.py").write_text(GUARD)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATAMIND_", "BT_AGENT_")) and "API_KEY" not in k}
    env.update(PYTHONPATH=os.pathsep.join((str(guard), str(ROOT))),
               STORAGE_TEST_PROCESS_LOG=str(guard / "processes.jsonl"),
               DATAMIND_WORKDIR=str(output.parent / "legacy"))
    args = ["--verify-storage", str(verify)] if verify else ["--case", case, "--output-dir", str(output)]
    return subprocess.run([sys.executable, str(SCRIPT), *args, *extra], cwd=output.parent, env=env,
                          capture_output=True, text=True, timeout=90)


def read_summary(output):
    paths = list(output.glob("*/summary.json"))
    assert len(paths) == 1, paths
    directory = paths[0].parent.resolve()
    summary = json.loads(paths[0].read_text())
    assert Path(summary["run_dir"]) == directory
    assert Path(summary["storage_root"]).is_absolute()
    assert Path(summary["storage_root"]).resolve().is_relative_to(directory)
    assert (directory / "summary.md").is_file()
    return directory, summary


def check_summary(output, expected):
    directory, summary = read_summary(output)
    assert summary["passed"] is True
    assert {row["case"] for row in summary["cases"]} == set(expected)
    root = Path(summary["storage_root"])
    assets, runs = AssetStore(root), RunStore(root)
    assert {a["domain"] for a in assets.list_assets()} == {"data", "robotics"}
    assert len(list(root.rglob("*.sqlite3"))) == 1
    observed = {json.loads(line)["pid"] for line in
                (output.parent / ".storage-test-guard/processes.jsonl").read_text().splitlines()}
    producer, verifier = summary["producer_pid"], summary["verification"]["pid"]
    assert producer != verifier and {producer, verifier} <= observed
    assert summary["verification"]["passed"] is True
    assert set(summary["verification"]["run_ids"]) == {r["run_id"] for r in runs.list_runs()}
    assert {a["ontology_id"] for a in summary["verification"]["assets"]} == {
        a["ontology_id"] for a in assets.list_assets()}
    for row in summary["cases"]:
        assert row["passed"] is True
        assert row["expected_status"] == row["actual_status"] == expected[row["case"]]
        assert row["case"] in (directory / "summary.md").read_text()
        run = runs.get_run(row["run_id"])
        assert run["ontology_id"] == row["ontology_id"] and run["version_id"] == row["version_id"]
        version = assets.get_version(run["ontology_id"], run["version_id"])
        assert sha(assets.read_payload(run["ontology_id"], run["version_id"])) == version["payload_sha256"]
        if row["case"] == "interrupted-recovery":
            assert run["status"] == "interrupted" and run["result"] is None
            assert row["crash_returncode"] == 23
            assert {row["crash_pid"], row["recovery_pid"]} <= observed
            assert len({producer, row["crash_pid"], row["recovery_pid"]}) == 3
            assert [e["payload"]["status"] for e in runs.events(run["run_id"]) if e["kind"] == "state"] == [
                "queued", "running", "interrupted"]
            assert runs.artifacts(run["run_id"]) == []
            continue
        assert run["status"] == "completed" and run["result"]["status"] == row["actual_status"]
        assert run["result"]["execution_readiness"] == "not_verified"
        manifest = runs.manifest(run["run_id"])
        assert manifest["version_id"] == version["version_id"]
        for source in manifest["ontology_snapshot"]["sources"]:
            assert sha(assets.read_source(source["source_asset_id"])) == source["sha256"]
        hashes = {s["role"]: s["sha256"] for s in manifest["ontology_snapshot"]["sources"]}
        assert hashes["behavior_tree"] == sha(SOURCE.read_bytes())
        assert hashes["node_models"] == sha(MODELS.read_bytes())
        artifacts = runs.artifacts(run["run_id"])
        candidates = [a for a in artifacts if a["kind"] == "candidate"]
        for item in artifacts:
            assert sha(runs.read_artifact(run["run_id"], item["artifact_id"])) == item["sha256"]
        if row["case"] == "demo-success":
            assert len(candidates) == 1 and len(run["result"]["parameter_sources"]) == 6
            raw = runs.read_artifact(run["run_id"], candidates[0]["artifact_id"])
            assert b"DEMO ONLY" in raw and b"__UNRESOLVED_" not in raw
            assert ET.fromstring(raw).find("BehaviorTree[@ID='main']/Sequence")[4].get("threshold") == "0.72"
            assert hashes["demo_supplement"] == sha((FIXTURES / "demo_supplement.json").read_bytes())
            versions = assets.list_versions(run["ontology_id"])
            assert len(versions) >= 2 and versions[-1]["version_id"] != run["version_id"]
        else:
            assert not candidates and run["result"]["issues"]
            assert run["mode"] == "real" and "demo_supplement" not in hashes
    return summary


def test_all_cases_build_fresh_offline_and_verify_in_separate_processes(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    for name in ("ont_chats.json", "edits_demo.json", "ont_usage.json"):
        (legacy / name).write_text('{"sentinel":"do not replace"}')
    before = {p: p.read_bytes() for p in legacy.iterdir()}
    output = tmp_path / "outputs"
    process = run_cli(output)
    assert process.returncode == 0, process.stdout + process.stderr
    summary = check_summary(output, EXPECTED)
    assert summary["run_dir"] in process.stdout
    assert all(p.read_bytes() == content for p, content in before.items())
    probe = run_cli(output, verify=summary["storage_root"])
    assert probe.returncode == 0, probe.stdout + probe.stderr
    result = json.loads(probe.stdout)
    assert result["passed"] is True
    assert result["pid"] != summary["producer_pid"]
    assert set(result["run_ids"]) == set(summary["verification"]["run_ids"])


@pytest.mark.parametrize("case", list(EXPECTED))
def test_each_storage_demo_case_is_independently_runnable(tmp_path, case):
    output = tmp_path / "outputs"
    process = run_cli(output, case=case)
    assert process.returncode == 0, process.stdout + process.stderr
    check_summary(output, {case: EXPECTED[case]})


def test_repeated_demo_preserves_prior_history_and_all_source_fixtures(tmp_path):
    before = {p: p.read_bytes() for p in FIXTURES.rglob("*") if p.is_file()}
    output = tmp_path / "outputs"
    first = run_cli(output, case="demo-success")
    assert first.returncode == 0, first.stdout + first.stderr
    original = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    second = run_cli(output, case="demo-success")
    assert second.returncode == 0, second.stdout + second.stderr
    summaries = [json.loads(p.read_text()) for p in output.glob("*/summary.json")]
    assert len(summaries) == 2
    assert len({s["storage_root"] for s in summaries}) == 2
    assert len({s["cases"][0]["run_id"] for s in summaries}) == 2
    assert all(p.read_bytes() == content for p, content in original.items())
    assert all(p.read_bytes() == content for p, content in before.items())


def test_independent_verification_is_read_only_and_does_not_interrupt_active_runs(tmp_path):
    output = tmp_path / "outputs"
    process = run_cli(output, case="demo-success")
    assert process.returncode == 0, process.stdout + process.stderr
    _, summary = read_summary(output)
    root = Path(summary["storage_root"])
    runs = RunStore(root)
    completed = runs.get_run(summary["cases"][0]["run_id"])
    queued = runs.create_run(**{key: completed[key] for key in (
        "ontology_id", "version_id", "operation", "task", "mode", "session_ref", "components")})
    active = runs.start(queued["run_id"])
    events = runs.events(active["run_id"])
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    verified = run_cli(output, verify=root)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert active["run_id"] in json.loads(verified.stdout)["run_ids"]
    assert runs.get_run(active["run_id"]) == active
    assert runs.events(active["run_id"]) == events
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("invalid", ["missing", "malformed"])
def test_invalid_input_is_failure_not_expected_missing_evidence(tmp_path, invalid):
    path = tmp_path / "input.xml"
    if invalid == "malformed":
        path.write_text("not XML")
    output = tmp_path / "outputs"
    process = run_cli(output, case="real-missing-evidence", extra=("--source", str(path)))
    assert process.returncode != 0
    _, summary = read_summary(output)
    assert summary["passed"] is False
    assert len(summary["cases"]) == 1
    row = summary["cases"][0]
    assert row["passed"] is False and row["actual_status"] == "error" and row["issues"]


@pytest.mark.parametrize("target", ["candidate", "manifest", "ontology"])
def test_independent_verification_fails_on_damaged_files_without_changing_history(tmp_path, target):
    output = tmp_path / "outputs"
    process = run_cli(output, case="demo-success")
    assert process.returncode == 0, process.stdout + process.stderr
    _, summary = read_summary(output)
    root = Path(summary["storage_root"])
    runs = RunStore(root)
    run = runs.get_run(summary["cases"][0]["run_id"])
    if target == "ontology":
        record = AssetStore(root).get_version(run["ontology_id"], run["version_id"])
    else:
        record = next(a for a in runs.artifacts(run["run_id"]) if a["kind"] == target)
    (root / record["path"]).write_bytes(b"injected corruption")
    verified = run_cli(output, verify=root)
    assert verified.returncode != 0, verified.stdout + verified.stderr
    assert runs.get_run(run["run_id"]) == run


@pytest.mark.parametrize("failure", ["reported_failure", "exception"])
def test_cli_exit_code_follows_actual_failure(tmp_path, monkeypatch, failure):
    cli = importlib.import_module("scripts.demo_domain_storage")

    def failed(*args, **kwargs):
        if failure == "exception":
            raise RuntimeError("injected demo failure")
        return {"passed": False, "run_dir": str(tmp_path), "storage_root": str(tmp_path / "storage"),
                "cases": [], "verification": {"passed": False}}

    monkeypatch.setattr(cli, "run_demo", failed)
    assert cli.main(["--case", "all", "--output-dir", str(tmp_path)]) != 0


@pytest.mark.parametrize("exists", [False, True])
def test_verification_does_not_initialize_missing_or_empty_storage(tmp_path, exists):
    root = tmp_path / "unknown-storage"
    if exists:
        root.mkdir()
    process = run_cli(tmp_path / "outputs", verify=root)
    assert process.returncode != 0, process.stdout + process.stderr
    assert root.exists() is exists
    assert not list(root.rglob("*"))

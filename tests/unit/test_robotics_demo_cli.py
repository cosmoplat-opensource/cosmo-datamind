"""T08 RED: runnable offline evidence, accurate exit status and isolated runs — DR-065 / ROB-13."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.robotics_support import FIXTURES, SOURCE, MODELS, api

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/demo_robotics.py"
EXPECTED = {"demo-success": "generated", "real-missing-evidence": "needs_information", "invalid-contract": "rejected"}


def run_cli(output, case="all", *, cwd=None, extra=()):
    assert SCRIPT.is_file(), "T09 must deliver the runnable offline demo entry"
    # Execute the actual script in a subprocess that forbids network and legacy OWL reads.
    guard = '''import builtins, pathlib, runpy, socket, sys
def no_network(*args, **kwargs):
    raise AssertionError("Offline demo attempted network access")
socket.socket.connect = no_network
socket.create_connection = no_network
old_open, old_path_open = builtins.open, pathlib.Path.open
def check(path):
    text = str(path).replace("\\\\", "/")
    if "yang_implementation/" in text or "/fixtures/robotics/owl/" in text:
        raise AssertionError("Demo read original archive or prebuilt OWL: " + text)
def guarded_open(path, *args, **kwargs):
    check(path)
    return old_open(path, *args, **kwargs)
def guarded_path_open(path, *args, **kwargs):
    check(path)
    return old_path_open(path, *args, **kwargs)
builtins.open, pathlib.Path.open = guarded_open, guarded_path_open
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATAMIND_", "BT_AGENT_")) and "API_KEY" not in k}
    return subprocess.run([sys.executable, "-c", guard, str(SCRIPT), "--case", case,
                           "--output-dir", str(output), *extra], cwd=cwd or ROOT, env=env,
                          capture_output=True, text=True, timeout=150)


def read_summary(output):
    paths = list(output.glob("*/summary.json"))
    assert len(paths) == 1, paths
    path = paths[0]
    summary = json.loads(path.read_text())
    assert Path(summary["run_dir"]).resolve() == path.parent.resolve()
    assert (path.parent / "summary.md").is_file()
    return path.parent, summary


def test_all_cases_run_offline_and_report_actual_artifacts(tmp_path):
    output = tmp_path / "outputs"
    run = run_cli(output, cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    directory, summary = read_summary(output)
    assert summary["passed"] is True
    assert {row["case"] for row in summary["cases"]} == set(EXPECTED)
    assert str(directory.resolve()) in run.stdout
    for row in summary["cases"]:
        assert row["passed"] is True
        assert row["expected_status"] == row["actual_status"] == EXPECTED[row["case"]]
        assert row["case"] in run.stdout and row["case"] in (directory / "summary.md").read_text()
        artifacts = {}
        for item in row["artifacts"]:
            path = Path(item["path"])
            assert path.is_absolute() and path.resolve().is_relative_to(directory.resolve())
            assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
            artifacts[path.name] = path
        assert "validation_report.json" in artifacts
        if row["case"] == "demo-success":
            assert {"published.owl", "demo_candidate.xml"} <= set(artifacts)
            report = json.loads(artifacts["validation_report.json"].read_text())
            assert report["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
            assert report["models_sha256"] == hashlib.sha256(MODELS.read_bytes()).hexdigest()
            assert report["supplement_sha256"] == hashlib.sha256((FIXTURES / "demo_supplement.json").read_bytes()).hexdigest()
            assert report["ontology_sha256"] == hashlib.sha256(artifacts["published.owl"].read_bytes()).hexdigest()
            assert report["execution_readiness"] == "not_verified" and report["supplement_changes"]
        else:
            assert row["issues"]
            assert not any(name.endswith(".xml") for name in artifacts)
            assert not list((directory / row["case"]).rglob("*.xml"))


@pytest.mark.parametrize("case", list(EXPECTED))
def test_each_demo_case_is_independently_runnable(tmp_path, case):
    run = run_cli(tmp_path, case)
    assert run.returncode == 0, run.stdout + run.stderr
    _, summary = read_summary(tmp_path)
    assert len(summary["cases"]) == 1
    assert summary["cases"][0]["actual_status"] == EXPECTED[case]


@pytest.mark.parametrize("fault", ["unexpected_acceptance", "accepted_contract", "missing_artifact", "runtime_error", "invalid_source"])
def test_unexpected_acceptance_and_runtime_errors_exit_nonzero(tmp_path, monkeypatch, fault):
    cli = importlib.import_module("scripts.demo_robotics")
    if fault == "accepted_contract":
        loaded = api("consumer").load_ontology(FIXTURES / "owl/agent_real.owl")
        monkeypatch.setattr(api("consumer"), "load_ontology", lambda *args, **kwargs: loaded)
        case = "invalid-contract"
    elif fault in ("runtime_error", "invalid_source"):
        def broken_loader(*args, **kwargs):
            if fault == "invalid_source":
                raise ValueError("injected malformed source")
            raise OSError("injected read failure")
        if fault == "invalid_source":
            monkeypatch.setattr(api("importer"), "build_graph", broken_loader)
        else:
            monkeypatch.setattr(api("consumer"), "load_ontology", broken_loader)
        case = "invalid-contract"
    else:
        monkeypatch.setattr(api("generation"), "generate_task", lambda *args, **kwargs: {
            "status": "generated", "candidate_written": True, "candidate_path": str(tmp_path / "missing.xml"),
            "execution_readiness": "not_verified", "issues": [], "parameter_sources": [], "validation": {}})
        case = "real-missing-evidence" if fault == "unexpected_acceptance" else "demo-success"
    assert cli.main(["--case", case, "--output-dir", str(tmp_path)]) != 0
    _, summary = read_summary(tmp_path)
    assert summary["passed"] is False
    assert summary["cases"][0]["passed"] is False
    if fault in ("runtime_error", "invalid_source"):
        assert summary["cases"][0]["actual_status"] == "error"


def test_missing_input_is_an_error_not_an_expected_rejection(tmp_path):
    run = run_cli(tmp_path, "invalid-contract", extra=("--source", str(tmp_path / "absent.xml")))
    assert run.returncode != 0
    _, summary = read_summary(tmp_path)
    assert summary["passed"] is False and summary["cases"][0]["passed"] is False
    assert summary["cases"][0]["actual_status"] == "error"


def test_repeated_runs_preserve_previous_outputs_and_inputs(tmp_path):
    before = {p: p.read_bytes() for p in FIXTURES.rglob("*") if p.is_file()}
    output = tmp_path / "runs"
    first = run_cli(output, "demo-success")
    assert first.returncode == 0, first.stdout + first.stderr
    original = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    second = run_cli(output, "demo-success")
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(list(output.glob("*/summary.json"))) == 2
    assert all(p.read_bytes() == content for p, content in original.items())
    assert all(p.read_bytes() == content for p, content in before.items())

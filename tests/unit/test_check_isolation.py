"""The verification harness must never inherit live model configuration or mutable fixtures."""
from pathlib import Path
import subprocess

from scripts.check import ROOT, isolated_env


def test_check_environment_uses_only_committed_fixtures_and_disables_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAMIND_WORKDIR", "/must-not-read-live-state")
    monkeypatch.setenv("DATAMIND_ENGINE_DIR", "/must-not-load-engine")
    monkeypatch.setenv("DATAMIND_LLM_KEY", "test-sentinel")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-sentinel")
    monkeypatch.setenv("CLAW_DRIVER", "external-runtime")
    env = isolated_env(tmp_path)
    work = Path(env["DATAMIND_WORKDIR"])
    assert work == tmp_path / "workdir"
    assert not Path(env["DATAMIND_ENGINE_DIR"]).exists()
    assert not {"DATAMIND_LLM_KEY", "ANTHROPIC_API_KEY", "CLAW_DRIVER"}.intersection(env)
    expected = subprocess.check_output(["git", "show", "HEAD:workdir/demo_ir.json"], cwd=ROOT)
    assert (work / "demo_ir.json").read_bytes() == expected
    assert not (work / "engine_config.json").exists()

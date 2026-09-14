"""Deployment controls must not execute dotenv code or signal unrelated processes."""
from types import SimpleNamespace

import pytest

from scripts import service


def test_environment_is_literal_and_error_does_not_disclose_value(tmp_path):
    path = tmp_path / ".env"
    path.write_text('export MODEL="local model" # comment\nKEY=$(touch_marker)\nEMPTY=\n')
    assert service.read_env(path) == {"MODEL": "local model", "KEY": "$(touch_marker)", "EMPTY": ""}
    path.write_text('KEY="secret-with-unclosed-quote\n')
    with pytest.raises(ValueError) as exc:
        service.read_env(path)
    assert "secret" not in str(exc.value)


def test_environment_paths_use_repo_and_cli_overrides_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DATAMIND_PORT=8092\nDATAMIND_DB=examples/demo_metrics.db\n")
    env = service.service_env(path, port=18092)
    assert env["DATAMIND_PORT"] == "18092"
    assert env["DATAMIND_DB"] == str(service.ROOT / "examples/demo_metrics.db")


@pytest.mark.parametrize("pid,command", [(0, "gunicorn"), (1, "gunicorn"), (42, "unrelated-server")])
def test_stale_pid_never_authorizes_signalling_unrelated_process(tmp_path, monkeypatch, pid, command):
    path = tmp_path / "gunicorn.pid"
    path.write_text(str(pid))
    monkeypatch.setattr(service, "PIDFILE", path)
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=command))
    monkeypatch.setattr(service.os, "kill", lambda *a: pytest.fail("must not signal unrelated process"))
    assert service.owned_pid() is None
    service.stop()


def test_live_gunicorn_requires_exact_pidfile_identity(tmp_path, monkeypatch):
    path = tmp_path / "gunicorn.pid"
    path.write_text("42")
    monkeypatch.setattr(service, "PIDFILE", path)
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=f"python -m gunicorn --pid {path} wsgi:application"))
    assert service.owned_pid() == 42

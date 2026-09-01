# -*- coding: utf-8 -*-
"""Production WSGI/reverse-proxy configuration regression tests."""
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _config():
    return runpy.run_path(str(ROOT / "gunicorn.conf.py"))


def test_gunicorn_defaults_are_bounded_and_threaded(monkeypatch):
    for name in (
        "DATAMIND_HOST", "DATAMIND_PORT", "DATAMIND_GUNICORN_THREADS",
        "DATAMIND_GUNICORN_TIMEOUT", "DATAMIND_GUNICORN_GRACEFUL_TIMEOUT",
        "DATAMIND_GUNICORN_MAX_REQUESTS", "DATAMIND_GUNICORN_MAX_REQUESTS_JITTER",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = _config()
    assert cfg["bind"] == "127.0.0.1:8092"
    assert cfg["workers"] == 1
    assert cfg["worker_class"] == "gthread"
    assert 2 <= cfg["threads"] <= 64
    assert 60 <= cfg["timeout"] <= 3600


def test_gunicorn_env_overrides_and_invalid_fallback(monkeypatch):
    monkeypatch.setenv("DATAMIND_HOST", "127.0.0.2")
    monkeypatch.setenv("DATAMIND_PORT", "18092")
    monkeypatch.setenv("DATAMIND_GUNICORN_THREADS", "12")
    monkeypatch.setenv("DATAMIND_GUNICORN_TIMEOUT", "900")
    cfg = _config()
    assert cfg["bind"] == "127.0.0.2:18092"
    assert cfg["threads"] == 12
    assert cfg["timeout"] == 900
    monkeypatch.setenv("DATAMIND_PORT", "invalid")
    monkeypatch.setenv("DATAMIND_GUNICORN_THREADS", "999")
    cfg = _config()
    assert cfg["bind"].endswith(":8092")
    assert cfg["threads"] == 8


def test_nginx_template_preserves_host_limits_uploads_and_streams():
    text = (ROOT / "deploy/nginx/cosmo-datamind.conf").read_text(encoding="utf-8")
    assert "server 127.0.0.1:8092" in text
    assert "client_max_body_size 25m" in text
    assert "proxy_set_header Host $host" in text
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for" in text
    assert "proxy_buffering off" in text
    assert "proxy_read_timeout 1900s" in text

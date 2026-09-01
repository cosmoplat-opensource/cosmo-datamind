#!/usr/bin/env python3
"""Gunicorn defaults for COSMO DataMind.

One worker is deliberate: runtime sessions, job state and locks are currently process-local.
Concurrency is provided by gthread until those states are externalized.
"""
import os


def _bounded_env(name, default, low, high):
    try:
        value = int((os.environ.get(name) or str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return value if low <= value <= high else default


_host = os.environ.get("DATAMIND_HOST") or "127.0.0.1"
_port = _bounded_env("DATAMIND_PORT", 8092, 1, 65535)

bind = f"{_host}:{_port}"
workers = 1
worker_class = "gthread"
threads = _bounded_env("DATAMIND_GUNICORN_THREADS", 8, 2, 64)

# 构建/技能对比可持续数分钟；该超时仍是有限上限，可由运维按模型时延收紧。
timeout = _bounded_env("DATAMIND_GUNICORN_TIMEOUT", 1800, 60, 3600)
graceful_timeout = _bounded_env("DATAMIND_GUNICORN_GRACEFUL_TIMEOUT", 30, 10, 300)
keepalive = 5

# 长驻 worker 定期平滑回收，降低第三方解析库泄漏积累的风险。
max_requests = _bounded_env("DATAMIND_GUNICORN_MAX_REQUESTS", 5000, 100, 100000)
max_requests_jitter = _bounded_env("DATAMIND_GUNICORN_MAX_REQUESTS_JITTER", 500, 0, 10000)

accesslog = os.environ.get("DATAMIND_GUNICORN_ACCESS_LOG") or "-"
errorlog = os.environ.get("DATAMIND_GUNICORN_ERROR_LOG") or "-"
_loglevel = (os.environ.get("DATAMIND_LOG_LEVEL") or "info").lower()
loglevel = _loglevel if _loglevel in {"debug", "info", "warning", "error", "critical"} else "info"
capture_output = True
preload_app = False

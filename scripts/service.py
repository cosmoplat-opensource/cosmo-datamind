#!/usr/bin/env python3
"""Manage the local persistent Gunicorn service without killing unrelated listeners."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".deploy"
PIDFILE = STATE / "gunicorn.pid"
MANIFEST = STATE / "service.json"


def read_env(path):
    """Read literal dotenv assignments; never execute shell expressions or print secrets."""
    result = {}
    if not path.exists():
        return result
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"Invalid environment assignment on line {number}")
        try:
            tokens = shlex.split(value, comments=True)
        except ValueError:
            raise ValueError(f"Invalid environment quoting on line {number}") from None
        if len(tokens) > 1:
            raise ValueError(f"Quote values containing spaces on line {number}")
        result[key] = tokens[0] if tokens else ""
    return result


def service_env(env_file, host=None, port=None):
    env = dict(os.environ)
    env.update(read_env(env_file))
    for key, fallback in {"DATAMIND_DB": "examples/demo_metrics.db",
                          "DATAMIND_WORKDIR": "workdir",
                          "DATAMIND_ENGINE_DIR": "../ontology-engine",
                          "DATAMIND_OUTPUTS_DIR": "../outputs"}.items():
        path = Path(env.get(key) or fallback).expanduser()
        env[key] = str((ROOT / path).resolve())
    env["DATAMIND_HOST"] = host or env.get("DATAMIND_HOST") or "127.0.0.1"
    env["DATAMIND_PORT"] = str(port or env.get("DATAMIND_PORT") or "8092")
    if env["DATAMIND_HOST"] not in {"127.0.0.1", "localhost"}:
        raise ValueError("This local service manager requires a loopback host")
    if not env["DATAMIND_PORT"].isdigit() or not 1 <= int(env["DATAMIND_PORT"]) <= 65535:
        raise ValueError("Invalid service port")
    return env


def source_digest():
    files = [p for p in ROOT.glob("*.py") if not p.name.startswith("test_")]
    files += [p for p in (ROOT / "ui").rglob("*") if p.suffix in {".js", ".css", ".html"}]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def owned_pid():
    try:
        pid = int(PIDFILE.read_text().strip())
    except (OSError, ValueError):
        return None
    if pid <= 1:
        return None
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    # A stale PID file never authorizes killing a reused PID or another service.
    return pid if "gunicorn" in result.stdout and str(PIDFILE) in result.stdout else None


def get_status():
    data = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    pid = owned_pid()
    data.update(running=pid is not None, pid=pid,
                source_changed=data.get("source_sha256") != source_digest())
    if pid and data.get("url"):
        try:
            with urlopen(data["url"] + "/api/db/check", timeout=5) as response:
                data["database_ready"] = json.load(response).get("ok") is True
        except (OSError, ValueError):
            data["database_ready"] = False
    return data


def stop():
    pid = owned_pid()
    if pid is None:
        print("No owned Gunicorn service is running.")
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if owned_pid() is None:
            print("Gunicorn service stopped.")
            return
        time.sleep(0.2)
    raise RuntimeError("Graceful stop timed out; the service was not forcibly killed")


def start(env):
    if owned_pid() is not None:
        status = get_status()
        if status["source_changed"]:
            raise RuntimeError("Service is running older source; use restart")
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    host, port = env["DATAMIND_HOST"], int(env["DATAMIND_PORT"])
    with socket.socket() as sock:
        sock.settimeout(2)
        if sock.connect_ex((host, port)) == 0:
            raise RuntimeError(f"Port {port} is occupied; no process was stopped")
    if not Path(env["DATAMIND_DB"]).is_file():
        raise ValueError("Configured SQLite database does not exist")
    STATE.mkdir(exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    PIDFILE.unlink(missing_ok=True)
    subprocess.run([sys.executable, "-m", "gunicorn", "-c", str(ROOT / "gunicorn.conf.py"),
                    "wsgi:application", "--daemon", "--pid", str(PIDFILE),
                    "--access-logfile", str(STATE / "access.log"),
                    "--error-logfile", str(STATE / "error.log")],
                   cwd=ROOT, env=env, check=True)
    data = {"url": f"http://{host}:{port}", "server": "gunicorn", "workers": 1,
            "workdir": env["DATAMIND_WORKDIR"], "database": env["DATAMIND_DB"],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source_sha256": source_digest(), "env_file_loaded": True}
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.chmod(MANIFEST, 0o600)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(data["url"] + "/api/db/check", timeout=2) as response:
                if json.load(response).get("ok") is True and owned_pid():
                    print(json.dumps(get_status(), ensure_ascii=False, indent=2))
                    return
        except (OSError, ValueError, URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Service did not become ready; inspect {STATE / 'error.log'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "restart", "status"))
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        if args.action == "status":
            status = get_status()
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return int(not status["running"])
        if args.action in {"stop", "restart"}:
            stop()
        if args.action in {"start", "restart"}:
            start(service_env(args.env_file, args.host, args.port))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

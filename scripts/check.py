#!/usr/bin/env python3
"""Reproducible local/CI checks against disposable fixtures, with no configured LLM.

Usage: .venv/bin/python scripts/check.py --suite all
Reports stay in .check-results; the server and runtime workdir are always cleaned up.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def isolated_env(directory):
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("DATAMIND_") or key in {
            "CLAW_DRIVER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY",
            "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY",
            "HERMES_PROVIDER", "HERMES_MODEL", "CLAUDE_MODEL", "OPENCLAW_MODEL",
        }:
            env.pop(key, None)
    work = directory / "workdir"
    work.mkdir()
    # Only committed fixture bytes: never copy local secrets or the user's edited graphs.
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "workdir"], cwd=ROOT, text=True).splitlines()
    for name in names:
        if Path(name).suffix != ".json":
            continue
        dest = directory / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT))
    env.update(DATAMIND_WORKDIR=str(work), DATAMIND_DB=str(ROOT / "examples/demo_metrics.db"),
               DATAMIND_ENGINE_DIR=str(directory / "no-engine"),
               DATAMIND_OUTPUTS_DIR=str(directory / "no-outputs"),
               DATAMIND_HOST="127.0.0.1", PYTHONUNBUFFERED="1")
    return env


def run_check(name, args, env, reports, results):
    start = time.monotonic()
    with (reports / f"{name}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=360)
        except subprocess.TimeoutExpired:
            log.write("\nFAIL: acceptance process exceeded 360 seconds\n")
            code = 124
        finally:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait()
    results.append({"name": name, "exit_code": code,
                    "seconds": round(time.monotonic() - start, 3), "log": f"{name}.log"})
    (reports / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"{name}: {'PASS' if code == 0 else 'FAIL'} ({results[-1]['seconds']}s)", flush=True)
    if code:
        print((reports / f"{name}.log").read_text()[-8000:], flush=True)
    return code == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--suite", choices=("unit", "integration", "ui", "all", "serve"), default="all")
    ap.add_argument("--report-dir", type=Path, default=ROOT / ".check-results")
    args = ap.parse_args()
    reports = args.report_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    results = []
    (reports / "summary.json").write_text("[]\n")
    with tempfile.TemporaryDirectory(prefix="datamind-check-") as raw:
        env = isolated_env(Path(raw))
        env["COVERAGE_FILE"] = str(reports / ".coverage")
        py = sys.executable
        if args.suite in ("unit", "all"):
            for name, command in (
                ("lint", [py, "-m", "ruff", "check", "."]),
                ("types", [py, "-m", "mypy", ".", "--no-error-summary"]),
                ("unit", [py, "-m", "coverage", "run", "-m", "pytest", "tests/", "-ra"]),
                ("coverage", [py, "-m", "coverage", "report"]),
            ):
                run_check(name, command, env, reports, results)
        if args.suite in ("integration", "ui", "all", "serve"):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env.update(DATAMIND_PORT=str(port), DATAMIND_URL=f"http://127.0.0.1:{port}")
            with (reports / "server.log").open("w", encoding="utf-8") as log:
                server = subprocess.Popen([py, "-m", "gunicorn", "-c", "gunicorn.conf.py", "wsgi:application"],
                                          cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline and server.poll() is None:
                        try:
                            with urlopen(env["DATAMIND_URL"] + "/api/db/check", timeout=1) as response:
                                if json.load(response).get("ok"):
                                    break
                        except (OSError, ValueError):
                            pass
                        time.sleep(0.2)
                    else:
                        raise RuntimeError(f"Isolated server failed to start; see {reports / 'server.log'}")
                    print(f"Isolated server: {env['DATAMIND_URL']} (temporary workdir)", flush=True)
                    if args.suite == "serve":
                        try:
                            server.wait()
                        except KeyboardInterrupt:
                            pass
                    if args.suite in ("integration", "all"):
                        run_check("integration", [py, "test_all.py"], env, reports, results)
                        run_check("strict10", [py, "scripts/test_ontology_strict_10_rounds.py",
                                  "--url", env["DATAMIND_URL"], "--workdir", env["DATAMIND_WORKDIR"],
                                  "--graph", "built_2d74f0", "--report", str(reports / "strict10.json")],
                                  env, reports, results)
                    if args.suite in ("ui", "all"):
                        for name in ("test_ui", "test_ui_ops"):
                            run_check(name, [py, name + ".py"], env, reports, results)
                        run_check("build-browser", [py, "tests/frontend/build-browser.py",
                                  "--url", env["DATAMIND_URL"], "--output", str(reports / "build-browser")],
                                  env, reports, results)
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
    (reports / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    return int(any(result["exit_code"] for result in results))


if __name__ == "__main__":
    raise SystemExit(main())

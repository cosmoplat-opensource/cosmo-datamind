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
ROUTE_LAYER = ("server.py,bp_actions.py,bp_engine.py,bp_metrics.py,bp_semantic.py,bp_decision.py,srv_actions.py,srv_engine.py,"
               "asset_lifecycle.py,bp_robotics.py,bp_robotics_application.py,bp_robotics_review.py,"
               "bp_robotics_task.py,bp_application_contracts.py")


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
    # Hosted runners also verify the complete RDF build and validation paths under coverage.
    timeout = {"unit": 7200, "unit-shard": 3300, "navigation-browser": 720}.get(name, 360)
    with (reports / f"{name}.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.write(f"\nFAIL: acceptance process exceeded {timeout} seconds\n")
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
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--unit-shard", nargs=2, type=int, metavar=("INDEX", "COUNT"),
                      help="Run a partition of the unit suite and save its branch coverage for CI.")
    mode.add_argument("--combine-unit", nargs="+", type=Path, metavar="COVERAGE_FILE",
                      help="Check lint, types and coverage using all successful unit partitions.")
    args = ap.parse_args()
    if (args.unit_shard or args.combine_unit) and args.suite != "unit":
        ap.error("partition options require --suite unit")
    reports = args.report_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    results = []
    (reports / "summary.json").write_text("[]\n")
    with tempfile.TemporaryDirectory(prefix="datamind-check-") as raw:
        env = isolated_env(Path(raw))
        env["COVERAGE_FILE"] = str(reports / ".coverage")
        # DR-147: targeted checks may use different follow-imports settings.
        # Do not reuse their incremental type cache for the full regression gate.
        env["MYPY_CACHE_DIR"] = str(reports / ".mypy_cache")
        py = sys.executable
        pytest_command = [py, "-m", "coverage", "run", "-m", "pytest", "tests/", "-ra"]
        if args.unit_shard:
            env["COVERAGE_FILE"] = str(reports / "coverage-data")
            command = [*pytest_command, "--ci-shard", *map(str, args.unit_shard),
                       "--junitxml", str(reports / "unit.xml")]
            return int(not run_check("unit-shard", command, env, reports, results))
        if args.suite in ("unit", "all"):
            unit_stage = ("unit", pytest_command)
            if args.combine_unit:
                unit_stage = ("coverage-combine", [py, "-m", "coverage", "combine", "--keep",
                                                  *map(str, args.combine_unit)])
            for name, command in (
                ("lint", [py, "-m", "ruff", "check", "."]),
                ("types", [py, "-m", "mypy", ".", "--no-error-summary"]),
                unit_stage,
                ("coverage", [py, "-m", "coverage", "report"]),
                # 分层下限(DR-106):确定性模块与路由层各自不得低于基线,避免高覆盖的小模块掩盖 server.py
                ("coverage-core", [py, "-m", "coverage", "report", "--omit=" + ROUTE_LAYER + ",tests/*,ath_adapter/e2e/*",
                                   "--fail-under=87"]),
                ("coverage-routes", [py, "-m", "coverage", "report", "--include=" + ROUTE_LAYER, "--fail-under=61"]),
            ):
                run_check(name, command, env, reports, results)
                # pytest exit 1 means test failures. Collection errors and interruptions
                # produce partial coverage and must be reported as the unit failure itself.
                if name == "coverage-combine" and results[-1]["exit_code"]:
                    break
                if name == "unit" and results[-1]["exit_code"] not in (0, 1):
                    break
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
                        for name, file in (("state-transition-browser", "state_transition_browser.py"),
                                           ("navigation-browser", "navigation_transitions.py"),
                                           ("representation-browser", "representation_fidelity_browser.py"),
                                           ("page-values-browser", "page_value_contracts.py")):
                            run_check(name, [py, "-m", "pytest", f"tests/frontend/{file}",
                                             "--junitxml", str(reports / f"{name}.xml")], env, reports, results)
                        run_check("functional-browser", [py, "-m", "pytest", "tests/frontend/functional_workflows.py"],
                                  env, reports, results)
                        run_check("build-webkit-browser", [py, "-m", "pytest", "tests/frontend/build_viewport_browser.py"],
                                  env, reports, results)
                        run_check("qa-history-browser", [py, "-m", "pytest", "tests/frontend/qa_history_browser.py"],
                                  env, reports, results)
                        for name in ("test_ui", "test_ui_ops"):
                            run_check(name, [py, name + ".py"], env, reports, results)
                        run_check("build-browser", [py, "tests/frontend/build-browser.py",
                                  "--url", env["DATAMIND_URL"], "--output", str(reports / "build-browser")],
                                  env, reports, results)
                        # DR-147: owns an enabled server/root, never reuses the legacy server.
                        run_check("robotics-browser", [py, "tests/frontend/robotics-build-browser.py",
                                  "--output", str(reports / "robotics-browser")], env, reports, results)
                        run_check("robotics-review-browser", [py, "tests/frontend/robotics-review-browser.py",
                                  "--output", str(reports / "robotics-review-browser")], env, reports, results)
                        run_check("robotics-task-browser", [py, "tests/frontend/robotics-task-browser.py",
                                  "--output", str(reports / "robotics-task-browser")], env, reports, results)
                        run_check("robotics-design-browser", [py, "tests/frontend/robotics-design-browser.py",
                                  "--output", str(reports / "robotics-design-browser")], env, reports, results)
                        # DR-155: real fixed-version detail, preparation and history navigation.
                        run_check("ontology-detail-browser", [py, "tests/frontend/ontology-detail-browser.py",
                                  "--output", str(reports / "ontology-detail-browser")], env, reports, results)
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

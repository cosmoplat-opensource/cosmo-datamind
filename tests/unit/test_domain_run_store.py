"""T04 RED: durable snapshots, events, lifecycle and explicit recovery — DR-064 / DR-065."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from tests.asset_support import data_bytes
from tests.run_support import complete, outputs, result, runs_at, setup_run


def test_run_fixes_version_task_components_and_namespaced_session(tmp_path):
    root, assets, runs, first, args = setup_run(tmp_path)
    expected = deepcopy(args)
    second = runs.create_run(**args)
    other_args = deepcopy(args)
    other_args["session_ref"]["namespace"] = "build_localstorage"
    other = runs.create_run(**other_args)
    args["task"]["instruction"] = "caller changed"
    args["components"]["compiler_version"] = "caller changed"
    assets.add_version(expected["ontology_id"], data_bytes("新版"), payload_kind="datamind_ir",
                       parent_version_id=expected["version_id"])
    reopened = runs_at(root)
    row = reopened.get_run(first["run_id"])
    for key, value in expected.items():
        assert row[key] == value
    snapshot = row["ontology_snapshot"]
    version = assets.get_version(expected["ontology_id"], expected["version_id"])
    assert snapshot["payload_sha256"] == version["payload_sha256"]
    assert snapshot["source_asset_ids"] == version["source_asset_ids"]
    assert row["status"] == "queued" and row["result"] is None
    assert {r["run_id"] for r in reopened.list_runs(session_ref=expected["session_ref"])} == {
        first["run_id"], second["run_id"]}
    assert [r["run_id"] for r in reopened.list_runs(session_ref=other_args["session_ref"])] == [other["run_id"]]
    row["task"]["instruction"] = "mutated query result"
    assert reopened.get_run(first["run_id"])["task"] == expected["task"]


@pytest.mark.parametrize("change", ["asset", "version", "cross_asset", "mode", "session", "secret"])
def test_invalid_run_inputs_are_rejected_without_records(tmp_path, change):
    _, assets, runs, _, args = setup_run(tmp_path)
    before = deepcopy(runs.list_runs())
    if change == "asset":
        args["ontology_id"] = "missing"
    elif change == "version":
        args["version_id"] = "missing"
    elif change == "cross_asset":
        args["ontology_id"] = assets.create_asset(name="other", domain="data")["ontology_id"]
    elif change == "mode":
        args["mode"] = "unknown"
    elif change == "session":
        args["session_ref"] = {"id": "chat-1"}
    else:
        args["components"]["model"]["api_key"] = "synthetic-test-secret"
    with pytest.raises((ValueError, KeyError)) as caught:
        runs.create_run(**args)
    assert "synthetic-test-secret" not in str(caught.value)
    assert runs.list_runs() == before
    if change == "secret":
        assert all(b"synthetic-test-secret" not in p.read_bytes()
                   for p in (tmp_path / "storage").rglob("*") if p.is_file())


def test_existing_chats_are_referenced_without_copy_or_rewrite(tmp_path):
    root, _, runs, _, args = setup_run(tmp_path)
    legacy = tmp_path / "legacy-workdir"
    legacy.mkdir()
    chats = legacy / "ont_chats.json"
    sentinel = b'{"chat-1":{"messages":[{"content":"DO-NOT-COPY-CHAT-BODY"}]}}'
    chats.write_bytes(sentinel)
    repo = Path(__file__).resolve().parents[2]
    process = subprocess.run([sys.executable, "-c", """
import json, sys
from domain_runs import RunStore
RunStore(sys.argv[1]).create_run(**json.loads(sys.argv[2]))
assert 'server' not in sys.modules
""", str(root), json.dumps(args)], cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(repo), DATAMIND_WORKDIR=str(legacy)),
        capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert chats.read_bytes() == sentinel
    assert all(b"DO-NOT-COPY-CHAT-BODY" not in p.read_bytes() for p in root.rglob("*") if p.is_file())
    assert len(runs.list_runs(session_ref=args["session_ref"])) == 2


def test_reopening_store_does_not_interrupt_active_run_and_recovery_is_explicit(tmp_path):
    root, _, runs, active, args = setup_run(tmp_path)
    queued = runs.create_run(**args)
    other_active = runs.create_run(**args)
    done = runs.create_run(**args)
    complete(runs, done)
    runs.start(active["run_id"])
    runs.start(other_active["run_id"])
    done_before = deepcopy(runs.get_run(done["run_id"]))
    done_events = deepcopy(runs.events(done["run_id"]))
    reopened = runs_at(root)
    assert reopened.get_run(active["run_id"])["status"] == "running"
    assert set(reopened.recover_interrupted()) == {active["run_id"], other_active["run_id"]}
    interrupted = reopened.get_run(active["run_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["task"] == args["task"] and interrupted["version_id"] == args["version_id"]
    assert reopened.events(active["run_id"])[-1]["payload"]["status"] == "interrupted"
    assert reopened.get_run(queued["run_id"])["status"] == "queued"
    assert reopened.get_run(done["run_id"]) == done_before
    assert reopened.events(done["run_id"]) == done_events
    after = deepcopy(reopened.events(active["run_id"]))
    assert reopened.recover_interrupted() == []
    assert reopened.events(active["run_id"]) == after


def test_new_process_reads_completed_history_and_crashed_run_can_be_recovered(tmp_path):
    root, _, runs, crashed, args = setup_run(tmp_path)
    done = runs.create_run(**args)
    complete(runs, done)
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(repo))
    crash = subprocess.run([sys.executable, "-c", """
import os, sys
from domain_runs import RunStore
RunStore(sys.argv[1]).start(sys.argv[2])
os._exit(23)
""", str(root), crashed["run_id"]], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert crash.returncode == 23, crash.stderr
    read = subprocess.run([sys.executable, "-c", """
import json, sys
from domain_runs import RunStore
s = RunStore(sys.argv[1])
assert 'server' not in sys.modules
assert s.get_run(sys.argv[2])['status'] == 'running'
recovered = s.recover_interrupted()
print(json.dumps({'recovered': recovered, 'done': s.get_run(sys.argv[3]),
                 'events': s.events(sys.argv[3]), 'manifest': s.manifest(sys.argv[3])}))
""", str(root), crashed["run_id"], done["run_id"]], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30)
    assert read.returncode == 0, read.stderr
    saved = json.loads(read.stdout)
    assert saved["recovered"] == [crashed["run_id"]]
    assert saved["done"]["status"] == "completed" and saved["done"]["result"] == result()
    assert saved["done"]["version_id"] == args["version_id"]
    assert saved["events"][-1]["payload"]["status"] == "completed"
    assert saved["manifest"]["run_id"] == done["run_id"]


@pytest.mark.parametrize("terminal", ["interrupted", "failed"])
def test_retry_creates_new_run_with_same_snapshot_and_preserves_original(tmp_path, terminal):
    _, assets, runs, old, args = setup_run(tmp_path)
    runs.start(old["run_id"])
    if terminal == "interrupted":
        runs.recover_interrupted()
    else:
        runs.finish(old["run_id"], result=result("error"), artifacts=[outputs()[1]])
    before = deepcopy(runs.get_run(old["run_id"]))
    events = deepcopy(runs.events(old["run_id"]))
    assets.add_version(args["ontology_id"], data_bytes("latest"), payload_kind="datamind_ir")
    retry = runs.retry(old["run_id"])
    assert retry["run_id"] != old["run_id"] and retry["retry_of"] == old["run_id"]
    assert retry["status"] == "queued" and retry["result"] is None
    for field in ("ontology_id", "version_id", "ontology_snapshot", "task", "mode", "session_ref", "components"):
        assert retry[field] == before[field]
    assert runs.artifacts(retry["run_id"]) == []
    assert runs.get_run(old["run_id"]) == before and runs.events(old["run_id"]) == events


def test_events_are_ordered_replayable_and_isolated_under_concurrent_writes(tmp_path):
    root, _, runs, first, args = setup_run(tmp_path)
    second = runs.create_run(**args)
    for run in (first, second):
        runs.start(run["run_id"])
    barrier = threading.Barrier(4)

    def write(worker):
        local = runs_at(root)
        barrier.wait(timeout=10)
        for i in range(10):
            local.append_event(first["run_id"], kind="progress", payload={"worker": worker, "index": i})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(4)))
    rows = runs.events(first["run_id"])
    assert [e["seq"] for e in rows] == list(range(1, len(rows) + 1))
    progress = [e for e in rows if e["kind"] == "progress"]
    assert len(progress) == 40
    assert {(e["payload"]["worker"], e["payload"]["index"]) for e in progress} == {
        (w, i) for w in range(4) for i in range(10)}
    cursor = rows[17]["seq"]
    assert runs.events(first["run_id"], after=cursor) == rows[18:]
    assert not [e for e in runs.events(second["run_id"]) if e["kind"] == "progress"]
    assert all(e["run_id"] == first["run_id"] for e in rows)


@pytest.mark.parametrize("action", ["finish_queued", "double_start", "restart_terminal", "finish_terminal", "append_terminal"])
def test_illegal_transitions_leave_state_and_events_unchanged(tmp_path, action):
    _, _, runs, run, _ = setup_run(tmp_path)
    rid = run["run_id"]
    if action == "double_start":
        runs.start(rid)
    elif action not in ("finish_queued",):
        complete(runs, run)
    before, events = deepcopy(runs.get_run(rid)), deepcopy(runs.events(rid))
    with pytest.raises(ValueError):
        if action in ("double_start", "restart_terminal"):
            runs.start(rid)
        elif action == "append_terminal":
            runs.append_event(rid, kind="progress", payload={"late": True})
        else:
            runs.finish(rid, result=result(), artifacts=outputs())
    assert runs.get_run(rid) == before and runs.events(rid) == events


def test_concurrent_completion_has_one_winner_without_duplicate_artifacts(tmp_path):
    root, _, runs, run, _ = setup_run(tmp_path)
    rid = run["run_id"]
    runs.start(rid)
    barrier = threading.Barrier(2)

    def finish(_):
        local = runs_at(root)
        barrier.wait(timeout=10)
        try:
            local.finish(rid, result=result(), artifacts=outputs())
        except ValueError:
            return "rejected"
        return "completed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(finish, range(2))) == ["completed", "rejected"]
    assert runs.get_run(rid)["status"] == "completed"
    assert len(runs.artifacts(rid)) == 3  # XML, report, manifest; manifest does not hash itself.
    assert len([e for e in runs.events(rid) if e["payload"].get("status") == "completed"]) == 1


def test_empty_store_and_missing_run_do_not_create_default_history(tmp_path):
    runs = runs_at(tmp_path / "storage")
    assert runs.list_runs() == []
    assert runs.recover_interrupted() == []
    for call in (runs.get_run, runs.start, runs.events, runs.artifacts, runs.manifest, runs.retry):
        with pytest.raises(KeyError):
            call("missing")
    assert runs.list_runs() == []

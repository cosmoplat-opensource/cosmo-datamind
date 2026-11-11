"""Frozen schema-5 migration, restart and immutable revisions — DR-070 / T06 RED."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import sqlite3
import subprocess
import sys

import pytest

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.storage import Storage
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_design_support import Harness, SNAPSHOT5, no_network, restore_v5, service_api, proposals, reply

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_frozen_schema5_fixture_has_legacy_constraints_and_real_history(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v5(root)
    assert db_rows(root) == fixture['rows']
    assert set(blob_bytes(root)) == set(fixture['blobs'])
    assert fixture['provenance']['real_model_invoked'] is False
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 5
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        # The original source-run CHECK really forbids a design operation.
        source_id = next(r['run_id'] for r in fixture['rows']['runs'] if r['operation'] == 'build_ontology')
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE runs SET operation='design_tbox' WHERE run_id=?", (source_id,))
    assert {'queued', 'running', 'completed', 'failed'} <= {r['status'] for r in fixture['rows']['runs']}
    assert fixture['rows']['robotics_task_sessions']


def test_schema5_migration_preserves_legacy_runs(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v5(root)
    before, files = db_rows(root), blob_bytes(root)
    runs, assets = RunStore(root), AssetStore(root)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] > 5
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    after = db_rows(root)
    for table, rows in before.items():
        assert len(after[table]) == len(rows)
        assert [{key: new[key] for key in old} for new, old in zip(after[table], rows)] == rows
    assert blob_bytes(root) == files
    assert runs.get_run(fixture['run_ids']['running'])['status'] == 'running'
    for artifact in before['run_artifacts']:
        raw = runs.read_artifact(artifact['run_id'], artifact['artifact_id'])
        assert raw == files[artifact['path']]
        if artifact['kind'] == 'manifest':
            assert runs.manifest(artifact['run_id']) == json.loads(raw)
    assert assets.load_robotics(**fixture['robotics_ref'], mode='real')['validation']['shacl_conforms'] is True
    # New design operations coexist with exact old task-session rows.
    h = Harness(root)
    queued = h.submit(description='要求')
    assert runs.get_run(queued['run_id'])['operation'] == 'design_tbox'
    assert db_rows(root)['robotics_task_sessions'] == before['robotics_task_sessions']


def test_migration_commit_failure_rolls_back_all_old_rows_and_bytes(tmp_path, monkeypatch):
    root = tmp_path / 'old'
    restore_v5(root)
    before, files = db_rows(root), blob_bytes(root)
    original = Storage.transaction
    versions = []

    @contextmanager
    def fail(self, *, write=False):
        with original(self, write=write) as conn:
            yield conn
            if write:
                versions.append(conn.execute('PRAGMA user_version').fetchone()[0])
                raise sqlite3.OperationalError('injected commit failure')

    monkeypatch.setattr(Storage, 'transaction', fail)
    with pytest.raises(sqlite3.OperationalError):
        RunStore(root)
    assert versions and versions[-1] > 5, 'Inject only after actually executing the migration'
    assert db_rows(root) == before and blob_bytes(root) == files
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 5
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    monkeypatch.setattr(Storage, 'transaction', original)
    assert len(RunStore(root).list_runs()) == len(before['runs'])


def test_parallel_migration_is_atomic_and_never_recovers_running_history(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v5(root)
    files = blob_bytes(root)
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: RunStore(root).list_runs(), range(4)))
    assert all(rows == snapshots[0] for rows in snapshots)
    assert RunStore(root).get_run(fixture['run_ids']['running'])['status'] == 'running'
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] > 5
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert blob_bytes(root) == files


@pytest.mark.parametrize('constructor', [RunStore, AssetStore])
def test_read_only_schema5_rejects_upgrade_without_changing_any_file(tmp_path, constructor):
    root = tmp_path / 'old'
    restore_v5(root)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        constructor(root, read_only=True)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_new_database_allows_only_explicit_design_run_input_branch(tmp_path):
    h = Harness(tmp_path / 'new')
    state = h.submit(description='要求')
    with sqlite3.connect(h.root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] > 5
        assert 'design_input' in {r[1] for r in conn.execute('PRAGMA table_info(runs)')}
        for assignment in ("operation='generate'", 'design_input=NULL', "mode='demo'", "build_input='{}'"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(f'UPDATE runs SET {assignment} WHERE run_id=?', (state['run_id'],))
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_new_process_reads_identical_revisions_and_exports_without_runtime(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.properties()
    before, files = db_rows(h.root), blob_bytes(h.root)
    expected = h.service.get_revision(state['session_id'], state['revision'])
    raw = h.service.export_revision(state['session_id'], state['revision'], format='owl')
    script = '''import hashlib, json, socket, sys
from domain_runs.robotics_design_service import DesignService
def deny(*a, **k): raise AssertionError('read attempted network/config resolution')
socket.socket.connect = deny
s = DesignService(sys.argv[1], read_only=True, route_resolver=deny)
r = int(sys.argv[3])
print(json.dumps({'state': s.get(sys.argv[2]), 'revision': s.get_revision(sys.argv[2], r),
'owl': hashlib.sha256(s.export_revision(sys.argv[2], r, format='owl')).hexdigest()}))
assert 'server' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', script, str(h.root), state['session_id'], str(state['revision'])],
        cwd=SNAPSHOT5.parents[3], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    from tests.build_pipeline_support import sha
    assert json.loads(result.stdout) == {'state': state, 'revision': expected, 'owl': sha(raw)}
    assert db_rows(h.root) == before and blob_bytes(h.root) == files


def test_restart_marks_inflight_without_model_replay(tmp_path):
    h = Harness(tmp_path / 'store')
    active = h.submit(description='active')
    queued = h.submit(description='queued', request_id='queued')
    assert h.service.claim_next()['run_id'] == active['run_id']
    reopened = service_api().DesignService(h.root, route_resolver=h.resolve)
    assert RunStore(h.root).get_run(active['run_id'])['status'] == 'running'
    recovered = reopened.recover_interrupted()
    assert recovered == [active['run_id']]
    assert reopened.recover_interrupted() == []
    assert RunStore(h.root).get_run(active['run_id'])['status'] == 'interrupted'
    assert RunStore(h.root).get_run(queued['run_id'])['status'] == 'queued'
    assert reopened.get(active['session_id'])['status'] == 'interrupted'
    assert reopened.get(active['session_id'])['revision'] == 0 and h.runtime.calls == []
    # New request ID explicitly retries; recovery itself never issues a model call.
    retried = reopened.retry(active['session_id'], request_id='retry', expected_revision=0,
        failed_run_id=active['run_id'])
    assert retried['run_id'] != active['run_id'] and h.runtime.calls == []


def test_completed_run_cannot_be_reexecuted_or_duplicate_a_revision(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    before, files = db_rows(h.root), blob_bytes(h.root)
    with pytest.raises(ValueError):
        h.service.execute(state['run_id'])
    assert db_rows(h.root) == before and blob_bytes(h.root) == files
    assert len(h.runtime.calls) == 1


def test_history_returns_independent_copies_and_detects_artifact_damage(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    revision = h.service.get_revision(state['session_id'], state['revision'])
    saved = json.loads(json.dumps(revision))
    revision['state']['result']['classes'].clear()
    assert h.service.get_revision(state['session_id'], state['revision']) == saved
    artifacts = RunStore(h.root).artifacts(state['run_id'])
    artifact = next(a for a in artifacts if a['name'] == 'design.json')
    (h.root / artifact['path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        h.service.export_revision(state['session_id'], state['revision'], format='json')


def test_recovery_does_not_interrupt_unrelated_legacy_run(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v5(root)
    h = Harness(root)
    state = h.submit(description='要求')
    h.service.claim_next()
    assert h.service.recover_interrupted() == [state['run_id']]
    assert RunStore(root).get_run(fixture['run_ids']['running'])['status'] == 'running'
    h.runtime.responses.append(reply(proposals()[0]))
    h.service.retry(state['session_id'], request_id='retry', expected_revision=0, failed_run_id=state['run_id'])
    assert h.run()['status'] == 'completed'


def test_design_run_cannot_use_old_generation_completion_to_bypass_validation(tmp_path):
    from tests.run_support import outputs, result
    h = Harness(tmp_path / 'store')
    h.submit(description='要求')
    run = h.service.claim_next()
    runs = RunStore(h.root)
    before, files = db_rows(h.root), blob_bytes(h.root)
    with pytest.raises(ValueError):
        runs.finish(run['run_id'], result=result(), artifacts=outputs())
    assert db_rows(h.root) == before and blob_bytes(h.root) == files


def test_same_failed_run_cannot_be_retried_twice_with_different_request_ids(tmp_path):
    from tests.robotics_design_support import expect_error
    h = Harness(tmp_path / 'store')
    state = h.submit(description='要求')
    h.runtime.responses.append(TimeoutError('synthetic timeout'))
    failed = h.run()
    h.service.retry(state['session_id'], request_id='retry-one', expected_revision=0,
        failed_run_id=failed['run_id'])
    before = db_rows(h.root)
    expect_error('session_busy', lambda: h.service.retry(state['session_id'], request_id='retry-two',
        expected_revision=0, failed_run_id=failed['run_id']), 409)
    assert db_rows(h.root) == before and len(h.runtime.calls) == 1

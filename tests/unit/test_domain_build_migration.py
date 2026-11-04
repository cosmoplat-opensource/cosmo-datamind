"""T02 RED: actual frozen schema-2 upgrade, rollback and historic compatibility — DR-059 / DR-064 / DR-065."""
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
from tests.build_pipeline_support import SNAPSHOT, blob_bytes, db_rows, inputs, restore_v2
from tests.run_support import components


def test_frozen_fixture_is_schema_two_and_internally_consistent(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v2(root)
    assert len(fixture['created_from_commit']) == 40
    assert db_rows(root) == fixture['rows']
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert len(blob_bytes(root)) == len(fixture['blobs'])
    assert {r['status'] for r in fixture['rows']['runs']} == {'queued', 'running', 'completed', 'failed'}


def test_schema_two_upgrade_preserves_rows_blobs_manifests_and_accepts_source_run(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v2(root)
    files, before = blob_bytes(root), db_rows(root)
    runs, assets = RunStore(root), AssetStore(root)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] > 2
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    after = db_rows(root)
    for table, rows in before.items():
        # Added columns are allowed; every old field and row remains exact.
        assert [{key: row[key] for key in old} for row, old in zip(after[table], rows)] == rows
        assert len(after[table]) == len(rows)
    assert blob_bytes(root) == files
    assert runs.get_run(fixture['run_ids']['running'])['status'] == 'running', 'Opening storage is not recovery'
    for artifact in fixture['rows']['run_artifacts']:
        raw = runs.read_artifact(artifact['run_id'], artifact['artifact_id'])
        assert raw == files[artifact['path']]
        if artifact['kind'] == 'manifest':
            assert runs.manifest(artifact['run_id']) == json.loads(raw)
    # Consume an actual real OWL published by schema-2 code after migration.
    context = assets.load_robotics(**fixture['robotics_ref'], mode='real')
    assert context['validation']['shacl_conforms'] is True
    sources = inputs(assets)
    build = runs.create_build_run(name='new source build', recipe='bt_xml', profile_id='ur_pick_place',
        source_asset_ids=[s['source_asset_id'] for s in sources], components={**components(), 'profile_id': 'ur_pick_place'},
        session_ref={'namespace': 'robotics_build', 'id': 'migrated'})
    assert build['version_id'] is None and build['output_asset_ref'] is None
    assert len(runs.list_runs()) == len(before['runs']) + 1
    for relative, content in files.items():
        assert (root / relative).read_bytes() == content


def test_migration_transaction_failure_keeps_schema_two_and_all_history(tmp_path, monkeypatch):
    root = tmp_path / 'old'
    restore_v2(root)
    before, files = db_rows(root), blob_bytes(root)
    transaction = Storage.transaction
    attempted = []

    @contextmanager
    def fail_before_commit(self, *, write=False):
        with transaction(self, write=write) as conn:
            yield conn
            if write:
                attempted.append(conn.execute('PRAGMA user_version').fetchone()[0])
                raise sqlite3.OperationalError('injected migration commit failure')

    monkeypatch.setattr(Storage, 'transaction', fail_before_commit)
    with pytest.raises(sqlite3.OperationalError):
        RunStore(root)
    assert attempted and attempted[-1] > 2, 'Must actually attempt an upgrade before the injected failure'
    assert db_rows(root) == before and blob_bytes(root) == files
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    monkeypatch.setattr(Storage, 'transaction', transaction)
    reopened = RunStore(root)
    assert len(reopened.list_runs()) == len(before['runs'])


def test_concurrent_openers_migrate_once_without_interrupting_running_task(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v2(root)
    before, files = db_rows(root), blob_bytes(root)
    with ThreadPoolExecutor(max_workers=4) as pool:
        snapshots = list(pool.map(lambda _: RunStore(root).list_runs(), range(4)))
    assert all(rows == snapshots[0] for rows in snapshots)
    assert len(snapshots[0]) == len(before['runs'])
    assert RunStore(root).get_run(fixture['run_ids']['running'])['status'] == 'running'
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] > 2
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert blob_bytes(root) == files


@pytest.mark.parametrize('constructor', [RunStore, AssetStore])
def test_unknown_schema_is_rejected_without_writing_or_dropping_history(tmp_path, constructor):
    root = tmp_path / 'old'
    restore_v2(root)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        conn.execute('PRAGMA user_version=999')
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):
        constructor(root)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_new_process_reads_migrated_history_without_server_or_recovery(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v2(root)
    RunStore(root)
    code = '''import json, sqlite3, sys
from domain_runs import RunStore
r = RunStore(sys.argv[1], read_only=True)
with sqlite3.connect(sys.argv[1] + '/domain.sqlite3') as c:
    version = c.execute('PRAGMA user_version').fetchone()[0]
assert 'server' not in sys.modules
print(json.dumps({'schema': version, 'run': r.get_run(sys.argv[2]), 'manifest': r.manifest(sys.argv[3])}))
'''
    result = subprocess.run([sys.executable, '-c', code, str(root), fixture['run_ids']['running'],
                             fixture['run_ids']['robotics_real']], cwd=SNAPSHOT.parents[3],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['schema'] > 2 and data['run']['status'] == 'running'
    assert data['manifest']['result']['status'] == 'needs_information'

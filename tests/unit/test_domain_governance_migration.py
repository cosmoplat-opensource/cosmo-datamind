"""T02 RED: frozen schema-4 migration, rollback and old consumption — DR-059 / DR-064 / DR-065."""
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
from tests.build_pipeline_support import blob_bytes, db_rows, sha
from tests.robotics_governance_support import SNAPSHOT4, graph, module, restore_v4
from tests.robotics_support import S
from tests.storage_pipeline_support import check_saved, generate, pipeline, setup_assets


def test_frozen_schema_four_fixture_has_real_history_and_consistent_bytes(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v4(root)
    assert len(fixture['created_from_commit']) == 40
    assert db_rows(root) == fixture['rows']
    assert {row['status'] for row in fixture['rows']['runs']} >= {'queued', 'running', 'completed', 'failed'}
    assert len(fixture['rows']['build_requests']) == 2
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    files = blob_bytes(root)
    assert len(files) == len(fixture['blobs'])
    for row in fixture['rows']['run_artifacts']:
        assert sha(files[row['path']]) == row['sha256']
    for row in fixture['rows']['versions']:
        assert sha(files[row['path']]) == row['payload_sha256']
    for row in fixture['rows']['sources']:
        assert sha(files[row['path']]) == row['sha256']


def test_upgrade_preserves_all_old_columns_rows_bytes_manifests_and_queued_build(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v4(root)
    before, files = db_rows(root), blob_bytes(root)
    runs, assets = RunStore(root), AssetStore(root)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] > 4
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    after = db_rows(root)
    for table, rows in before.items():
        assert len(after[table]) == len(rows)
        assert [{key: new[key] for key in old} for new, old in zip(after[table], rows)] == rows
    assert blob_bytes(root) == files
    assert runs.get_run(fixture['run_ids']['running'])['status'] == 'running'
    assert runs.get_run(fixture['run_ids']['source_queued'])['status'] == 'queued'
    for artifact in fixture['rows']['run_artifacts']:
        raw = runs.read_artifact(artifact['run_id'], artifact['artifact_id'])
        assert raw == files[artifact['path']]
        if artifact['kind'] == 'manifest': assert runs.manifest(artifact['run_id']) == json.loads(raw)
    assert assets.load_robotics(**fixture['robotics_ref'], mode='real')['validation']['shacl_conforms'] is True
    api = module('ontology_assets.governance')
    ref = fixture['source_build_ref']
    service = api.GovernanceService(root)
    untouched = db_rows(root)
    assert service.head(ref['ontology_id']) == {'version_id': ref['version_id'], 'revision': 0}
    assert db_rows(root) == untouched


def test_failed_migration_rolls_back_schema_rows_and_all_blobs(tmp_path, monkeypatch):
    root = tmp_path / 'old'
    restore_v4(root)
    before, files = db_rows(root), blob_bytes(root)
    original = Storage.transaction
    attempted = []
    @contextmanager
    def fail_before_commit(self, *, write=False):
        with original(self, write=write) as conn:
            yield conn
            if write:
                attempted.append(conn.execute('PRAGMA user_version').fetchone()[0])
                raise sqlite3.OperationalError('synthetic governance migration failure')
    monkeypatch.setattr(Storage, 'transaction', fail_before_commit)
    with pytest.raises(sqlite3.OperationalError): RunStore(root)
    assert attempted and attempted[-1] > 4, 'Must really attempt the new migration before failure'
    assert db_rows(root) == before and blob_bytes(root) == files
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    monkeypatch.setattr(Storage, 'transaction', original)
    assert len(RunStore(root).list_runs()) == len(before['runs'])


def test_parallel_initializers_upgrade_once_without_recovering_running_tasks(tmp_path):
    root = tmp_path / 'old'
    fixture = restore_v4(root)
    before, files = db_rows(root), blob_bytes(root)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda _: RunStore(root).list_runs(), range(4)))
    assert all(rows == records[0] for rows in records)
    assert len(records[0]) == len(before['runs'])
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] > 4
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert RunStore(root).get_run(fixture['run_ids']['running'])['status'] == 'running'
    assert blob_bytes(root) == files


@pytest.mark.parametrize('constructor', [AssetStore, RunStore])
def test_read_only_schema_four_is_not_silently_upgraded(tmp_path, constructor):
    root = tmp_path / 'old'
    restore_v4(root)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): constructor(root, read_only=True)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('constructor', [AssetStore, RunStore])
def test_unknown_schema_preserves_every_file(tmp_path, constructor):
    root = tmp_path / 'unknown'
    restore_v4(root)
    with sqlite3.connect(root / 'domain.sqlite3') as conn: conn.execute('PRAGMA user_version=999')
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): constructor(root)
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_read_only_governance_never_creates_missing_storage(tmp_path):
    api = module('ontology_assets.governance')
    root = tmp_path / 'missing'
    with pytest.raises(ValueError): api.GovernanceService(root, read_only=True)
    assert not root.exists()


def test_new_process_reads_migrated_head_and_history_without_recovery(tmp_path):
    api = module('ontology_assets.governance')
    root = tmp_path / 'old'
    fixture = restore_v4(root)
    service = api.GovernanceService(root)
    oid = fixture['source_build_ref']['ontology_id']
    code = '''import json, sys
from ontology_assets.governance import GovernanceService
from domain_runs import RunStore
s = GovernanceService(sys.argv[1], read_only=True)
assert 'server' not in sys.modules
print(json.dumps({'head': s.head(sys.argv[2]), 'history': s.history(sys.argv[2]),
                  'status': RunStore(sys.argv[1], read_only=True).get_run(sys.argv[3])['status']}))
'''
    result = subprocess.run([sys.executable, '-c', code, str(root), oid, fixture['run_ids']['running']],
        cwd=SNAPSHOT4.parents[3], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual == {'head': service.head(oid), 'history': service.history(oid), 'status': 'running'}


def test_legacy_demo_and_real_contracts_keep_their_original_outcomes(tmp_path):
    # Baseline control deliberately uses real existing APIs, not a governance stub.
    from tests.robotics_generation_support import task
    for mode, expected in [('demo', 'generated'), ('real', 'needs_information')]:
        root, _, asset, version, _ = setup_assets(tmp_path / mode, mode=mode)
        saved = generate(pipeline(), root, asset, version, task(), mode=mode)
        check_saved(root, saved, expected)


def test_fixture_premises_have_real_protected_invocations_and_unmapped_parameters():
    from rdflib import RDF
    g = graph()
    assert (S.grasp_Sequence_1, RDF.type, S.SkillInvocation) in g
    assert str(g.value(S.grasp_Sequence_1, S.stepIndex)) == '1'
    assert str(g.value(S.Perception_Current, S.threshold)) != '0.73'
    assert not list(g.objects(S.PartType_A, S.hasPerceptionProfile))
    assert not list(g.objects(S.Slot_B_01, S.hasPlacementProfile))
    assert not list(g.subjects(RDF.type, S.ReadinessEvidence))

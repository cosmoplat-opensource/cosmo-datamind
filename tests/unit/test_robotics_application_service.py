"""T04 RED: real adaptation admission, atomic versions and restart/CAS behavior — DR-072."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.robotics_design import DesignAssets
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_modeling_support import no_network
from tests.robotics_application_integration_support import (
    service, candidate, begin, confirm, submit, finish, adapted, current_bundle, expect_error,
)

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_prepare_is_explicit_role_confirmation_without_asset_or_run_changes(tmp_path):
    candidate_api = service(tmp_path)
    h, original, ref = candidate(tmp_path)
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    state = candidate_api.prepare(**ref, mode='real', task_ref=None, request_id='prepare')
    assert state['asset_ref'] == ref and state['revision'] == 0
    assert state['status'] == 'awaiting_role_confirmation' and state['role_map'] is None
    assert state['role_proposal']['confirmed'] is False
    assert state['output_asset_ref'] is None and state['run_id'] is None
    after = db_rows(tmp_path)
    for table in ('assets', 'versions', 'sources', 'runs', 'run_artifacts'): assert after[table] == before[table]
    assert blob_bytes(tmp_path) == files and len(h.runtime.calls) == 2
    assert DesignAssets(tmp_path).export_json(**ref)


def test_confirm_then_submit_and_execute_creates_one_lineaged_candidate(tmp_path):
    service(tmp_path); h, original, ref = candidate(tmp_path)
    old_owl = AssetStore(tmp_path).read_payload(**ref); old_json = DesignAssets(tmp_path).export_json(**ref)
    first = begin(tmp_path, ref); confirmed = confirm(tmp_path, first)
    assert confirmed['role_map']['confirmed'] is True and confirmed['revision'] == first['revision'] + 1
    queued = submit(tmp_path, confirmed)
    assert queued['status'] == 'queued' and queued['output_asset_ref'] is None
    completed = finish(tmp_path, queued); new = completed['output_asset_ref']
    version = AssetStore(tmp_path).get_version(**new)
    assert new['ontology_id'] == ref['ontology_id'] and new['version_id'] != ref['version_id']
    assert version['parent_version_id'] == ref['version_id'] and version['publication_state'] == 'candidate'
    raw, doc = current_bundle(tmp_path, new)
    assert doc['origin']['asset_ref'] == ref and doc['parent_version_id'] == ref['version_id']
    assert doc['identity_map'] and doc['coverage'] and raw
    assert AssetStore(tmp_path).read_payload(**ref) == old_owl
    assert DesignAssets(tmp_path).export_json(**ref) == old_json and len(h.runtime.calls) == 2


@pytest.mark.parametrize('operation', ['prepare', 'confirm', 'submit'])
def test_idempotent_replay_does_not_create_duplicate_versions_or_runs(tmp_path, operation):
    service(tmp_path); _, _, ref = candidate(tmp_path)
    first = begin(tmp_path, ref)
    if operation == 'prepare':
        call = lambda: begin(tmp_path, ref)
        expected = first
    elif operation == 'confirm':
        expected = confirm(tmp_path, first)
        call = lambda: confirm(tmp_path, first)
    else:
        confirmed = confirm(tmp_path, first); expected = submit(tmp_path, confirmed)
        call = lambda: submit(tmp_path, confirmed)
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    assert call() == expected
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files


def test_same_request_id_with_different_prepare_input_conflicts(tmp_path):
    service(tmp_path); _, _, ref = candidate(tmp_path); begin(tmp_path, ref)
    before = db_rows(tmp_path)
    expect_error('request_conflict', lambda: begin(tmp_path, ref, mode='demo'))
    assert db_rows(tmp_path) == before


@pytest.mark.parametrize('operation', ['confirm', 'submit', 'unconfirmed_submit'])
def test_revision_conflicts_and_unconfirmed_submit_have_no_side_effect(tmp_path, operation):
    service(tmp_path); _, _, ref = candidate(tmp_path); initial = begin(tmp_path, ref)
    if operation == 'unconfirmed_submit':
        state = initial; code = 'confirmation_required'
    else:
        current = confirm(tmp_path, initial)
        state = {**(initial if operation == 'confirm' else current), 'revision': 999}
        code = 'revision_conflict'
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    expect_error(code, lambda: confirm(tmp_path, state, request_id='conflicting-confirm') if operation == 'confirm'
                 else submit(tmp_path, state, request_id='conflicting-submit'))
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files


def test_concurrent_submit_admits_one_durable_run(tmp_path):
    service(tmp_path); _, _, ref = candidate(tmp_path); state = confirm(tmp_path, begin(tmp_path, ref))
    gate = Barrier(3)
    def admit(_):
        gate.wait(timeout=10); return submit(tmp_path, state)
    before = len(RunStore(tmp_path).list_runs())
    with ThreadPoolExecutor(max_workers=3) as pool: results = list(pool.map(admit, range(3)))
    assert len({r['run_id'] for r in results}) == 1
    assert len(RunStore(tmp_path).list_runs()) == before + 1


def test_head_change_after_confirmation_rejects_submission(tmp_path):
    service(tmp_path); _, _, ref = candidate(tmp_path); state = confirm(tmp_path, begin(tmp_path, ref))
    assets = AssetStore(tmp_path); base = assets.get_version(**ref)
    assets.add_version(ref['ontology_id'], assets.read_payload(**ref), payload_kind='robotics_rdf',
        parent_version_id=ref['version_id'], source_asset_ids=base['source_asset_ids'], metadata={'synthetic_branch': True})
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    expect_error('head_conflict', lambda: submit(tmp_path, state))
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files


def test_head_change_during_queued_work_does_not_publish_over_concurrent_branch(tmp_path):
    service(tmp_path); _, _, ref = candidate(tmp_path); queued = submit(tmp_path, confirm(tmp_path, begin(tmp_path, ref)))
    assets = AssetStore(tmp_path); version = assets.get_version(**ref)
    branch = assets.add_version(ref['ontology_id'], assets.read_payload(**ref), payload_kind='robotics_rdf',
        parent_version_id=ref['version_id'], source_asset_ids=version['source_asset_ids'], metadata={'synthetic_branch': True})
    count = len(assets.list_versions(ref['ontology_id']))
    result = service(tmp_path).execute(queued['run_id'])
    assert result['status'] == 'failed' and result['output_asset_ref'] is None
    assert any(i['code'] == 'head_conflict' for i in result['result']['issues'])
    assert len(assets.list_versions(ref['ontology_id'])) == count
    assert assets.list_versions(ref['ontology_id'])[-1]['version_id'] == branch['version_id']


@pytest.mark.parametrize('target', ['source', 'design_json', 'ontology'])
def test_corruption_after_admission_fails_without_partial_application_version(tmp_path, target):
    service(tmp_path); _, original, ref = candidate(tmp_path); queued = submit(tmp_path, confirm(tmp_path, begin(tmp_path, ref)))
    assets = AssetStore(tmp_path); version = assets.get_version(**ref)
    if target == 'source': path = assets.get_source(version['source_asset_ids'][0])['path']
    elif target == 'ontology': path = version['path']
    else: path = next(a['path'] for a in RunStore(tmp_path).artifacts(original['run_id']) if a['name'] == 'design.json')
    (tmp_path / path).write_bytes(b'corrupt-synthetic-input')
    count = len(assets.list_versions(ref['ontology_id']))
    result = service(tmp_path).execute(queued['run_id'])
    assert result['status'] == 'failed' and result['output_asset_ref'] is None
    assert any(i['code'] == 'execution.source_integrity' for i in result['result']['issues'])
    assert len(assets.list_versions(ref['ontology_id'])) == count
    assert RunStore(tmp_path).artifacts(queued['run_id']) == []


def test_failed_version_write_rolls_back_blobs_and_allows_explicit_retry(tmp_path):
    service(tmp_path); _, _, ref = candidate(tmp_path); queued = submit(tmp_path, confirm(tmp_path, begin(tmp_path, ref)))
    with sqlite3.connect(tmp_path / 'domain.sqlite3') as conn:
        conn.execute("CREATE TRIGGER reject_application_version BEFORE INSERT ON versions "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic version write failure'); END")
    assets = AssetStore(tmp_path); count = len(assets.list_versions(ref['ontology_id'])); files = blob_bytes(tmp_path)
    failed = service(tmp_path).execute(queued['run_id'])
    assert failed['status'] == 'failed' and failed['output_asset_ref'] is None
    assert len(assets.list_versions(ref['ontology_id'])) == count and blob_bytes(tmp_path) == files
    assert RunStore(tmp_path).artifacts(queued['run_id']) == []
    with sqlite3.connect(tmp_path / 'domain.sqlite3') as conn: conn.execute('DROP TRIGGER reject_application_version')
    state = service(tmp_path).get(queued['adaptation_id'])
    retry = submit(tmp_path, state, request_id='explicit-retry')
    assert retry['run_id'] != queued['run_id']
    assert RunStore(tmp_path).get_run(retry['run_id'])['retry_of'] == queued['run_id']
    finish(tmp_path, retry)


def test_completed_state_and_bundle_survive_independent_process_without_repair_replay(tmp_path):
    service(tmp_path); _, _, state = adapted(tmp_path)
    expected = service(tmp_path).get(state['adaptation_id']); before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    script = '''
import json,sys
from domain_runs.robotics_application_service import ApplicationService
print(json.dumps(ApplicationService(sys.argv[1],read_only=True).get(sys.argv[2])))
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path), state['adaptation_id']],
        cwd=Path(__file__).resolve().parents[2], env=dict(os.environ), capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == expected
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files


def test_missing_role_rebase_clears_old_confirmation_and_keeps_original_version(tmp_path):
    service(tmp_path); h, old, ref = candidate(tmp_path, missing_role='ParameterBinding')
    state = begin(tmp_path, ref)
    assert state['status'] == 'needs_tbox_revision'
    assert any(i['code'] == 'schema.missing_role' for i in state['issues'])
    original = AssetStore(tmp_path).read_payload(**ref)
    # Exercise the existing design revision flow using scripted model proposals.
    edited = h.service.edit_classes(old['session_id'], request_id='revise-tbox', expected_revision=old['revision'])
    h.runtime.responses.append(reply_for_classes()); h.message(edited); h.run(); edited = h.get(edited)
    edited = h.service.confirm_classes(edited['session_id'], request_id='confirm-revised-classes',
        expected_revision=edited['revision'], snapshot_sha256=edited['snapshot_sha256'])
    from tests.robotics_design_support import proposals, reply
    h.runtime.responses.append(reply(proposals()[1])); h.message(edited); h.run(); edited = h.get(edited)
    h.service.confirm_schema(edited['session_id'], request_id='extract-revised',
        expected_revision=edited['revision'], snapshot_sha256=edited['snapshot_sha256']); h.run()
    new_ref = h.get(edited)['output_asset_ref']
    rebound = service(tmp_path).rebase(state['adaptation_id'], asset_ref=new_ref,
        expected_revision=state['revision'], request_id='bind-revised')
    assert rebound['asset_ref'] == new_ref and rebound['role_map'] is None and rebound['run_id'] is None
    assert rebound['status'] == 'awaiting_role_confirmation'
    assert AssetStore(tmp_path).read_payload(**ref) == original
    finish(tmp_path, submit(tmp_path, confirm(tmp_path, rebound)))


def reply_for_classes():
    from tests.robotics_design_support import proposals, reply
    return reply(proposals()[0])


def test_get_on_missing_root_and_old_schema_does_not_create_or_migrate(tmp_path):
    from tests.robotics_design_support import restore_v5
    root = tmp_path / 'absent'
    expect_error('not_found', lambda: service(root, read_only=True).get('missing'))
    assert not root.exists()
    root = tmp_path / 'old'; restore_v5(root); before, files = db_rows(root), blob_bytes(root)
    expect_error('storage_unavailable', lambda: service(root, read_only=True).get('missing'))
    assert db_rows(root) == before and blob_bytes(root) == files


@pytest.mark.parametrize('schema', [5, 7])
def test_writable_upgrade_preserves_old_rows_original_fields_and_artifact_bytes(tmp_path, schema):
    if schema == 5:
        from tests.robotics_design_support import restore_v5
        restore_v5(tmp_path / 'old'); root = tmp_path / 'old'
    else:
        root = tmp_path / 'current'; candidate(root)
    before, files = db_rows(root), blob_bytes(root)
    api = service(root)
    expect_error('not_found', lambda: api.get('unknown-adaptation'))
    after = db_rows(root)
    # Compare every original field; added schema columns are allowed, old values are not rewritten.
    for table, rows in before.items():
        assert len(after[table]) == len(rows)
        for old, upgraded in zip(rows, after[table]):
            assert {key: upgraded[key] for key in old} == old
    assert blob_bytes(root) == files
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []

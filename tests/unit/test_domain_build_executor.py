"""T04 RED: durable admission and real local executor lifecycle — DR-060 / DR-064 / DR-065."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import time

import pytest

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.storage import Storage
from tests.build_pipeline_support import db_rows, failure_result
from tests.run_support import setup_run
from tests.build_runtime_support import (alive, child_info, control_path, controlled_compute, executor,
                                         owner_process, running_executor, runtime_api, service,
                                         terminal, upload_args, wait_until)


def test_admission_records_actual_sources_and_components_before_execution(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root)
    args = upload_args()
    row = manager.submit(**args)
    assert row['status'] == 'queued' and row['output_asset_ref'] is None
    assert row['operation'] == 'build_ontology' and row['mode'] == 'real'
    assert row['components']['model'] == {'invoked': False}
    assert row['components']['compiler_version'].startswith('sha256:')
    from domains.robotics import importer
    from tests.build_pipeline_support import sha
    assert row['components']['compiler_version'] == 'sha256:' + sha(Path(importer.__file__).read_bytes())
    from domains.robotics.artifact_contract import VALIDATION_SHAPES_SHA256
    assert row['components']['rule_digest'] == VALIDATION_SHAPES_SHA256
    assert row['session_ref'] == {'namespace': 'robotics_build', 'id': args['request_id']}
    assets = AssetStore(root)
    for source in row['build_input']['sources']:
        assert assets.read_source(source['source_asset_id']) == args['sources'][source['role']]['content']
    assert assets.list_assets() == []


def test_idempotency_survives_reopen_and_full_queue_without_duplicate_sources(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root, max_pending=1)
    original = manager.submit(**upload_args())
    before = db_rows(root)
    again = service(root, max_pending=1).submit(**upload_args())
    assert again['run_id'] == original['run_id'] and db_rows(root) == before
    error = runtime_api()[0].BuildRequestError
    with pytest.raises(error) as caught:
        manager.submit(**upload_args('second'))
    assert caught.value.code == 'queue_full' and caught.value.status_code == 429
    assert db_rows(root) == before


@pytest.mark.parametrize('change', ['name', 'behavior_tree', 'node_models'])
def test_same_request_id_with_different_input_is_conflict(tmp_path, change):
    root = tmp_path / 'storage'
    manager = service(root)
    manager.submit(**upload_args())
    args = upload_args()
    if change == 'name':
        args['name'] = 'different ontology name'
    else:
        args['sources'][change]['content'] += b'\n'
    before = db_rows(root)
    with pytest.raises(runtime_api()[0].BuildRequestError) as caught:
        manager.submit(**args)
    assert caught.value.code == 'request_conflict' and caught.value.status_code == 409
    assert db_rows(root) == before


def test_concurrent_duplicate_requests_have_exactly_one_durable_admission(tmp_path):
    root = tmp_path / 'storage'
    service(root)
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: service(root).submit(**upload_args()), range(8)))
    assert len({r['run_id'] for r in rows}) == 1
    saved = db_rows(root)
    assert len(saved['runs']) == 1 and len(saved['sources']) == 2
    assert len(saved['run_events']) == 1


def test_concurrent_distinct_requests_cannot_overbook_pending_capacity(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root, max_pending=2)
    error = runtime_api()[0].BuildRequestError

    def submit(index):
        try:
            return manager.submit(**upload_args(f'request-{index}'))['run_id']
        except error as exc:
            assert exc.code == 'queue_full'
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [rid for rid in pool.map(submit, range(8)) if rid]
    assert len(set(accepted)) == 2
    rows = db_rows(root)
    assert len(rows['runs']) == 2 and len(rows['sources']) == 4
    assert all(r['status'] == 'queued' for r in rows['runs'])


def test_admission_commit_failure_rolls_back_request_sources_and_queue(tmp_path, monkeypatch):
    root = tmp_path / 'storage'
    manager = service(root)
    before = db_rows(root)
    transaction = Storage.transaction
    injected = []

    @contextmanager
    def broken(self, *, write=False):
        with transaction(self, write=write) as conn:
            yield conn
            count = conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
            if write and count > len(before['runs']):
                assert conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 2
                injected.append(True)
                raise sqlite3.OperationalError('injected admission commit failure')

    monkeypatch.setattr(Storage, 'transaction', broken)
    with pytest.raises(sqlite3.OperationalError):
        manager.submit(**upload_args())
    assert injected == [True] and db_rows(root) == before
    monkeypatch.setattr(Storage, 'transaction', transaction)
    accepted = manager.submit(**upload_args())
    assert accepted['status'] == 'queued' and len(RunStore(root).list_runs()) == 1


def test_real_child_builds_candidate_and_records_ordered_execution_events(tmp_path):
    root = tmp_path / 'storage'
    row = service(root).submit(**upload_args())
    with running_executor(root, compute_fn=controlled_compute):
        info = child_info(root, row['run_id'])
        assert info['pid'] != os.getpid() and info['ppid'] == os.getpid()
        saved = terminal(root, row['run_id'])
        wait_until(lambda: not alive(info['pid']), seconds=5)
    assert saved['status'] == 'completed' and saved['result']['status'] == 'built'
    runs = RunStore(root)
    events = runs.events(row['run_id'])
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    assert [e['payload']['status'] for e in events if e['kind'] == 'state'] == ['queued', 'running', 'completed']
    stages = [e['payload']['stage'] for e in events if e['kind'] == 'stage']
    assert stages == ['computing', 'committing'], 'Coarse truthful stages; no invented child percentages'
    assert runs.manifest(row['run_id'])['output_asset_ref'] == saved['output_asset_ref']
    assert AssetStore(root).get_version(**saved['output_asset_ref'])['publication_state'] == 'candidate'


def test_one_active_child_and_pending_capacity_release_on_completion(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root, max_pending=1)
    first = manager.submit(**upload_args('first'))
    control_path(root, first['run_id'], 'mode').write_text('block-error')
    with running_executor(root, compute_fn=controlled_compute):
        info = child_info(root, first['run_id'])
        second = manager.submit(**upload_args('second'))
        control_path(root, second['run_id'], 'mode').write_text('error')
        error = runtime_api()[0].BuildRequestError
        with pytest.raises(error) as caught:
            manager.submit(**upload_args('third'))
        assert caught.value.code == 'queue_full'
        assert RunStore(root).get_run(first['run_id'])['status'] == 'running'
        assert RunStore(root).get_run(second['run_id'])['status'] == 'queued'
        assert not control_path(root, second['run_id'], 'started').exists()
        assert alive(info['pid'])
        control_path(root, first['run_id'], 'release').touch()
        assert terminal(root, first['run_id'])['status'] == 'failed'
        assert terminal(root, second['run_id'])['status'] == 'failed'
        third = manager.submit(**upload_args('third'))
        assert third['run_id'] not in (first['run_id'], second['run_id'])


@pytest.mark.parametrize('mode,code', [('block', 'build_timeout'), ('crash', 'worker_crashed'),
                                     ('exception', 'worker_error'), ('bad-result', 'invalid_build_result')])
def test_timeout_crash_exception_or_invalid_result_cannot_leave_success_and_release_slot(tmp_path, mode, code):
    root = tmp_path / 'storage'
    manager = service(root)
    row = manager.submit(**upload_args('bad'))
    control_path(root, row['run_id'], 'mode').write_text(mode)
    with running_executor(root, timeout_seconds=1.5 if mode == 'block' else 30, compute_fn=controlled_compute):
        info = child_info(root, row['run_id'])
        saved = terminal(root, row['run_id'], seconds=10)
        assert saved['status'] == 'failed' and saved['output_asset_ref'] is None
        assert any(i['code'] == code for i in saved['result']['issues'])
        assert str(root) not in json.dumps(saved['result'])
        wait_until(lambda: not alive(info['pid']), seconds=5)
        # Releasing the former child gate cannot cause a late successful commit.
        before = deepcopy(RunStore(root).get_run(row['run_id']))
        control_path(root, row['run_id'], 'release').touch()
        following = manager.submit(**upload_args('after-failure', sources={
            **upload_args()['sources'], 'behavior_tree': {'filename': 'bad.xml', 'content': b'<broken>'}}))
        assert terminal(root, following['run_id'], seconds=10)['status'] == 'failed'
        assert RunStore(root).get_run(row['run_id']) == before
    assert sorted(a['kind'] for a in RunStore(root).artifacts(row['run_id'])) == ['manifest', 'validation']


def test_standby_does_not_recover_live_owner_and_takes_over_after_shutdown(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root)
    first = manager.submit(**upload_args())
    control_path(root, first['run_id'], 'mode').write_text('block-error')
    one, two = executor(root, compute_fn=controlled_compute), executor(root, compute_fn=controlled_compute)
    one.start()
    try:
        wait_until(lambda: one.is_owner)
        child_info(root, first['run_id'])
        before = RunStore(root).get_run(first['run_id'])
        two.start()
        # Repeated public reads and a competing scheduler must not interrupt the active host.
        time.sleep(0.15)
        assert not two.is_owner and RunStore(root).get_run(first['run_id']) == before
        one.stop()
        wait_until(lambda: two.is_owner, seconds=10)
        assert RunStore(root).get_run(first['run_id'])['status'] == 'interrupted'
        row = manager.submit(**upload_args('after-handover', sources={
            **upload_args()['sources'], 'behavior_tree': {'filename': 'bad.xml', 'content': b'<broken>'}}))
        assert terminal(root, row['run_id'])['status'] == 'failed'
    finally:
        one.stop()
        two.stop()


def test_real_owner_death_preserves_queue_and_recovers_only_build_runs(tmp_path):
    root, _, old_runs, unrelated, old_args = setup_run(tmp_path)
    other_queued = old_runs.create_run(**old_args)
    old_runs.start(unrelated['run_id'])
    unrelated_before = deepcopy(old_runs.get_run(unrelated['run_id']))
    manager = service(root)
    lost = manager.submit(**upload_args('lost'))
    control_path(root, lost['run_id'], 'mode').write_text('block-error')
    queued = manager.submit(**upload_args('queued'))
    control_path(root, queued['run_id'], 'mode').write_text('error')
    with owner_process(root) as owner:
        child_info(root, lost['run_id'])
        owner.kill()
        owner.wait(timeout=5)
        assert RunStore(root).get_run(lost['run_id'])['status'] == 'running'
        with running_executor(root, compute_fn=controlled_compute):
            wait_until(lambda: RunStore(root).get_run(lost['run_id'])['status'] == 'interrupted')
            assert terminal(root, queued['run_id'])['status'] == 'failed'
            assert RunStore(root).get_run(unrelated['run_id']) == unrelated_before
            assert RunStore(root).get_run(other_queued['run_id']) == other_queued
            before = deepcopy(RunStore(root).get_run(lost['run_id']))
            control_path(root, lost['run_id'], 'release').touch()
            time.sleep(0.1)
            assert RunStore(root).get_run(lost['run_id']) == before
    assert RunStore(root).artifacts(lost['run_id']) == []


def test_readers_and_restart_do_not_rerun_completed_records(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root)
    row = manager.submit(**upload_args())
    runs = RunStore(root)
    runs.start(row['run_id'])
    saved = runs.finish_build(row['run_id'], result=failure_result(), ontology=None)
    events, manifest = deepcopy(runs.events(row['run_id'])), deepcopy(runs.manifest(row['run_id']))
    with running_executor(root, compute_fn=controlled_compute):
        assert service(root).submit(**upload_args())['run_id'] == row['run_id']
        assert RunStore(root).get_run(row['run_id']) == saved
        assert RunStore(root).events(row['run_id']) == events
        assert not control_path(root, row['run_id'], 'started').exists()
    assert runs.manifest(row['run_id']) == manifest


def test_retry_is_durable_idempotent_and_keeps_original_snapshot(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root)
    row = manager.submit(**upload_args('original'))
    runs = RunStore(root)
    runs.start(row['run_id'])
    old = runs.finish_build(row['run_id'], result=failure_result(), ontology=None)
    before = deepcopy(runs.get_run(row['run_id'])), deepcopy(runs.events(row['run_id'])), runs.manifest(row['run_id'])
    retried = manager.retry(row['run_id'], request_id='retry-1')
    assert retried['run_id'] != row['run_id'] and retried['retry_of'] == row['run_id']
    assert retried['build_input'] == old['build_input'] and retried['components'] == old['components']
    assert service(root).retry(row['run_id'], request_id='retry-1')['run_id'] == retried['run_id']
    assert (runs.get_run(row['run_id']), runs.events(row['run_id']), runs.manifest(row['run_id'])) == before
    assert len(db_rows(root)['sources']) == 2


@pytest.mark.parametrize('state', ['queued', 'running', 'completed'])
def test_retry_rejects_non_retryable_state(tmp_path, state):
    root = tmp_path / 'storage'
    manager = service(root)
    row = manager.submit(**upload_args())
    runs = RunStore(root)
    if state == 'running':
        runs.start(row['run_id'])
    elif state == 'completed':
        # Use actual child/OWL; never forge a successful row in SQLite.
        with running_executor(root):
            assert terminal(root, row['run_id'])['status'] == 'completed'
    before = db_rows(root)
    with pytest.raises(runtime_api()[0].BuildRequestError) as caught:
        manager.retry(row['run_id'], request_id='retry')
    assert caught.value.status_code == 409
    assert db_rows(root) == before


def test_commit_failure_is_reported_without_success_and_executor_remains_available(tmp_path, monkeypatch):
    root = tmp_path / 'storage'
    manager = service(root)
    row = manager.submit(**upload_args('commit-fault'))
    original = RunStore.finish_build
    faults = []

    def once(self, run_id, *, result, ontology):
        if run_id == row['run_id'] and result['status'] == 'built' and not faults:
            faults.append(True)
            raise OSError('injected commit unavailable')
        return original(self, run_id, result=result, ontology=ontology)

    monkeypatch.setattr(RunStore, 'finish_build', once)
    with running_executor(root):
        saved = terminal(root, row['run_id'])
        assert faults == [True]
        assert saved['status'] == 'failed' and saved['output_asset_ref'] is None
        assert any(i['code'] == 'storage_error' for i in saved['result']['issues'])
        following = manager.submit(**upload_args('after-commit-fault', sources={
            **upload_args()['sources'], 'behavior_tree': {'filename': 'bad.xml', 'content': b'<broken>'}}))
        assert terminal(root, following['run_id'])['status'] == 'failed'
    assert AssetStore(root).list_assets() == []
    assert sorted(a['kind'] for a in RunStore(root).artifacts(row['run_id'])) == ['manifest', 'validation']


def test_independent_storage_roots_can_execute_while_another_is_blocked(tmp_path):
    first_root, second_root = tmp_path / 'one', tmp_path / 'two'
    first = service(first_root).submit(**upload_args())
    second = service(second_root).submit(**upload_args())
    control_path(first_root, first['run_id'], 'mode').write_text('block-error')
    control_path(second_root, second['run_id'], 'mode').write_text('error')
    with running_executor(first_root, compute_fn=controlled_compute):
        info = child_info(first_root, first['run_id'])
        with running_executor(second_root, compute_fn=controlled_compute):
            assert terminal(second_root, second['run_id'])['status'] == 'failed'
            assert RunStore(first_root).get_run(first['run_id'])['status'] == 'running'
            assert alive(info['pid'])
        control_path(first_root, first['run_id'], 'release').touch()
        assert terminal(first_root, first['run_id'])['status'] == 'failed'


def test_damaged_queued_source_fails_with_report_and_does_not_block_next_run(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root)
    row = manager.submit(**upload_args('damaged'))
    source = row['build_input']['sources'][0]
    record = AssetStore(root).get_source(source['source_asset_id'])
    (root / record['path']).write_bytes(b'changed after admission')
    following = manager.submit(**upload_args('following', sources={
        **upload_args()['sources'], 'behavior_tree': {'filename': 'bad.xml', 'content': b'<broken>'}}))
    with running_executor(root):
        saved = terminal(root, row['run_id'])
        assert saved['status'] == 'failed' and saved['output_asset_ref'] is None
        problem = next(i for i in saved['result']['issues'] if i['code'] == 'source_integrity')
        assert problem['source_asset_id'] == source['source_asset_id']
        assert problem['source_role'] == source['role']
        assert terminal(root, following['run_id'])['status'] == 'failed'
    assert saved['build_input'] == row['build_input']
    assert sorted(a['kind'] for a in RunStore(root).artifacts(row['run_id'])) == ['manifest', 'validation']


def test_interrupted_retry_obeys_capacity_and_reuses_only_its_own_request(tmp_path):
    root = tmp_path / 'storage'
    manager = service(root, max_pending=1)
    old = manager.submit(**upload_args('original'))
    runs = RunStore(root)
    runs.start(old['run_id'])
    runs.recover_interrupted()
    queued = manager.submit(**upload_args('pending'))
    before = db_rows(root)
    error = runtime_api()[0].BuildRequestError
    with pytest.raises(error) as caught:
        manager.retry(old['run_id'], request_id='retry')
    assert caught.value.code == 'queue_full' and db_rows(root) == before
    runs.start(queued['run_id'])
    runs.finish_build(queued['run_id'], result=failure_result(), ontology=None)
    retried = manager.retry(old['run_id'], request_id='retry')
    assert retried['status'] == 'queued' and retried['retry_of'] == old['run_id']
    assert retried['build_input'] == old['build_input']
    assert service(root, max_pending=1).retry(old['run_id'], request_id='retry')['run_id'] == retried['run_id']
    before = db_rows(root)
    with pytest.raises(error) as caught:
        manager.retry(queued['run_id'], request_id='retry')
    assert caught.value.code == 'request_conflict' and db_rows(root) == before
    assert runs.get_run(old['run_id'])['status'] == 'interrupted'

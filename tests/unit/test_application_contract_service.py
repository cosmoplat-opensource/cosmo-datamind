"""T04 RED: persisted reports, replay, transaction rollback and repair dispatch — DR-071."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from application_contracts import Registry
from tests.application_contract_support import ENTRYPOINT, contract, no_network, problem, result
from tests.application_contract_integration_support import (
    LocalResolver, expect_error, harness, repair_args, request_body, seeded, service_api,
)
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_design_support import restore_v5

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_integration_resolver_fixture_uses_real_bytes_and_t03_core(tmp_path):
    from application_contracts import evaluate
    from ontology_assets import AssetStore
    ref = seeded(tmp_path); resolver = LocalResolver(tmp_path)
    spec, _ = contract(); reg = Registry(); reg.register(spec)
    data = resolver.resolve(entrypoint=ENTRYPOINT, asset_refs=[ref], context={'mode': 'real'})
    assert data['snapshots'][0]['payload'] == AssetStore(tmp_path, read_only=True).read_payload(**ref)
    before = db_rows(tmp_path)
    report = evaluate(registry=reg, entrypoint=ENTRYPOINT, **data)
    assert report['preparation_status'] == 'available' and report['execution_status'] == 'not_evaluated'
    assert db_rows(tmp_path) == before


def test_report_and_idempotency_are_durable_without_changing_ontology(tmp_path):
    service, resolver, reg, checks, ref, _ = harness(tmp_path)
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    created = service.evaluate(**request_body(ref))
    report = created['report']
    assert not created['replayed'] and created['freshness'] == 'current'
    assert report['check_id'] and report['checked_at'] and report['schema_version'] == 1
    assert report['preparation_status'] == 'available' and report['execution_status'] == 'not_evaluated'
    assert report['execution_readiness'] == 'not_verified'
    after = db_rows(tmp_path)
    assert all(after[t] == rows for t, rows in before.items() if not t.startswith('application_')) and blob_bytes(tmp_path) == files
    assert len(after['application_checks']) == len(after['application_check_requests']) == 1
    restarted = service_api().CheckService(tmp_path, registry=reg, resolver=resolver)
    calls = sum(len(c.calls) for c in checks.values())
    replay = restarted.evaluate(**request_body(ref))
    assert replay['replayed'] and replay['report'] == report
    assert sum(len(c.calls) for c in checks.values()) == calls and db_rows(tmp_path) == after


def test_independent_process_reads_history_without_rechecking(tmp_path):
    service, _, _, _, ref, _ = harness(tmp_path)
    report = service.evaluate(**request_body(ref))['report']
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    script = '''
import json, sys
from application_contracts import Registry
from application_contracts.service import CheckService
class LocalOnly:
    def peek(self, report): return None
    def resolve(self, **kwargs): raise AssertionError('history must not execute checks')
service = CheckService(sys.argv[1], registry=Registry(), resolver=LocalOnly(), read_only=True)
print(json.dumps(service.read(sys.argv[2])))
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path), report['check_id']],
        cwd=Path(__file__).resolve().parents[2], env=dict(os.environ), capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    read = json.loads(child.stdout)
    assert read == {'report': report, 'freshness': 'unknown'}
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files


@pytest.mark.parametrize('change', ['task', 'environment', 'lifecycle', 'unknown'])
def test_read_and_replay_keep_historical_conclusion_and_expose_freshness(tmp_path, change):
    service, resolver, _, checks, ref, _ = harness(tmp_path)
    body = request_body(ref, context={'mode': 'real', 'task_ref': {'session_id': 'task-1'}})
    report = service.evaluate(**body)['report']
    assert report['execution_status'] == 'ready'
    before, calls = db_rows(tmp_path), sum(len(c.calls) for c in checks.values())
    if change == 'task': resolver.data['task']['revision'] += 1
    elif change == 'environment': resolver.data['environment']['revision'] = 'env-2'
    elif change == 'lifecycle': resolver.data['snapshots'][0]['lifecycle_revision'] += 1
    else: resolver.unknown = True
    expected = 'unknown' if change == 'unknown' else 'stale'
    assert service.read(report['check_id']) == {'report': report, 'freshness': expected}
    replay = service.evaluate(**body)
    assert replay['report'] == report and replay['freshness'] == expected and replay['replayed']
    assert db_rows(tmp_path) == before and sum(len(c.calls) for c in checks.values()) == calls


def test_request_identity_conflict_and_explicit_new_check(tmp_path):
    service, _, _, checks, ref, _ = harness(tmp_path)
    report = service.evaluate(**request_body(ref))['report']
    before = db_rows(tmp_path)
    expect_error('request_conflict', lambda: service.evaluate(**request_body(ref, context={'mode': 'demo'})))
    assert db_rows(tmp_path) == before
    new = service.evaluate(**request_body(ref, request_id='check-2'))['report']
    assert new['check_id'] != report['check_id'] and len(checks['schema'].calls) == 2
    assert service.read(report['check_id'])['report'] == report


def test_concurrent_duplicate_checks_publish_one_report(tmp_path):
    _, resolver, reg, _, ref, _ = harness(tmp_path)
    barrier = Barrier(3)
    def submit(_):
        service = service_api().CheckService(tmp_path, registry=reg, resolver=resolver)
        barrier.wait(timeout=10)
        return service.evaluate(**request_body(ref))
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(submit, range(3)))
    assert len({r['report']['check_id'] for r in replies}) == 1
    assert sum(not r['replayed'] for r in replies) == 1
    rows = db_rows(tmp_path)
    assert len(rows['application_checks']) == len(rows['application_check_requests']) == 1


def test_failed_idempotency_insert_rolls_back_report_in_same_transaction(tmp_path):
    service, _, _, _, ref, _ = harness(tmp_path)
    with sqlite3.connect(tmp_path / 'domain.sqlite3') as conn:
        conn.execute("CREATE TRIGGER reject_check_request BEFORE INSERT ON application_check_requests "
                     "BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END")
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    expect_error('storage_unavailable', lambda: service.evaluate(**request_body(ref)))
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files
    with sqlite3.connect(tmp_path / 'domain.sqlite3') as conn:
        conn.execute('DROP TRIGGER reject_check_request')
    assert not service.evaluate(**request_body(ref))['replayed']


def test_input_changed_during_check_cannot_be_saved_as_current(tmp_path):
    service, resolver, reg, _, ref, _ = harness(tmp_path)
    spec, _ = contract()
    def changing_check(data):
        resolver.data['environment']['revision'] = 'changed-during-check'
        return [result('schema')]
    spec['checkers'][0]['check'] = changing_check
    reg = Registry(); reg.register(spec)
    service = service_api().CheckService(tmp_path, registry=reg, resolver=resolver)
    before = db_rows(tmp_path)
    expect_error('stale_result', lambda: service.evaluate(**request_body(ref)))
    assert db_rows(tmp_path) == before


@pytest.mark.parametrize('previous', ['ready', 'other_application', 'no_report'])
def test_service_execution_guard_rereads_current_facts_not_stored_permission(tmp_path, previous):
    service, resolver, _, checks, ref, _ = harness(tmp_path)
    context = {'mode': 'real', 'task_ref': {'session_id': 'task-1'}}
    old = service.evaluate(**request_body(ref, context=context))['report']
    assert old['execution_status'] == 'ready'
    if previous == 'other_application': old = {**old, 'application_id': 'data.deep_qa'}
    elif previous == 'no_report': old = None
    resolver.data['environment']['revision'] = 'revoked-environment'
    checks['execution'].response = [result('execution', 'missing', issues=[problem()])]
    calls = len(resolver.calls), len(checks['execution'].calls)
    expect_error('application_not_ready', lambda: service.require_ready(entrypoint=ENTRYPOINT,
        asset_refs=[ref], context=context, previous_report=old))
    assert len(resolver.calls) > calls[0] and len(checks['execution'].calls) > calls[1]


def test_read_only_missing_root_does_not_initialize_storage(tmp_path):
    root = tmp_path / 'absent'
    reader = service_api().CheckService(root, registry=Registry(), resolver=LocalResolver(root), read_only=True)
    expect_error('not_found', lambda: reader.read('missing'))
    assert not root.exists()


def test_old_database_reads_do_not_migrate_and_writer_preserves_all_old_rows(tmp_path):
    root = tmp_path / 'restored'; restore_v5(root)
    before, files = db_rows(root), blob_bytes(root)
    def read_old():
        reader = service_api().CheckService(root, registry=Registry(), resolver=LocalResolver(root), read_only=True)
        reader.read('missing')
    expect_error('storage_unavailable', read_old)
    assert db_rows(root) == before and blob_bytes(root) == files
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 5
    service_api().CheckService(root, registry=Registry(), resolver=LocalResolver(root))
    after = db_rows(root)
    assert all(len(after[t]) == len(rows) and all(
        all(new[k] == value for k, value in old.items())
        for old, new in zip(rows, after[t])) for t, rows in before.items())
    assert blob_bytes(root) == files
    assert {'application_checks', 'application_check_requests', 'application_repair_requests'} <= set(after)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('kind', ['navigate', 'submit'])
def test_repair_dispatch_is_bound_to_issue_resource_and_original_task(tmp_path, kind):
    service, _, _, _, ref, handler = harness(tmp_path, repair=True, kind=kind)
    report = service.evaluate(**request_body(ref, context={'mode': 'real', 'task_ref': {'session_id': 'task-1'}}))['report']
    response = service.repair(**repair_args(report))
    assert response['kind'] == kind and not response['replayed']
    command = handler.calls[0]
    assert command['target'] == {'resource_kind': 'design_session', 'resource_id': 'design-1', 'expected_revision': 3}
    assert command['return_context']['task_ref'] == report['task_ref']
    assert command['return_context']['asset_refs'] == [ref]
    assert response['return_context'] == command['return_context']
    before = db_rows(tmp_path)
    replay = service.repair(**repair_args(report))
    assert replay == {**response, 'replayed': True} and len(handler.calls) == 1
    assert db_rows(tmp_path) == before and len(before['test_repair_effects']) == 1


@pytest.mark.parametrize('change,code', [('revision', 'stale_result'), ('stale', 'stale_result'),
    ('wrong_issue', 'unknown_action'), ('wrong_action', 'unknown_action'), ('unavailable', 'repair_unavailable')])
def test_stale_or_unbound_repairs_have_no_effect(tmp_path, change, code):
    service, resolver, _, _, ref, handler = harness(tmp_path, repair=change != 'unavailable',
        overrides={'execution': [result('execution', 'missing', issues=[problem()])]})
    report = service.evaluate(**request_body(ref))['report']
    args = repair_args(report)
    if change == 'revision': resolver.target_revision = 4
    elif change == 'stale': resolver.data['environment']['revision'] = 'env-2'
    elif change == 'wrong_issue': args['issue_id'] = 'another-issue'
    elif change == 'wrong_action': args['action_id'] = 'arbitrary.module:execute'
    before = db_rows(tmp_path)
    expect_error(code, lambda: service.repair(**args))
    assert db_rows(tmp_path) == before and handler.calls == []


def test_repair_request_conflict_does_not_repeat_side_effect(tmp_path):
    service, _, _, _, ref, handler = harness(tmp_path, repair=True)
    report = service.evaluate(**request_body(ref))['report']
    service.repair(**repair_args(report))
    before = db_rows(tmp_path)
    expect_error('request_conflict', lambda: service.repair(**repair_args(report, input={'changed': True})))
    assert db_rows(tmp_path) == before and len(handler.calls) == 1


def test_concurrent_repairs_execute_one_transactional_effect(tmp_path):
    service, resolver, reg, _, ref, handler = harness(tmp_path, repair=True)
    report = service.evaluate(**request_body(ref))['report']
    barrier = Barrier(3)
    def submit(_):
        local = service_api().CheckService(tmp_path, registry=reg, resolver=resolver)
        barrier.wait(timeout=10)
        return local.repair(**repair_args(report))
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(submit, range(3)))
    assert sum(not r['replayed'] for r in replies) == 1 and len(handler.calls) == 1
    assert len(db_rows(tmp_path)['test_repair_effects']) == 1


def test_repair_failure_rolls_back_local_effect_and_does_not_poison_retry(tmp_path):
    service, _, _, _, ref, handler = harness(tmp_path, repair=True, fail_repair=True)
    report = service.evaluate(**request_body(ref))['report']
    before = db_rows(tmp_path)
    expect_error('storage_unavailable', lambda: service.repair(**repair_args(report)))
    assert db_rows(tmp_path) == before
    handler.fail = False
    assert not service.repair(**repair_args(report))['replayed']
    assert len(db_rows(tmp_path)['test_repair_effects']) == 1

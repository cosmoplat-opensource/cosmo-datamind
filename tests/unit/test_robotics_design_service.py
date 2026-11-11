"""Admission, revision identity and durable failure boundaries — DR-070 / T06 RED."""
from copy import deepcopy
import json

import pytest

from domain_runs import RunStore
from domain_runs.build_service import BuildService
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_design_support import (
    Harness, assert_no_candidate, expect_error, history_bytes, inputs, no_network, proposals, reply, service_api,
)
from tests.robotics_dual_build_support import TEST_KEY

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_missing_description_persists_sources_without_run_or_model(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.submit()
    assert state['method'] == 'llm_tbox_abox'
    assert state['status'] == 'needs_description' and state['stage'] == 'classes'
    assert state['revision'] == 0 and state['run_id'] is None
    assert state['session_ref'] == {'namespace': 'robotics_design', 'id': state['session_id']}
    assert state['model'] == h.resolve().public()['model']
    assert h.runtime.calls == [] and RunStore(h.root).list_runs() == []
    assert len(db_rows(h.root)['sources']) == 2
    assert h.get(state) == state
    assert_no_candidate(h)


def test_public_build_service_routes_model_submission_to_design(tmp_path):
    h = Harness(tmp_path / 'store')
    state = BuildService(h.root).submit(**inputs(description='整理调用参数'), route_resolver=h.resolve)
    assert state['method'] == 'llm_tbox_abox' and state['stage'] == 'classes'
    assert state['revision'] == 0 and state['run_id']
    run = RunStore(h.root).get_run(state['run_id'])
    assert run['operation'] == 'design_tbox' and run['session_ref'] == state['session_ref']
    assert run['ontology_id'] is None and run['version_id'] is None and run['build_input'] is None
    assert run['design_input']['source_sha256'] == h.get(state)['source_sha256']
    assert h.runtime.calls == []


def test_first_message_after_missing_description_and_each_turn_are_separate_runs(tmp_path):
    h = Harness(tmp_path / 'store')
    empty = h.submit()
    h.runtime.responses.append(reply(proposals()[0]))
    queued = h.message(empty)
    assert queued['revision'] == 0 and queued['status'] == 'queued'
    h.run()
    first = h.get(empty)
    assert first['revision'] == 1 and first['status'] == 'awaiting_confirmation'
    assert first['stage'] == 'classes' and first['class_confirmation'] is None
    assert h.service.claim_next() is None, 'Waiting for user consumes no worker slot'
    h.runtime.responses.append(reply(proposals()[0]))
    second = h.message(first)
    assert second['run_id'] != queued['run_id'] and second['revision'] == 1
    h.run()
    current = h.get(first)
    assert current['revision'] == 2 and current['stage'] == 'classes'
    assert len(h.runtime.calls) == 2
    assert len(RunStore(h.root).list_runs()) == 2


def test_admission_replay_is_frozen_even_after_config_change(tmp_path):
    h = Harness(tmp_path / 'store')
    accepted = h.submit(description='整理调用参数')
    h.runtime.responses.append(reply(proposals()[0]))
    h.run()
    before, files = db_rows(h.root), blob_bytes(h.root)
    h.config = {'llm': {'base': 'https://broken.invalid/v1'}}
    assert h.submit(description='整理调用参数') == accepted
    assert db_rows(h.root) == before and blob_bytes(h.root) == files
    assert len(h.runtime.calls) == 1


@pytest.mark.parametrize('changed', ['name', 'description', 'behavior_tree', 'node_models'])
def test_admission_request_identity_rejects_changed_input(tmp_path, changed):
    h = Harness(tmp_path / 'store')
    h.submit()
    altered = inputs()
    if changed in altered:
        altered[changed] = 'changed'
    else:
        altered['sources'][changed]['content'] += b'\n'
    before, files = db_rows(h.root), blob_bytes(h.root)
    expect_error('request_conflict', lambda: h.service.submit(**altered), 409)
    assert db_rows(h.root) == before and blob_bytes(h.root) == files and not h.runtime.calls


@pytest.mark.parametrize('bad', [-1, True, '0', None])
def test_revision_type_is_strict_and_rejection_is_side_effect_free(tmp_path, bad):
    h = Harness(tmp_path / 'store')
    state = h.submit()
    before = db_rows(h.root)
    expect_error('invalid_request', lambda: h.service.message(state['session_id'], text='要求',
        expected_revision=bad, expected_stage='classes', request_id='bad'), 400)
    assert db_rows(h.root) == before and not h.runtime.calls


def test_stale_revision_stage_and_snapshot_cannot_confirm_or_enqueue(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    cases = [
        ('revision_conflict', lambda: h.service.message(state['session_id'], text='要求',
            expected_revision=0, expected_stage='classes', request_id='stale-revision')),
        ('stage_conflict', lambda: h.service.message(state['session_id'], text='要求',
            expected_revision=1, expected_stage='properties', request_id='wrong-stage')),
        ('snapshot_conflict', lambda: h.service.confirm_classes(state['session_id'],
            expected_revision=1, snapshot_sha256='0' * 64, request_id='stale-snapshot')),
    ]
    before, files = db_rows(h.root), blob_bytes(h.root)
    for code, action in cases:
        expect_error(code, action, 409)
        assert db_rows(h.root) == before and blob_bytes(h.root) == files
    assert len(h.runtime.calls) == 1


def test_confirmation_and_edit_preserve_history_and_never_call_model(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.properties()
    before = history_bytes(h, state)
    calls = deepcopy(h.runtime.calls)
    assert state['stage'] == 'properties' and state['class_confirmation']
    edited = h.service.edit_classes(state['session_id'], request_id='edit', expected_revision=state['revision'])
    assert edited['revision'] == state['revision'] + 1 and edited['stage'] == 'classes'
    assert edited['class_confirmation'] is None and edited['schema_confirmation'] is None
    assert 'object_properties' not in edited['result']
    assert h.runtime.calls == calls
    assert all(history_bytes(h, edited)[revision] == raw for revision, raw in before.items())
    old = h.service.get_revision(state['session_id'], state['revision'])
    assert old['state']['class_confirmation'] == state['class_confirmation']
    assert old['state']['result']['object_properties'] == state['result']['object_properties']


def test_operation_replay_returns_original_receipt_and_cross_operation_conflicts(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    args = {'request_id': 'confirm', 'expected_revision': state['revision'],
            'snapshot_sha256': state['snapshot_sha256']}
    confirmed = h.service.confirm_classes(state['session_id'], **args)
    h.service.edit_classes(state['session_id'], request_id='edit', expected_revision=confirmed['revision'])
    before, files = db_rows(h.root), blob_bytes(h.root)
    assert h.service.confirm_classes(state['session_id'], **args) == confirmed
    expect_error('request_conflict', lambda: h.service.edit_classes(state['session_id'],
        request_id='confirm', expected_revision=state['revision']), 409)
    assert db_rows(h.root) == before and blob_bytes(h.root) == files


@pytest.mark.parametrize('failure,code,calls', [
    (TimeoutError('synthetic timeout'), 'model_request_failed', 1),
    ((False, '401 ' + TEST_KEY), 'model_request_failed', 1),
    ((False, '429'), 'model_request_failed', 1),
    ((True, '{broken'), 'model_format', 2),
])
def test_model_failure_preserves_revision_without_fallback(tmp_path, failure, code, calls):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    before = history_bytes(h, state)
    h.runtime.responses.extend([failure] * calls)
    receipt = h.message(state)
    run = h.run()
    assert run['status'] == 'failed' and run['result']['error']['code'] == code
    current = h.get(state)
    assert current['revision'] == state['revision'] and current['result'] == state['result']
    assert current['snapshot_sha256'] == state['snapshot_sha256']
    assert current['method'] == 'llm_tbox_abox' and current['status'] == 'failed'
    assert history_bytes(h, state) == before
    assert len(h.runtime.calls) == 1 + calls
    assert {r['operation'] for r in RunStore(h.root).list_runs()} == {'design_tbox'}
    assert_no_candidate(h)
    assert h.message(state) == receipt, 'Replaying a failed request must not call the model again'
    serialized = json.dumps(db_rows(h.root), ensure_ascii=False).encode() + b''.join(blob_bytes(h.root).values())
    assert TEST_KEY.encode() not in serialized


def test_semantically_invalid_model_reply_is_not_repaired_or_committed(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    before = history_bytes(h, state)
    invalid = proposals()[0]
    invalid['classes'][0]['parent_ids'] = [invalid['classes'][0]['id']]
    h.runtime.responses.append(reply(invalid))
    h.message(state)
    assert h.run()['status'] == 'failed'
    assert len(h.runtime.calls) == 2 and history_bytes(h, state) == before
    assert_no_candidate(h)


def test_configuration_change_cannot_switch_session_method(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    before = db_rows(h.root)
    h.config['llm']['model'] = h.runtime.model = 'other-model'
    expect_error('configuration_changed', lambda: h.message(state), 409)
    assert db_rows(h.root) == before and len(h.runtime.calls) == 1
    # Recovery of the exact provider/model/endpoint permits a new operation.
    h.config['llm']['model'] = h.runtime.model = state['model']['model']
    assert h.message(state)['status'] == 'queued'


def test_config_changed_after_enqueue_fails_before_transport(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.submit(description='要求')
    h.config['llm']['model'] = h.runtime.model = 'different'
    run = h.run()
    assert run['status'] == 'failed' and run['result']['error']['code'] == 'configuration_changed'
    assert h.get(state)['revision'] == 0 and h.runtime.calls == []
    assert_no_candidate(h)


def test_retry_remains_pinned_and_format_repair_is_bounded(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    h.runtime.responses.append(TimeoutError('timeout'))
    h.message(state)
    failed = h.run()
    h.config['llm']['key'] = h.runtime.key = 'rotated-synthetic-key'
    args = {'request_id': 'retry', 'expected_revision': state['revision'], 'failed_run_id': failed['run_id']}
    retried = h.service.retry(state['session_id'], **args)
    assert retried['run_id'] != failed['run_id']
    assert RunStore(h.root).get_run(retried['run_id'])['retry_of'] == failed['run_id']
    h.runtime.responses.extend([(True, '{broken'), reply(proposals()[0])])
    done = h.run()
    assert done['status'] == 'completed'
    assert h.get(state)['revision'] == state['revision'] + 1
    assert h.service.retry(state['session_id'], **args) == retried
    assert len(h.runtime.calls) == 4
    assert h.get(state)['model'] == state['model']


def test_retry_rejects_other_session_success_and_stale_revision(tmp_path):
    h = Harness(tmp_path / 'store')
    first = h.classes()
    other = h.submit(request_id='other', description='要求')
    h.runtime.responses.append(TimeoutError('timeout'))
    failed = h.run()
    before = db_rows(h.root)
    for run_id, revision in [(failed['run_id'], first['revision']),
                             (first['run_id'], first['revision']),
                             (failed['run_id'], first['revision'] - 1)]:
        expect_error('retry_conflict', lambda rid=run_id, rev=revision: h.service.retry(first['session_id'],
            request_id='invalid-retry', expected_revision=rev, failed_run_id=rid), 409)
    assert db_rows(h.root) == before and h.get(other)['revision'] == 0


def test_secrets_never_enter_persisted_state_manifest_exports_or_prompts(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    payload = json.dumps(db_rows(h.root), ensure_ascii=False).encode() + b''.join(blob_bytes(h.root).values())
    assert TEST_KEY.encode() not in payload and b'https://model.invalid' not in payload
    assert all(TEST_KEY not in c['message'] for c in h.runtime.calls)
    assert all(str(h.root) not in c['message'] for c in h.runtime.calls)
    assert state['model'] == h.resolve().public()['model']
    for run in RunStore(h.root).list_runs():
        assert run['components']['prompt_version']
        assert run['design_input']['vocabulary_sha256']
        assert run['design_input']['source_sha256'] == state['source_sha256']
        assert RunStore(h.root).manifest(run['run_id'])['design_input'] == run['design_input']


def test_read_only_service_does_not_create_missing_root(tmp_path):
    root = tmp_path / 'missing'
    with pytest.raises(ValueError):
        service_api().DesignService(root, read_only=True)
    assert not root.exists()


def test_schema_confirmation_rejects_missing_properties_and_stale_digest(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    confirmed = h.service.confirm_classes(state['session_id'], request_id='confirm-classes',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    before = db_rows(h.root)
    expect_error('confirmation_required', lambda: h.service.confirm_schema(state['session_id'],
        request_id='too-early', expected_revision=confirmed['revision'],
        snapshot_sha256=confirmed['snapshot_sha256']), 409)
    assert db_rows(h.root) == before
    h.runtime.responses.append(reply(proposals()[1]))
    h.message(confirmed)
    h.run()
    current = h.get(state)
    before = db_rows(h.root)
    expect_error('snapshot_conflict', lambda: h.service.confirm_schema(state['session_id'],
        request_id='stale', expected_revision=current['revision'], snapshot_sha256=state['snapshot_sha256']), 409)
    assert db_rows(h.root) == before and len(h.runtime.calls) == 2


def test_session_scopes_isolate_request_ids_and_revision_exports(tmp_path):
    h = Harness(tmp_path / 'store')
    first = h.classes()
    second = h.submit(request_id='second')
    h.runtime.responses.append(reply(proposals()[0]))
    h.message(second)
    h.run()
    second = h.get(second)
    for state in (first, second):
        h.service.confirm_classes(state['session_id'], request_id='same-local-request',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    assert h.get(first)['revision'] == h.get(second)['revision'] == 2
    assert h.service.get_revision(first['session_id'], 1)['session_ref'] == first['session_ref']
    assert h.service.get_revision(second['session_id'], 1)['session_ref'] == second['session_ref']
    before = db_rows(h.root)
    expect_error('not_found', lambda: h.service.get('missing-session'), 404)
    expect_error('not_found', lambda: h.service.get_revision(first['session_id'], 99), 404)
    assert db_rows(h.root) == before


@pytest.mark.parametrize('format', ['../sources', 'xml', '', None])
def test_revision_export_rejects_unknown_format_without_filesystem_effects(tmp_path, format):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    before, files = db_rows(h.root), blob_bytes(h.root)
    expect_error('invalid_request', lambda: h.service.export_revision(state['session_id'], 1, format=format), 400)
    assert db_rows(h.root) == before and blob_bytes(h.root) == files

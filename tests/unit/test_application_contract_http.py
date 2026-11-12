"""T04 RED: real Blueprint/guards/storage with synthetic application facts — DR-071."""
from copy import deepcopy
import json
import sqlite3

import pytest

from tests.application_contract_support import APP, ENTRYPOINT, no_network, problem, result
from tests.application_contract_integration_support import (
    app_for, error, harness, ok, post, repair_args, request_body,
)
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_static_application_listing_and_missing_history_are_readonly(tmp_path):
    root = tmp_path / 'absent'
    _, client = app_for(root)
    entries = ok(client.get('/api/applications', base_url=BASE_URL))['items']
    robotics = next(r for r in entries if r['application_id'] == APP)
    qa = next(r for r in entries if r['application_id'] == 'data.deep_qa')
    assert robotics['entrypoints'] == [ENTRYPOINT] and robotics['contract_version'] == '1'
    assert qa['availability'] == 'unavailable'
    assert 'checkers' not in robotics and 'repair_handlers' not in robotics
    error(client.get('/api/application-checks/missing', base_url=BASE_URL), 404, 'not_found')
    assert not root.exists()


def test_explicit_post_persists_report_replays_and_reads_without_rechecking(tmp_path):
    _, resolver, reg, checks, ref, _ = harness(tmp_path)
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    body = request_body(ref)
    created = ok(post(client, body), 201)
    report = created['report']
    assert report['application_id'] == APP and report['asset_refs'] == [ref]
    assert report['context'] == {'mode': 'real', 'generation_policy': 'offline'}
    assert report['task_ref'] is None and report['execution_status'] == 'not_evaluated'
    before, calls = db_rows(tmp_path), sum(len(c.calls) for c in checks.values())
    read = ok(client.get('/api/application-checks/' + report['check_id'], base_url=BASE_URL))
    assert read == {'report': report, 'freshness': 'current'}
    assert ok(post(client, dict(reversed(list(body.items()))))) == {**created, 'replayed': True}
    assert db_rows(tmp_path) == before and sum(len(c.calls) for c in checks.values()) == calls
    error(post(client, {**body, 'context': {'mode': 'demo'}}), 409, 'request_conflict')


INVALID = ['empty_refs', 'duplicate_refs', 'latest', 'unknown_entrypoint', 'extra_top', 'extra_ref',
    'ready', 'task_hash', 'task_content', 'task_revision', 'generation_policy', 'environment_revision',
    'contract_version', 'payload', 'path', 'executor', 'long_id', 'bool_mode', 'array', 'missing_request']


@pytest.mark.parametrize('case', INVALID)
def test_http_rejects_untrusted_input_before_check_or_write(tmp_path, case):
    _, resolver, reg, checks, ref, _ = harness(tmp_path)
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    body = deepcopy(request_body(ref)); code = 'invalid_request'
    if case == 'empty_refs': body['asset_refs'] = []
    elif case == 'duplicate_refs': body['asset_refs'] *= 2
    elif case == 'latest': body['asset_refs'][0]['version_id'] = 'latest'
    elif case == 'unknown_entrypoint': body['entrypoint'] = 'unregistered.run'; code = 'unknown_entrypoint'
    elif case == 'extra_top': body['application_id'] = 'data.deep_qa'
    elif case == 'extra_ref': body['asset_refs'][0]['payload_sha256'] = 'forged'
    elif case == 'task_content': body['context']['task'] = {'operation': 'anything'}
    elif case == 'task_revision': body['context']['task_ref'] = {'session_id': 'task-1', 'revision': 0}
    elif case in ('generation_policy', 'task_hash', 'environment_revision'):
        body['context'][case] = 'offline' if case == 'generation_policy' else 'forged'
    elif case == 'long_id': body['request_id'] = 'x' * 129
    elif case == 'bool_mode': body['context']['mode'] = True
    elif case == 'array': body = []
    elif case == 'missing_request': body.pop('request_id')
    else: body[case] = True if case == 'ready' else 'untrusted'
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    response = post(client, body)
    if case == 'array':
        # Existing host guard uses its legacy string error, before Blueprint validation.
        assert response.status_code == 400 and response.is_json and response.json['error']
    else:
        error(response, 400, code)
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files
    assert resolver.calls == [] and not any(c.calls for c in checks.values())


@pytest.mark.parametrize('case,status', [('origin', 403), ('host', 400), ('malformed', 400),
    ('content_type', 415), ('oversize', 413)])
def test_application_routes_reuse_platform_transport_guards(tmp_path, case, status):
    _, resolver, reg, checks, ref, _ = harness(tmp_path)
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    before = db_rows(tmp_path)
    options = {'base_url': BASE_URL}
    if case in ('host', 'origin'):
        options.update(json=request_body(ref), headers={
            'Host' if case == 'host' else 'Origin': 'bad.invalid' if case == 'host' else 'https://bad.invalid'})
    else:
        options.update(data='{' if case == 'malformed' else json.dumps({'padding': 'x' * 5000}),
            content_type='text/plain' if case == 'content_type' else 'application/json')
    response = client.post('/api/application-checks', **options)
    assert response.status_code == status, response.get_data(as_text=True)
    assert db_rows(tmp_path) == before and resolver.calls == [] and not any(c.calls for c in checks.values())


@pytest.mark.parametrize('case', ['missing_asset', 'cross_asset_version', 'missing_task'])
def test_missing_or_mismatched_server_references_are_404_without_report(tmp_path, case):
    from tests.application_contract_integration_support import seeded
    _, resolver, reg, checks, ref, _ = harness(tmp_path)
    body = request_body(ref)
    if case == 'missing_asset': body['asset_refs'] = [{'ontology_id': 'missing', 'version_id': 'missing'}]
    elif case == 'cross_asset_version': body['asset_refs'] = [{**ref, 'version_id': seeded(tmp_path)['version_id']}]
    else: body['context']['task_ref'] = {'session_id': 'missing'}
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    before = db_rows(tmp_path)
    error(post(client, body), 404, 'not_found')
    assert db_rows(tmp_path) == before and not any(c.calls for c in checks.values())


@pytest.mark.parametrize('state', ['missing', 'unsupported', 'invalid', 'error'])
def test_valid_diagnostic_request_saves_blocked_report_instead_of_transport_failure(tmp_path, state):
    observation = RuntimeError('synthetic-private-secret') if state == 'error' else [
        result('execution', state, issues=[problem()])]
    _, resolver, reg, _, ref, _ = harness(tmp_path, overrides={'execution': observation})
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    reply = ok(post(client, request_body(ref)), 201)
    report = reply['report']
    assert report['preparation_status'] == 'blocked' and report['execution_status'] != 'ready'
    assert report['layers']['execution']['checks'][0]['status'] == state
    assert report['issues'] and all(a['availability'] == 'unavailable'
        for i in report['issues'] for a in i['repair_actions'])
    assert str(tmp_path) not in json.dumps(reply) and 'synthetic-private-secret' not in json.dumps(reply)
    assert ok(client.get('/api/application-checks/' + report['check_id'], base_url=BASE_URL))['report'] == report


def test_disabled_service_and_unavailable_application_do_not_return_ready(tmp_path):
    root = tmp_path / 'absent'
    _, client = app_for(root, enabled=False)
    error(post(client, request_body({'ontology_id': 'missing', 'version_id': 'missing'})),
        503, 'application_unavailable')
    assert not root.exists()
    _, client = app_for(root, enabled=True)
    error(post(client, request_body({'ontology_id': 'missing', 'version_id': 'missing'}, entrypoint='data.ask')),
        503, 'application_unavailable')
    assert not root.exists()


def test_storage_failure_is_503_without_fake_report_or_partial_commit(tmp_path):
    _, resolver, reg, _, ref, _ = harness(tmp_path)
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    with sqlite3.connect(tmp_path / 'domain.sqlite3') as conn:
        conn.execute("CREATE TRIGGER no_report BEFORE INSERT ON application_checks "
            "BEGIN SELECT RAISE(ABORT, 'private database path'); END")
    before = db_rows(tmp_path)
    body = error(post(client, request_body(ref)), 503, 'storage_unavailable')
    assert 'check_id' not in json.dumps(body) and 'private database path' not in json.dumps(body)
    assert db_rows(tmp_path) == before


@pytest.mark.parametrize('kind,status', [('navigate', 200), ('submit', 202)])
def test_repair_http_get_has_no_effect_and_post_replays_once(tmp_path, kind, status):
    service, resolver, reg, _, ref, handler = harness(tmp_path, repair=True, kind=kind)
    report = service.evaluate(**request_body(ref))['report']
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    path = '/api/application-checks/' + report['check_id'] + '/repairs'
    assert client.get(path, base_url=BASE_URL).status_code == 405
    assert handler.calls == []
    body = repair_args(report); body.pop('check_id')
    first = ok(post(client, body, path), status)
    replay = ok(post(client, body, path), status)
    assert replay == {**first, 'replayed': True} and len(handler.calls) == 1


@pytest.mark.parametrize('revision', [True, -1, 1.5, '3', None])
def test_repair_revision_is_strict_integer_and_has_no_effect(tmp_path, revision):
    service, resolver, reg, _, ref, handler = harness(tmp_path, repair=True)
    report = service.evaluate(**request_body(ref))['report']
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    body = repair_args(report, expected_revision=revision); body.pop('check_id')
    before = db_rows(tmp_path)
    error(post(client, body, '/api/application-checks/' + report['check_id'] + '/repairs'), 400, 'invalid_request')
    assert db_rows(tmp_path) == before and handler.calls == []


def test_repair_http_cannot_override_target_or_inject_handler(tmp_path):
    service, resolver, reg, _, ref, handler = harness(tmp_path, repair=True)
    report = service.evaluate(**request_body(ref))['report']
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    body = repair_args(report); body.pop('check_id')
    before = db_rows(tmp_path)
    for field in ('target', 'handler', 'return_context'):
        error(post(client, {**body, field: 'forged'},
            '/api/application-checks/' + report['check_id'] + '/repairs'), 400, 'invalid_request')
    assert db_rows(tmp_path) == before and handler.calls == []


@pytest.mark.parametrize('case,status,code', [('stale', 409, 'stale_result'),
    ('revision', 409, 'stale_result'), ('unknown_action', 400, 'unknown_action'),
    ('unavailable', 409, 'repair_unavailable'), ('missing_report', 404, 'not_found')])
def test_repair_http_errors_preserve_clear_codes_and_no_effect(tmp_path, case, status, code):
    service, resolver, reg, _, ref, handler = harness(tmp_path, repair=case != 'unavailable',
        overrides={'execution': [result('execution', 'missing', issues=[problem()])]})
    report = service.evaluate(**request_body(ref))['report']
    _, client = app_for(tmp_path, registry=reg, resolver=resolver)
    body = repair_args(report); body.pop('check_id')
    cid = report['check_id']
    if case == 'stale': resolver.data['environment']['revision'] = 'changed'
    elif case == 'revision': resolver.target_revision += 1
    elif case == 'unknown_action': body['action_id'] = 'unregistered'
    elif case == 'missing_report': cid = 'absent'
    before = db_rows(tmp_path)
    error(post(client, body, '/api/application-checks/' + cid + '/repairs'), status, code)
    assert db_rows(tmp_path) == before and handler.calls == []

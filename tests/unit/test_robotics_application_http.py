"""T04 RED: actual host checks, repair actions, confirmation and versioned downloads — DR-072."""
import pytest

from ontology_assets import AssetStore
from tests.application_contract_integration_support import ok, error, post
from tests.build_pipeline_support import db_rows, blob_bytes
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_modeling_support import no_network
from tests.robotics_application_integration_support import (
    PREFIX, service, candidate, begin, confirm, submit, finish, current_bundle, check_body,
)

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def workflow_path(state, operation=''):
    return PREFIX + '/' + state['adaptation_id'] + ('/' + operation if operation else '')


def test_real_host_diagnosis_has_located_execution_action_without_model_or_asset_writes(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h, _, ref = candidate(root); before, files = db_rows(root), blob_bytes(root)
        report = ok(post(client, check_body(ref)), 201)['report']
        issues = [i for i in report['issues'] if i['code'] == 'execution.missing_metadata']
        assert issues and all(i['evidence_refs'] and i['entity_refs'] for i in issues)
        actions = [a for i in issues for a in i['repair_actions'] if a['action_id'] == 'robotics.adapt_execution']
        assert actions and all(a['availability'] == 'available' for a in actions)
        assert report['execution_status'] == 'not_evaluated'
        after = db_rows(root)
        for table in ('assets', 'versions', 'sources', 'runs', 'run_artifacts'): assert after[table] == before[table]
        assert blob_bytes(root) == files and len(h.runtime.calls) == 2


def test_missing_schema_action_navigates_to_original_design_without_auto_revision(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h, original, ref = candidate(root, missing_role='ParameterBinding')
        report = ok(post(client, check_body(ref)), 201)['report']
        assert any(i['code'] == 'schema.missing_role' for i in report['issues']), report
        gap = next(i for i in report['issues'] if i['code'] == 'schema.missing_role')
        action = next(a for a in gap['repair_actions'] if a['action_id'] == 'robotics.revise_tbox')
        before = db_rows(root)
        result = ok(post(client, {'issue_id': gap['issue_id'], 'action_id': action['action_id'], 'input': {},
            'expected_revision': action['target']['expected_revision'], 'request_id': 'revise'},
            '/api/application-checks/' + report['check_id'] + '/repairs'))
        assert result['kind'] == 'navigate' and original['session_id'] in result['href']
        assert result['return_context']['asset_refs'] == [ref]
        after = db_rows(root)
        for table in ('assets', 'versions', 'sources', 'runs', 'design_sessions', 'design_revisions'):
            assert after[table] == before[table]
        assert len(h.runtime.calls) == 2


def test_explicit_repair_automatically_matches_and_queues_once(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, _, ref = candidate(root); report = ok(post(client, check_body(ref)), 201)['report']
        assert any(a['action_id'] == 'robotics.adapt_execution' for i in report['issues'] for a in i['repair_actions']), report
        issue = next(i for i in report['issues'] if any(a['action_id'] == 'robotics.adapt_execution' for a in i['repair_actions']))
        action = next(a for a in issue['repair_actions'] if a['action_id'] == 'robotics.adapt_execution')
        before = db_rows(root)
        args = {'issue_id': issue['issue_id'], 'action_id': action['action_id'], 'input': {},
                'expected_revision': action['target']['expected_revision'], 'request_id': 'navigate-adaptation'}
        path = '/api/application-checks/' + report['check_id'] + '/repairs'
        response = ok(post(client, args, path))
        assert response['kind'] == 'navigate' and response['target']['resource_kind'] == 'robotics_adaptation'
        state = ok(client.get(PREFIX + '/' + response['target']['resource_id'], base_url=BASE_URL))
        assert state['role_map']['confirmed'] and state['run_id'] and state['status'] == 'queued'
        assert state['matching']['method'] == 'automatic'
        assert len(db_rows(root)['runs']) == len(before['runs']) + 1 and db_rows(root)['versions'] == before['versions']
        replay = ok(post(client, args, path))
        assert replay['replayed'] and replay['target'] == response['target']


def test_http_prepare_automatically_queues_fixed_downloads_and_readonly_restore(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, original, ref = candidate(root)
        queued = ok(post(client, {**ref, 'mode': 'real', 'task_ref': None, 'request_id': 'prepare'}, PREFIX), 202)
        assert queued['status'] == 'queued'
        assert queued['matching']['method'] == 'automatic'
        done = finish(root, queued); new = done['output_asset_ref']; raw, document = current_bundle(root, new)
        base = f"/api/ontology-assets/{new['ontology_id']}/versions/{new['version_id']}"
        before, files = db_rows(root), blob_bytes(root)
        assert ok(client.get(workflow_path(done), base_url=BASE_URL)) == done
        detail = ok(client.get(base, base_url=BASE_URL))
        assert detail['application_design']['contract'] == document['contract']
        json_response = client.get(base + '/exports/json', base_url=BASE_URL)
        assert json_response.status_code == 200 and json_response.json == document
        assert 'attachment' in json_response.headers['Content-Disposition']
        assert client.get(detail['payload_url'], base_url=BASE_URL).data == raw
        assert db_rows(root) == before and blob_bytes(root) == files
        assert original['output_asset_ref'] == ref


@pytest.mark.parametrize('case', ['confirmed', 'ready', 'payload', 'executor', 'schema_hash'])
def test_confirm_http_rejects_client_authority_fields_before_mutation(tmp_path, case):
    with http_app(tmp_path) as (_, client, _, root):
        service(root); _, _, ref = candidate(root); state = begin(root, ref)
        body = {'bindings': [], 'expected_revision': state['revision'], 'request_id': 'forged', case: True}
        before, files = db_rows(root), blob_bytes(root)
        error(post(client, body, workflow_path(state, 'confirm-roles')), 400, 'invalid_request')
        assert db_rows(root) == before and blob_bytes(root) == files


@pytest.mark.parametrize('revision', [True, -1, '0', None])
def test_submit_http_revision_is_nonnegative_integer(tmp_path, revision):
    with http_app(tmp_path) as (_, client, _, root):
        service(root); _, _, ref = candidate(root); state = begin(root, ref)
        before = db_rows(root)
        error(post(client, {'expected_revision': revision, 'request_id': 'bad'}, workflow_path(state, 'submit')),
              400, 'invalid_request')
        assert db_rows(root) == before


@pytest.mark.parametrize('header,status', [({'Origin': 'https://untrusted.invalid'}, 403),
                                         ({'Host': 'untrusted.invalid'}, 400)])
def test_new_routes_reuse_host_and_origin_guards(tmp_path, header, status):
    with http_app(tmp_path) as (_, client, _, root):
        service(root); _, _, ref = candidate(root); before = db_rows(root)
        response = post(client, {**ref, 'mode': 'real', 'task_ref': None, 'request_id': 'rejected'}, PREFIX, headers=header)
        assert response.status_code == status and db_rows(root) == before


@pytest.mark.parametrize('case,code', [('stale_report', 'stale_result'), ('unknown_action', 'unknown_action')])
def test_repair_request_cannot_use_stale_report_or_other_action(tmp_path, case, code):
    with http_app(tmp_path) as (_, client, _, root):
        service(root); _, _, ref = candidate(root)
        report = ok(post(client, check_body(ref)), 201)['report']
        issue = next(i for i in report['issues'] if i['repair_actions'])
        action = issue['repair_actions'][0]
        if case == 'stale_report':
            # Change the registered role/adapter resource via its real confirmation flow.
            state = begin(root, ref); confirm(root, state)
        before = db_rows(root)
        error(post(client, {'issue_id': issue['issue_id'],
            'action_id': action['action_id'] if case == 'stale_report' else 'robotics.arbitrary', 'input': {},
            'expected_revision': action['target']['expected_revision'], 'request_id': 'blocked'},
            '/api/application-checks/' + report['check_id'] + '/repairs'), 409 if case == 'stale_report' else 400, code)
        assert db_rows(root) == before


def test_read_missing_and_disabled_new_routes_do_not_initialize_storage(tmp_path):
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        error(client.get(PREFIX + '/missing', base_url=BASE_URL), 404, 'not_found')
        error(post(client, {'ontology_id': 'missing', 'version_id': 'missing', 'mode': 'real',
            'task_ref': None, 'request_id': 'prepare'}, PREFIX), 503, 'feature_disabled')
        assert not root.exists()


@pytest.mark.parametrize('file', ['owl', 'json'])
def test_v2_tampering_is_rejected_without_affecting_old_v1_download(tmp_path, file):
    with http_app(tmp_path) as (_, client, _, root):
        service(root); _, old, ref = candidate(root)
        from ontology_assets.robotics_design import DesignAssets
        old_bytes = DesignAssets(root).export_json(**ref)
        completed = finish(root, submit(root, confirm(root, begin(root, ref))))
        version = AssetStore(root).get_version(**completed['output_asset_ref'])
        if file == 'owl': path = version['path']
        else:
            from domain_runs import RunStore
            path = next(a['path'] for a in RunStore(root).artifacts(completed['run_id']) if a['name'] == 'design.json')
        (root / path).write_bytes(b'{}')
        new = completed['output_asset_ref']
        url = f"/api/ontology-assets/{new['ontology_id']}/versions/{new['version_id']}/exports/json"
        error(client.get(url, base_url=BASE_URL), 409, 'artifact_integrity')
        assert DesignAssets(root).export_json(**ref) == old_bytes and old['output_asset_ref'] == ref

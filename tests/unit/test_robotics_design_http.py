"""Public design contracts with real core/storage and synthetic runtime — DR-070 / T08 RED."""
import json

import pytest
from rdflib import Graph
from rdflib.compare import isomorphic

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.robotics_design import DesignAssets
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app, multipart, post
from tests.robotics_design_support import Harness
from tests.robotics_dual_build_support import (
    RuntimeRegistry, ScriptedRuntime, TEST_BASE, TEST_KEY, isolated_platform, model_config,
)

__all__ = ['isolated_platform']
pytestmark = pytest.mark.usefixtures('isolated_platform')


def path(state, suffix=''):
    return '/api/robotics/design-sessions/' + state['session_id'] + suffix


def get(client, url):
    return client.get(url, base_url=BASE_URL)


def mutate(client, state, op, **body):
    return client.post(path(state, '/' + op), json=body, base_url=BASE_URL)


def error(response, status, code):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.is_json and response.json['error']['code'] == code


def public(response, root):
    text = response.get_data(as_text=True)
    for private in (TEST_KEY, TEST_BASE, str(root)):
        assert private not in text


@pytest.mark.parametrize('description', ['', '设计技能与调用参数'])
def test_public_model_admission_and_replay_pin_original_route(tmp_path, isolated_platform, description):
    model = ScriptedRuntime()
    isolated_platform(model_config(), RuntimeRegistry(openai=model))
    with http_app(tmp_path) as (_, client, _, root):
        caps = get(client, '/api/robotics/capabilities')
        assert caps.json['building_method'] == 'llm_tbox_abox'
        assert caps.json['build_ready'] is True and caps.json['configuration_issue'] is None
        data = multipart(); data['description'] = description
        received = post(client, data)
        assert received.status_code == 202, received.get_data(as_text=True)
        row = received.json
        assert row['method'] == 'llm_tbox_abox' and row['revision'] == 0
        assert row['session_ref']['id'] == row['session_id']
        assert row['status'] == ('queued' if description else 'needs_description')
        assert bool(row['run_id']) == bool(description)
        before, files = db_rows(root), blob_bytes(root)
        isolated_platform()
        replay = multipart(); replay['description'] = description
        assert post(client, replay).json == row
        conflict = multipart(); conflict['description'] = description + 'changed'
        error(post(client, conflict), 409, 'request_conflict')
        assert db_rows(root) == before and blob_bytes(root) == files and model.calls == []
        public(received, root)


@pytest.mark.parametrize('operation', ['messages', 'confirm-classes', 'edit-classes', 'confirm-schema', 'retry'])
@pytest.mark.parametrize('body', [None, [], {'request_id': 'a', 'expected_revision': 0, 'api_key': 'injected'}])
def test_design_write_endpoints_reject_malformed_or_injected_body(tmp_path, operation, body):
    with http_app(tmp_path) as (_, client, _, root):
        state = Harness(root).submit()
        before, files = db_rows(root), blob_bytes(root)
        response = client.post(path(state, '/' + operation), data=json.dumps(body),
            content_type='application/json', base_url=BASE_URL)
        if not isinstance(body, dict):
            # Preserve the platform-wide JSON guard's established response envelope.
            assert response.status_code == 400 and response.json == {'error': '请求体必须为有效的 JSON 对象'}
        else:
            error(response, 400, 'invalid_request')
        assert db_rows(root) == before and blob_bytes(root) == files


@pytest.mark.parametrize('field,value', [('expected_revision', True), ('expected_revision', '0'),
    ('expected_revision', -1), ('text', ''), ('text', 'x' * 20001), ('text', 4)],
    ids=['bool-revision', 'string-revision', 'negative-revision', 'empty-text', 'oversized-text', 'nontext'])
def test_message_field_types_and_size_are_rejected_before_enqueue(tmp_path, field, value):
    with http_app(tmp_path) as (_, client, _, root):
        state = Harness(root).submit()
        body = dict(text='设计', expected_revision=0, expected_stage='classes', request_id='message')
        body[field] = value
        before = db_rows(root)
        error(mutate(client, state, 'messages', **body), 400, 'invalid_request')
        assert db_rows(root) == before


def test_message_admission_events_and_general_retry_cannot_bypass_session(tmp_path, isolated_platform):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.submit()
        isolated_platform(h.config, RuntimeRegistry(openai=h.runtime))
        body = dict(text='确认全部类别', expected_revision=0, expected_stage='classes', request_id='message')
        response = mutate(client, state, 'messages', **body)
        assert response.status_code == 202, response.get_data(as_text=True)
        row = response.json
        assert row['stage'] == 'classes' and row['revision'] == 0 and row['class_confirmation'] is None
        assert mutate(client, state, 'messages', **body).json == row
        rid = row['run_id']
        detail = get(client, '/api/domain-runs/' + rid)
        assert detail.status_code == 200 and detail.json['operation'] == 'design_tbox'
        events = get(client, '/api/domain-runs/' + rid + '/events?after=0').json['items']
        assert events and get(client, '/api/domain-runs/' + rid + '/events?after=' + str(events[-1]['seq'])).json == {'items': []}
        history = get(client, '/api/domain-runs?operation=design_tbox')
        assert history.status_code == 200 and [r['run_id'] for r in history.json['items']] == [rid]
        before = db_rows(root)
        bypass = client.post('/api/domain-runs/' + rid + '/retry', json={'request_id': 'bypass'}, base_url=BASE_URL)
        assert bypass.status_code in (400, 409)
        assert db_rows(root) == before and h.runtime.calls == []
        public(detail, root)


def test_confirm_classes_replay_and_return_to_edit_are_local(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.classes()
        body = dict(request_id='confirm-http', expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        response = mutate(client, state, 'confirm-classes', **body)
        assert response.status_code == 200, response.get_data(as_text=True)
        confirmed = response.json
        assert confirmed['stage'] == 'properties' and confirmed['revision'] == state['revision'] + 1
        assert mutate(client, state, 'confirm-classes', **body).json == confirmed
        edited = mutate(client, confirmed, 'edit-classes', request_id='edit', expected_revision=confirmed['revision'])
        assert edited.status_code == 200 and edited.json['stage'] == 'classes'
        assert edited.json['class_confirmation'] is None and edited.json['schema_confirmation'] is None
        assert len(h.runtime.calls) == 1


@pytest.mark.parametrize('change,code', [('revision', 'revision_conflict'), ('digest', 'snapshot_conflict')])
def test_confirmation_conflicts_preserve_effective_revision(tmp_path, change, code):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.classes()
        before = db_rows(root)
        response = mutate(client, state, 'confirm-classes', request_id='conflict',
            expected_revision=0 if change == 'revision' else state['revision'],
            snapshot_sha256='0' * 64 if change == 'digest' else state['snapshot_sha256'])
        error(response, 409, code)
        assert db_rows(root) == before and h.get(state) == state


def test_schema_confirmation_queues_real_abox_and_serves_registered_artifacts(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.properties()
        response = mutate(client, state, 'confirm-schema', request_id='schema-http',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        assert response.status_code == 202, response.get_data(as_text=True)
        rid = response.json['run_id']
        assert RunStore(root).get_run(rid)['operation'] == 'extract_abox'
        h.run()
        detail = get(client, '/api/domain-runs/' + rid)
        assert detail.status_code == 200 and detail.json['status'] == 'completed'
        assert len(h.runtime.calls) == 2
        assert {a['name'] for a in detail.json['artifacts']} == {'design.json', 'candidate.owl', 'manifest.json'}
        for artifact in detail.json['artifacts']:
            download = get(client, artifact['download_url'])
            assert download.status_code == 200 and 'attachment;' in download.headers['Content-Disposition']
            assert download.data == RunStore(root).read_artifact(rid, artifact['artifact_id'])
        public(detail, root)


def test_failed_round_requires_explicit_retry_and_preserves_snapshot(tmp_path, isolated_platform):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.classes()
        h.runtime.responses.append((False, 'private ' + TEST_KEY))
        h.message(state); h.run(); failed = h.get(state)
        isolated_platform(h.config, RuntimeRegistry(openai=h.runtime))
        before = db_rows(root)
        read = get(client, path(state))
        assert read.status_code == 200 and read.json['status'] == 'failed'
        assert read.json['revision'] == state['revision'] and db_rows(root) == before
        retry = mutate(client, failed, 'retry', request_id='retry-http', expected_revision=failed['revision'],
            failed_run_id=failed['run_id'])
        assert retry.status_code == 202 and retry.json['run_id'] != failed['run_id']
        assert RunStore(root).get_run(retry.json['run_id'])['retry_of'] == failed['run_id']
        assert len(h.runtime.calls) == 2
        public(read, root)


@pytest.mark.parametrize('suffix', ['', '/revisions', '/revisions/1', '/revisions/1/exports/json', '/revisions/1/exports/owl'])
def test_readonly_restore_and_fixed_history_do_not_probe_or_mutate(tmp_path, isolated_platform, suffix):
    with http_app(tmp_path) as (_, _, _, root):
        h = Harness(root); state = h.classes()
    model = ScriptedRuntime()
    registry = isolated_platform(model_config(), RuntimeRegistry(openai=model))
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        before, files = db_rows(root), blob_bytes(root)
        response = get(client, path(state, suffix))
        assert response.status_code == 200, response.get_data(as_text=True)
        if '/exports/' in suffix:
            assert 'attachment;' in response.headers['Content-Disposition']
            assert response.data == h.service.export_revision(state['session_id'], 1, format=suffix.rsplit('/', 1)[1])
        elif suffix == '/revisions':
            assert [v['revision'] for v in response.json['items']] == [0, 1]
        assert db_rows(root) == before and blob_bytes(root) == files
        assert len(h.runtime.calls) == 1
        assert registry.lookups == [] and model.calls == []
        public(response, root)


@pytest.mark.parametrize('operation', ['messages', 'confirm-classes', 'edit-classes', 'confirm-schema', 'retry'])
def test_feature_disabled_guards_all_design_mutations_without_creating_store(tmp_path, operation):
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        error(mutate(client, {'session_id': 'missing'}, operation), 503, 'feature_disabled')
        assert not root.exists()


@pytest.mark.parametrize('suffix,status,code', [('', 404, 'not_found'), ('/revisions', 404, 'not_found'),
    ('/revisions/999', 404, 'not_found'), ('/revisions/-1', 400, 'invalid_request'),
    ('/revisions/1/exports/csv', 400, 'invalid_request'), ('?model=override', 400, 'invalid_request')])
def test_read_errors_are_structured_without_creating_store(tmp_path, suffix, status, code):
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        error(get(client, path({'session_id': 'missing'}, suffix)), status, code)
        assert not root.exists()


def test_revision_artifact_tampering_returns_integrity_error(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.classes()
        original = h.service.export_revision(state['session_id'], 1, format='json')
        matches = [p for p in root.rglob('*.json') if p.read_bytes() == original]
        assert len(matches) == 1
        matches[0].write_bytes(b'{}')
        error(get(client, path(state, '/revisions/1/exports/json')), 409, 'artifact_integrity')


def test_fixed_design_asset_detail_json_graph_catalog_and_task_gate(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.candidate(); ref = state['output_asset_ref']
        url = f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}"
        before, files = db_rows(root), blob_bytes(root)
        detail = get(client, url)
        assert detail.status_code == 200, detail.get_data(as_text=True)
        assert detail.json['design']['tbox'] == state['schema_confirmation']['tbox']
        compatibility = get(client, url + '/compatibility').json
        assert compatibility == DesignAssets(root).compatibility(**ref)
        assert detail.json['application_compatibility'] == compatibility and compatibility['status'] == 'unsupported'
        saved = get(client, url + '/exports/json')
        assert saved.status_code == 200 and saved.data == DesignAssets(root).export_json(**ref)
        assert 'attachment;' in saved.headers['Content-Disposition']
        document = saved.json
        assert document['abox']['individuals'] and document['coverage'] and document['validation']
        assert document['state']['evidence'] and document['state']['messages']
        owl = get(client, detail.json['payload_url'])
        assert owl.status_code == 200
        assert isomorphic(Graph().parse(data=owl.data, format='xml'),
                          Graph().parse(data=AssetStore(root).read_payload(**ref), format='xml'))
        graph = get(client, url + '/views/relations')
        assert graph.status_code == 200 and {n['kind'] for n in graph.json['nodes']} >= {'class', 'individual'}
        item = next(i for i in get(client, '/api/ontology-assets?domain=robotics').json['items'] if i['ontology_id'] == ref['ontology_id'])
        assert item['application_compatibility'] == compatibility
        assert item['versions'][-1]['application_compatibility'] == compatibility
        denied = client.post('/api/robotics/tasks', base_url=BASE_URL,
            json={**ref, 'mode': 'real', 'request_id': 'no-bypass'})
        error(denied, 422, 'invalid_ontology')
        assert db_rows(root) == before and blob_bytes(root) == files
        public(detail, root)


def test_old_candidate_eligibility_still_supported(tmp_path):
    from tests.robotics_governance_support import actual_build
    with http_app(tmp_path) as (_, client, _, root):
        _, row = actual_build(root)
        ref = row['output_asset_ref']
        response = get(client, f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}/compatibility")
        assert response.status_code == 200 and response.json['status'] == 'supported'


def test_design_endpoints_reuse_host_and_origin_guards(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        state = Harness(root).submit(); before = db_rows(root)
        for headers, status in (({'Origin': 'https://untrusted.invalid'}, 403), ({'Host': 'untrusted.invalid'}, 400)):
            response = client.post(path(state, '/messages'), json={}, headers=headers, base_url=BASE_URL)
            assert response.status_code == status
        assert db_rows(root) == before


@pytest.mark.parametrize('case,code', [('revision', 'revision_conflict'), ('stage', 'stage_conflict'),
    ('busy', 'session_busy'), ('configuration', 'configuration_changed'), ('capacity', 'queue_full')])
def test_message_conflicts_do_not_register_a_partial_operation(tmp_path, isolated_platform, case, code):
    with http_app(tmp_path, DATAMIND_ROBOTICS_MAX_PENDING=1) as (_, client, _, root):
        h = Harness(root); state = h.submit()
        isolated_platform(h.config, RuntimeRegistry(openai=h.runtime))
        if case == 'busy':
            h.message(state)
        elif case == 'capacity':
            h.submit(request_id='occupy', description='排队')
        elif case == 'configuration':
            isolated_platform()
        before, files = db_rows(root), blob_bytes(root)
        response = mutate(client, state, 'messages', request_id='conflicting', text='设计技能',
            expected_revision=99 if case == 'revision' else 0,
            expected_stage='properties' if case == 'stage' else 'classes')
        error(response, 429 if case == 'capacity' else 409, code)
        assert db_rows(root) == before and blob_bytes(root) == files and h.runtime.calls == []


def test_reading_schema5_does_not_migrate_via_design_get(tmp_path):
    from tests.robotics_design_support import restore_v5
    root = tmp_path / 'assets'; restore_v5(root)
    before, files = db_rows(root), blob_bytes(root)
    with http_app(tmp_path, enabled=False) as (_, client, _, _):
        error(get(client, path({'session_id': 'missing'})), 503, 'storage_unavailable')
        assert db_rows(root) == before and blob_bytes(root) == files


def test_tampered_final_json_is_invalid_and_cannot_be_downloaded(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); state = h.candidate(); ref = state['output_asset_ref']
        record = next(a for a in RunStore(root).artifacts(state['run_id']) if a['name'] == 'design.json')
        (root / record['path']).write_bytes(b'{}')
        url = f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}"
        eligibility = get(client, url + '/compatibility')
        assert eligibility.status_code == 200 and eligibility.json['status'] == 'invalid'
        error(get(client, url + '/exports/json'), 409, 'artifact_integrity')

"""T04 RED: real Blueprint, guards, OWL import and task artifact identity — DR-063 / DR-064 / DR-066."""
import io
import json

import pytest

from domain_runs import RunStore
from domains.robotics import evidence
from ontology_assets import AssetStore
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_governance_support import NOW
from tests.robotics_task_support import (TEXT, assert_generated, assert_no_result, fixture,
    model_server, synthetic_answers)
from tests.storage_pipeline_support import fresh_payload

PREFIX = '/api/robotics/tasks'


def ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.is_json
    return response.get_json()


def error(response, status, code):
    body = ok(response, status)
    assert body['error']['code'] == code and body['error']['message']


def post(client, path, body):
    return client.post(path, json=body, base_url=BASE_URL)


def start(client, version, **changes):
    return ok(post(client, PREFIX, {'ontology_id': version['ontology_id'], 'version_id': version['version_id'],
        'mode': 'real', 'request_id': 'start', **changes}), 201)


def message(client, session, text=TEXT, request_id='message'):
    return ok(post(client, PREFIX + '/' + session['session_id'] + '/messages',
        {'text': text, 'expected_revision': session['revision'], 'request_id': request_id}))


@pytest.fixture
def model_config(monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    with model_server({}) as (url, calls):
        monkeypatch.setenv('DATAMIND_LLM_BASE', url)
        monkeypatch.setenv('DATAMIND_LLM_MODEL', 'scripted-test-model')
        monkeypatch.setenv('DATAMIND_LLM_KEY', 'synthetic-secret')
        yield calls


def test_real_task_routes_generate_preview_and_exact_download(tmp_path, model_config):
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root, ready=True)
        session = start(client, version)
        result = message(client, session)
        expected = assert_generated(root, result)
        path = f"{PREFIX}/{result['session_id']}/candidate?revision={result['revision']}"
        response = client.get(path, base_url=BASE_URL)
        assert response.status_code == 200 and response.data == expected
        assert 'attachment' in response.headers['Content-Disposition']
        assert response.mimetype == 'application/xml'
        assert str(root) not in json.dumps(result) and 'synthetic-secret' not in json.dumps(result)
        assert model_config[0]['path'] == '/v1/chat/completions'


def test_http_fresh_build_completes_without_generic_actor_reason_or_publish(tmp_path, model_config):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        current = message(client, start(client, version))
        for index in range(8):
            if current['status'] == 'generated': break
            assert current['status'] == 'needs_information'
            current = ok(post(client, f"{PREFIX}/{current['session_id']}/completion", {
                'answers': synthetic_answers(current), 'expected_revision': current['revision'],
                'request_id': f'fill-{index}'}))
        assert_generated(root, current)
        assert all(v['publication_state'] == 'candidate' for v in assets.list_versions(version['ontology_id']))
        restored = ok(client.get(f"{PREFIX}/{current['session_id']}", base_url=BASE_URL))
        assert restored == current


@pytest.mark.parametrize('case', ['empty', 'unknown', 'missing_revision', 'extra_config', 'json_array', 'origin'])
def test_bad_or_cross_origin_task_requests_do_not_mutate_state(tmp_path, model_config, case):
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        session = start(client, version)
        before = db_rows(root), blob_bytes(root)
        body = {'text': TEXT, 'expected_revision': session['revision'], 'request_id': 'message'}
        headers = {}
        path = f"{PREFIX}/{session['session_id']}/messages"
        expected = 400
        if case == 'empty': body['text'] = ' '
        elif case == 'unknown': path = f'{PREFIX}/unknown/messages'; expected = 404
        elif case == 'missing_revision': body.pop('expected_revision')
        elif case == 'extra_config': body['config'] = {'threshold': '0.8'}
        elif case == 'json_array': body = []
        else: headers['Origin'] = 'https://untrusted.invalid'; expected = 403
        response = client.post(path, json=body, headers=headers, base_url=BASE_URL)
        assert response.status_code == expected
        assert (db_rows(root), blob_bytes(root)) == before and model_config == []


def test_disabled_task_feature_returns_clear_error_without_initializing_storage(tmp_path):
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        error(post(client, PREFIX, {'ontology_id': 'ont_missing', 'version_id': 'ver_missing',
            'mode': 'real', 'request_id': 'start'}), 503, 'feature_disabled')
        assert not root.exists()


def test_import_valid_owl_is_optional_candidate_entry_and_preserves_publication_contract(tmp_path, model_config):
    with http_app(tmp_path) as (_, client, _, root):
        response = client.post('/api/robotics/imports', data={
            'name': '导入合成 OWL', 'mode': 'real', 'request_id': 'import',
            'file': (io.BytesIO(fresh_payload('real')), 'ontology.owl')}, base_url=BASE_URL)
        imported = ok(response, 201)
        assets = AssetStore(root)
        version = assets.get_version(imported['ontology_id'], imported['version_id'])
        assert version['publication_state'] == 'candidate'
        assert assets.read_payload(imported['ontology_id'], imported['version_id']) == fresh_payload('real')
        with pytest.raises(ValueError): assets.load_robotics(imported['ontology_id'], imported['version_id'])
        result = message(client, start(client, version))
        assert result['status'] == 'needs_information'
        assert_no_result(result)


@pytest.mark.parametrize('case', ['bad_xml', 'wrong_contract', 'dtd', 'demo_as_real', 'oversize'])
def test_invalid_owl_import_leaves_no_asset_or_partial_version(tmp_path, case):
    settings = {'DATAMIND_ROBOTICS_FILE_LIMIT_BYTES': 16} if case == 'oversize' else {}
    with http_app(tmp_path, **settings) as (_, client, _, root):
        AssetStore(root)
        before = db_rows(root), blob_bytes(root)
        content = {'bad_xml': b'<bad', 'wrong_contract': b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',
            'dtd': b'<!DOCTYPE rdf [<!ENTITY x SYSTEM "file:///etc/passwd">]><rdf>&x;</rdf>',
            'demo_as_real': fresh_payload('demo'), 'oversize': b'x' * 17}[case]
        response = client.post('/api/robotics/imports', data={'name': 'invalid import', 'mode': 'real',
            'request_id': 'import', 'file': (io.BytesIO(content), 'ontology.owl')}, base_url=BASE_URL)
        assert response.status_code == (413 if case == 'oversize' else 422), response.text
        assert (db_rows(root), blob_bytes(root)) == before


def test_task_download_rejects_corruption_and_has_no_xml_on_error(tmp_path, model_config):
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root, ready=True)
        result = message(client, start(client, version))
        runs = RunStore(root)
        artifact = next(a for a in runs.artifacts(result['run_id']) if a['kind'] == 'candidate')
        (root / artifact['path']).write_bytes(b'<wrong/>')
        path = f"{PREFIX}/{result['session_id']}/candidate?revision={result['revision']}"
        response = client.get(path, base_url=BASE_URL)
        error(response, 409, 'artifact_integrity')
        assert 'Content-Disposition' not in response.headers


def test_pending_task_has_no_download_and_revision_conflict_is_explicit(tmp_path, model_config):
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        initial = start(client, version)
        current = message(client, initial)
        error(client.get(f"{PREFIX}/{current['session_id']}/candidate?revision={current['revision']}",
                         base_url=BASE_URL), 409, 'no_candidate')
        before = db_rows(root), blob_bytes(root)
        error(post(client, f"{PREFIX}/{current['session_id']}/completion", {
            'answers': synthetic_answers(current), 'expected_revision': initial['revision'],
            'request_id': 'stale'}), 409, 'session_conflict')
        assert (db_rows(root), blob_bytes(root)) == before


def test_real_model_http_error_is_reported_as_task_error_without_candidate(tmp_path, monkeypatch):
    with model_server({'status': 401, 'body': b'private-model-error'}) as (url, calls):
        monkeypatch.setenv('DATAMIND_LLM_BASE', url)
        monkeypatch.setenv('DATAMIND_LLM_MODEL', 'test-model')
        monkeypatch.setenv('DATAMIND_LLM_KEY', 'test-only-key')
        with http_app(tmp_path) as (_, client, _, root):
            _, version, _ = fixture(root)
            result = message(client, start(client, version))
            assert result['status'] == 'error' and result['error']['code'] == 'model_auth'
            assert_no_result(result)
            assert 'test-only-key' not in json.dumps(result) and 'private-model-error' not in json.dumps(result)
            assert len(calls) == 1


def test_uploading_evidence_alone_does_not_confirm_task_and_selected_attachment_is_bound(tmp_path, model_config):
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        current = message(client, start(client, version))
        # Only map/confirm the dependencies to expose the actual applicability scope.
        for index in range(5):
            if any(g['kind'] == 'applicability' for g in current['gaps']): break
            current = ok(post(client, f"{PREFIX}/{current['session_id']}/completion", {
                'answers': synthetic_answers(current), 'expected_revision': current['revision'],
                'request_id': f'stage-{index}'}))
        assert any(g['kind'] == 'applicability' for g in current['gaps'])
        raw = b'SYNTHETIC TEST MATERIAL; NOT A ROBOT VALIDATION'
        attached = ok(client.post(f"/api/ontology-assets/{version['ontology_id']}/evidence-sources",
            data={'file': (io.BytesIO(raw), 'synthetic.txt', 'text/plain')}, base_url=BASE_URL), 201)
        assert ok(client.get(f"{PREFIX}/{current['session_id']}", base_url=BASE_URL)) == current
        assert_no_result(current)
        answers = synthetic_answers(current)
        for answer in answers:
            if 'reference' in answer: answer['source_asset_ids'] = [attached['source_asset_id']]
        after = ok(post(client, f"{PREFIX}/{current['session_id']}/completion", {
            'answers': answers, 'expected_revision': current['revision'], 'request_id': 'with-attachment'}))
        assert_generated(root, after)
        from rdflib import RDF
        from tests.robotics_support import S
        from tests.robotics_task_support import graph_of
        graph = graph_of(root, after)
        subjects = list(graph.subjects(RDF.type, S.ReadinessEvidence))
        assert len(subjects) == 2
        assert all(attached['source_asset_id'] in {str(v) for v in graph.objects(s, S.evidenceSourceId)} for s in subjects)
        assert AssetStore(root).read_source(attached['source_asset_id']) == raw

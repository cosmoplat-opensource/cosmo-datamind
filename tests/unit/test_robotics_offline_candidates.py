"""Offline candidates, optional notes and stable version labels — DR-067."""
from copy import deepcopy
import json

import pytest
from rdflib import RDF, Literal, XSD

from domain_runs import RunStore
from domain_runs.robotics_task_service import TaskService
from domains.robotics import evidence, generation
from domains.robotics.graph import S
from ontology_assets import AssetStore
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_governance_support import NOW, TASK
from tests.robotics_task_support import fixture, model, start, message, assert_generated, graph_of


def no_evidence(graph):
    for subject in list(graph.subjects(RDF.type, S.ReadinessEvidence)):
        graph.remove((subject, None, None))


@pytest.mark.parametrize('condition', ['missing', 'expired', 'revoked', 'changed'])
def test_offline_candidate_is_generated_without_false_applicability_and_strict_still_blocks(tmp_path, monkeypatch, condition):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    def mutate(graph):
        if condition == 'missing': no_evidence(graph)
        if condition == 'expired':
            for subject in graph.subjects(RDF.type, S.ReadinessEvidence):
                graph.set((subject, S.validUntil, Literal('2000-01-01T00:00:00+00:00', datatype=XSD.string)))
        if condition == 'changed':
            graph.set((S.Perception_Current, S.exposureTime, Literal('321', datatype=XSD.string)))
    assets, version, _ = fixture(tmp_path / 'assets', ready=True, mutate=mutate)
    context = assets.load_robotics_application(version['ontology_id'], version['version_id'])
    if condition == 'revoked':
        context['evidence_revocations'] = [{'kind': 'revoked', 'evidence_iri': str(s)}
            for s in context['graph'].subjects(RDF.type, S.ReadinessEvidence)]
    before = set(context['graph'])
    strict = generation.generate_task(context, TASK, output_dir=tmp_path / 'strict')
    assert strict['status'] == 'needs_information' and not strict['candidate_written']
    offline = generation.generate_task(context, TASK, output_dir=tmp_path / 'offline', purpose='offline')
    assert offline['status'] == 'generated'
    assert offline['task_readiness'] == 'not_verified'
    assert offline['execution_readiness'] == 'not_verified' and offline['applicability_issues']
    assert set(context['graph']) == before
    assert offline['validation']['symbolic_validation']['valid']


def test_offline_http_generates_without_notes_and_notes_do_not_change_version_or_validate_applicability(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root, ready=True, mutate=no_evidence)
        service = TaskService(root, model_client=model()[0], generation_policy='offline')
        current = message(service, start(service, version))
        expected = assert_generated(root, current)
        assert current['gaps'] == [] and len(current['optional_gaps']) == 2
        assert current['task_readiness'] == 'not_verified'
        before_payload = assets.read_payload(current['ontology_id'], current['version_id'])
        path = '/api/robotics/tasks/' + current['session_id']
        notes = [{'kind': 'grasp_compatibility', 'text': '仅记录待调试事项，不是适配验证'}]
        body = {'notes': notes, 'expected_revision': current['revision'], 'request_id': 'notes'}
        response = client.post(path + '/notes', json=body, base_url=BASE_URL)
        assert response.status_code == 200, response.text
        saved = response.get_json()
        assert saved['version_id'] == current['version_id'] and saved['version_number'] == 2
        assert saved['task_readiness'] == 'not_verified' and saved['status'] == 'generated'
        assert saved['adaptation_notes'][-1]['text'] == notes[0]['text']
        assert saved['adaptation_notes'][-1]['version_id'] == current['version_id']
        assert client.get(path, base_url=BASE_URL).get_json() == saved
        assert client.post(path + '/notes', json=body, base_url=BASE_URL).get_json() == saved
        assert service.read_candidate(current['session_id'], expected_revision=saved['revision']) == expected
        assert assets.read_payload(current['ontology_id'], current['version_id']) == before_payload
        assert not list(graph_of(root, saved).subjects(RDF.type, S.ReadinessEvidence))
        assert len(assets.list_versions(current['ontology_id'])) == 2


@pytest.mark.parametrize('fault', ['missing_parameter', 'invalid_parameter', 'missing_mapping'])
def test_offline_mode_keeps_required_information_gates(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    def mutate(graph):
        no_evidence(graph)
        if fault == 'missing_parameter': graph.remove((S.Perception_Current, S.exposureTime, None))
        elif fault == 'invalid_parameter': graph.set((S.Perception_Current, S.threshold, Literal('9', datatype=XSD.string)))
        else: graph.remove((S.Slot_B_01, S.hasPlacementProfile, None))
    _, version, _ = fixture(tmp_path / 'assets', ready=True, mutate=mutate)
    service = TaskService(tmp_path / 'assets', model_client=model()[0], generation_policy='offline')
    current = message(service, start(service, version))
    assert current['status'] == 'needs_information' and current['candidate'] is None
    assert any(g['kind'] in ('parameter', 'mapping') for g in current['gaps'])
    assert not any(g['kind'] == 'applicability' for g in current['gaps'])


def test_old_task_can_resume_offline_without_llm_and_get_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root, ready=True, mutate=no_evidence)
        strict = TaskService(root, model_client=model()[0])
        current = message(strict, start(strict, version))
        legacy = deepcopy(current)
        for key in ('generation_policy', 'version_number', 'optional_gaps', 'adaptation_notes', 'task_readiness'):
            legacy.pop(key, None)
        with strict.storage.transaction(write=True) as conn:
            conn.execute('UPDATE robotics_task_sessions SET record=? WHERE session_id=?',
                         (json.dumps(legacy), current['session_id']))
        before = db_rows(root), blob_bytes(root)
        path = '/api/robotics/tasks/' + current['session_id']
        restored = client.get(path, base_url=BASE_URL).get_json()
        assert restored['gaps'] == [] and len(restored['optional_gaps']) == 2
        assert restored['version_number'] == 2
        assert (db_rows(root), blob_bytes(root)) == before
        result = client.post(path + '/generate', json={'expected_revision': current['revision'],
            'request_id': 'resume'}, base_url=BASE_URL)
        assert result.status_code == 200, result.text
        generated = result.get_json()
        assert_generated(root, generated)
        assert generated['version_id'] == current['version_id']
        assert generated['task_readiness'] == 'not_verified'
        report = RunStore(root).get_run(generated['run_id'])['result']
        assert report['generation_purpose'] == 'offline' and report['task_readiness'] == 'not_verified'


def test_version_numbers_are_asset_local_stable_across_branches_and_reopen(tmp_path):
    root = tmp_path / 'assets'
    store = AssetStore(root)
    content = b'{"objects":[],"relations":[]}'
    one = store.create_asset(name='one', domain='data')['ontology_id']
    two = store.create_asset(name='two', domain='data')['ontology_id']
    first = store.add_version(one, content, payload_kind='datamind_ir')
    foreign = store.add_version(two, content, payload_kind='datamind_ir')
    second = store.add_version(one, content, payload_kind='datamind_ir', parent_version_id=first['version_id'])
    third = store.add_version(one, content, payload_kind='datamind_ir', parent_version_id=first['version_id'])
    reopened = AssetStore(root, read_only=True)
    assert [reopened.version_number(one, v['version_id']) for v in (first, second, third)] == [1, 2, 3]
    assert reopened.version_number(two, foreign['version_id']) == 1
    with pytest.raises(KeyError): reopened.version_number(one, foreign['version_id'])


def test_revocation_during_offline_generation_updates_durable_report(tmp_path, monkeypatch):
    from ontology_assets.governance import GovernanceService
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    root = tmp_path / 'assets'
    _, version, _ = fixture(root, ready=True)
    original = generation.generate_task
    def revoke_after_compilation(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result['task_readiness'] == 'evidence_confirmed'
        GovernanceService(root).revoke_evidence(version['ontology_id'],
            evidence_iri=str(S.TaskCompletionEvidence1), version_id=version['version_id'],
            actor='synthetic test', reason='test revocation during compilation', request_id='revoke')
        return result
    monkeypatch.setattr(generation, 'generate_task', revoke_after_compilation)
    service = TaskService(root, model_client=model()[0], generation_policy='offline')
    current = message(service, start(service, version))
    assert current['status'] == 'generated' and current['task_readiness'] == 'not_verified'
    runs = RunStore(root)
    result = runs.get_run(current['run_id'])['result']
    assert any(i['code'] == 'evidence_revoked' for i in result['applicability_issues'])
    artifact = next(a for a in runs.artifacts(current['run_id']) if a['kind'] == 'validation')
    report = json.loads(runs.read_artifact(current['run_id'], artifact['artifact_id']))
    assert report == result and report['execution_readiness'] == 'not_verified'


def test_offline_still_rejects_tampered_snapshot_and_notes_reject_forged_status(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root, ready=True, mutate=no_evidence)
        context = assets.load_robotics_application(version['ontology_id'], version['version_id'])
        context['graph'].set((S.Perception_Current, S.exposureTime, Literal('222', datatype=XSD.string)))
        result = generation.generate_task(context, TASK, output_dir=tmp_path / 'bad', purpose='offline')
        assert result['status'] == 'error' and not result['candidate_written']
        service = TaskService(root, model_client=model()[0], generation_policy='offline')
        current = message(service, start(service, version))
        before = db_rows(root), blob_bytes(root)
        for notes in ([], [{'kind': 'grasp_compatibility', 'text': 'x', 'confirmed': True}],
                      [{'kind': 'arbitrary', 'text': 'x'}], [{'kind': 'grasp_compatibility', 'text': ' '} ]):
            response = client.post('/api/robotics/tasks/' + current['session_id'] + '/notes',
                json={'notes': notes, 'expected_revision': current['revision'], 'request_id': 'bad'}, base_url=BASE_URL)
            assert response.status_code == 422
            assert (db_rows(root), blob_bytes(root)) == before

"""Task configuration inspection and explicit replacement — DR-061 / DR-066."""
from copy import deepcopy
import json

import pytest
from rdflib import Literal, RDF, RDFS

from domain_runs.robotics_task_service import TaskService
from domains.robotics import evidence, task_knowledge
from domains.robotics.graph import S
from domains.robotics.task_completion import inspect_gaps
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_governance_support import NOW, TASK
from tests.robotics_task_support import fixture, message, model, start
from tests.unit.test_robotics_task_completion import ready_graph


def alternative(graph):
    for predicate, value in list(graph.predicate_objects(S.Placement_Current)):
        graph.add((S.AlternativePlacement, predicate, value))
    graph.set((S.AlternativePlacement, RDFS.label, Literal('另一放置配置')))
    graph.set((S.UnrelatedSlot, RDF.type, S.PlacementSlot))
    graph.set((S.UnrelatedSlot, S.hasPlacementProfile, S.Placement_Current))


def test_configuration_summary_is_task_scoped_read_only_and_includes_parameter_sources():
    graph = ready_graph()
    alternative(graph)
    before = set(graph)
    result = inspect_gaps(graph, TASK, mode='real', now=NOW)
    rows = {row['field']: row for row in result['configurations']}
    assert set(rows) == {'hasPerceptionProfile', 'hasPlacementProfile'}
    perception = rows['hasPerceptionProfile']
    assert perception['entity_iri'] == str(S.PartType_A)
    assert perception['profile_iri'] == str(S.Perception_Current)
    assert {v['field'] for v in perception['parameters']} == {
        'objectName', 'modelName', 'threshold', 'exposureTime', 'captureJoints'}
    placement = rows['hasPlacementProfile']
    assert placement['entity_iri'] == str(S.Slot_B_01)
    assert placement['parameters'][0]['value'] == str(graph.value(S.PlacePose_Current, S.rawValue))
    assert placement['parameters'][0]['source_iri'] == str(S.PlacePose_Current)
    assert str(S.AlternativePlacement) in {c['id'] for c in placement['candidates']}
    assert result['items'] == [] and set(graph) == before


def test_replace_configuration_invalidates_scope_without_changing_other_slots_or_old_graph():
    graph = ready_graph()
    alternative(graph)
    before = set(graph)
    changed = task_knowledge.replace_configuration(graph, TASK, field='hasPlacementProfile',
        target=str(S.AlternativePlacement), mode='real')
    assert set(graph) == before
    assert list(changed.objects(S.Slot_B_01, S.hasPlacementProfile)) == [S.AlternativePlacement]
    assert list(changed.objects(S.UnrelatedSlot, S.hasPlacementProfile)) == [S.Placement_Current]
    missing = inspect_gaps(changed, TASK, mode='real', now=NOW)
    assert missing['status'] == 'needs_information'
    assert any(g['field'] == 'placement_compatibility' for g in missing['items'])
    assert str(changed.value(S.Slot_B_01, S.confirmationStatus)) == 'needs_review'


@pytest.mark.parametrize('arguments', [
    {'field': 'hasSlot', 'target': str(S.Slot_B_01)},
    {'field': 'hasPlacementProfile', 'target': str(S.Perception_Current)},
    {'field': 'hasPlacementProfile', 'target': 'urn:missing'},
    {'field': 'hasPlacementProfile', 'target': str(S.Placement_Current)},
    {'field': 'hasPlacementProfile', 'create': {'kind': 'PerceptionProfile', 'label': 'wrong'}},
    {'field': 'hasPlacementProfile', 'target': str(S.Placement_Current),
     'create': {'kind': 'PlacementProfile', 'label': 'both'}},
])
def test_invalid_configuration_replacement_cannot_modify_graph(arguments):
    graph = ready_graph()
    before = set(graph)
    with pytest.raises(ValueError):
        task_knowledge.replace_configuration(graph, TASK, mode='real', **arguments)
    assert set(graph) == before


def test_create_configuration_requires_completion_and_unresolved_tasks_cannot_replace():
    graph = ready_graph()
    changed = task_knowledge.replace_configuration(graph, TASK, mode='real',
        field='hasPerceptionProfile', create={'kind': 'PerceptionProfile', 'label': '新相机配置'})
    target = changed.value(S.PartType_A, S.hasPerceptionProfile)
    assert target != S.Perception_Current and str(changed.value(target, RDFS.label)) == '新相机配置'
    assert changed.value(target, S.exposureTime) is None
    gaps = inspect_gaps(changed, TASK, mode='real', now=NOW)['items']
    assert any(g['field'] == 'exposureTime' and g['entity_iri'] == str(target) for g in gaps)
    unresolved = {**TASK, 'slot': None}
    with pytest.raises(ValueError):
        task_knowledge.replace_configuration(graph, unresolved, mode='real',
            field='hasPlacementProfile', create={'kind': 'PlacementProfile', 'label': '不应创建'})


def test_configuration_http_is_atomic_versioned_idempotent_and_legacy_session_is_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root, ready=True, mutate=alternative)
        model_client, _ = model()
        service = TaskService(root, model_client=model_client)
        current = message(service, start(service, version))
        assert current['status'] == 'generated'
        original = assets.read_payload(current['ontology_id'], current['version_id'])
        path = '/api/robotics/tasks/' + current['session_id'] + '/configurations'
        body = {'field': 'hasPlacementProfile', 'target': str(S.AlternativePlacement),
                'expected_revision': current['revision'], 'request_id': 'replace'}
        before = db_rows(root), blob_bytes(root)
        for bad in ({**body, 'entity_iri': str(S.UnrelatedSlot)}, {**body, 'target': str(S.Part_A_001)}):
            response = client.post(path, json=bad, base_url=BASE_URL)
            assert response.status_code in (400, 422)
            assert (db_rows(root), blob_bytes(root)) == before
        response = client.post(path, json=body, base_url=BASE_URL)
        assert response.status_code == 200, response.text
        changed = response.get_json()
        assert changed['version_id'] != current['version_id']
        assert changed['revision'] == current['revision'] + 1
        assert changed['status'] == 'needs_information'
        assert changed['candidate'] is None and changed['tree'] is None
        assert assets.read_payload(current['ontology_id'], current['version_id']) == original
        assert client.post(path, json=body, base_url=BASE_URL).get_json() == changed
        stale = client.post(path, json={**body, 'request_id': 'stale'}, base_url=BASE_URL)
        assert stale.status_code == 409
        assert client.get('/api/robotics/tasks/' + current['session_id'] +
            '/candidate?revision=' + str(current['revision']), base_url=BASE_URL).status_code == 409
        # Old persisted sessions gain a read-only configuration summary on GET.
        legacy = deepcopy(changed)
        legacy.pop('configurations')
        with service.storage.transaction(write=True) as conn:
            conn.execute('UPDATE robotics_task_sessions SET record=? WHERE session_id=?',
                         (json.dumps(legacy), current['session_id']))
        before = db_rows(root), blob_bytes(root)
        restored = service.get(current['session_id'])
        assert len(restored['configurations']) == 2
        assert (db_rows(root), blob_bytes(root)) == before

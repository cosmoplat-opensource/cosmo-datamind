"""IR-020 T02 extension: task-scoped missing-information forms — DR-061 / DR-066.

All confirmations below are synthetic test setup, not physical evidence.
Production save/confirmation and browser flows are separately required by T04.
"""
from copy import deepcopy
from functools import lru_cache
import importlib

import pytest
from rdflib import Literal, RDF, RDFS, XSD

from domains.robotics import evidence, grounding
from tests.robotics_governance_support import NOW, TASK, evidence_spec
from tests.robotics_support import S, build, config


def api():
    return importlib.import_module('domains.robotics.task_completion')


@lru_cache(maxsize=1)
def _ready_graph():
    """Independent complete RDF fixture built from frozen XML, never a demo conversion."""
    graph = build()
    graph.set((S.PartType_A, S.hasPerceptionProfile, S.Perception_Current))
    graph.set((S.Slot_B_01, S.hasPlacementProfile, S.Placement_Current))
    values = config()
    for field, prop in [('object_name', 'objectName'), ('model_name', 'modelName'),
                        ('threshold', 'threshold'), ('exposure_time', 'exposureTime')]:
        graph.set((S.Perception_Current, S[prop], Literal(values['Perception'][field], datatype=XSD.string)))
    joints = graph.value(S.Perception_Current, S.hasCaptureJoints)
    pose = graph.value(S.Placement_Current, S.hasTargetPose)
    graph.set((joints, S.rawValue, Literal(values['Perception']['position'], datatype=XSD.string)))
    graph.set((pose, S.rawValue, Literal(values['Place']['place_pose'], datatype=XSD.string)))
    for entity in (S.Part_A_001, S.PartType_A, S.Station_B, S.Carrier_B_01, S.Slot_B_01,
                   S.Perception_Current, S.Placement_Current, joints, pose):
        graph.set((entity, S.confirmationStatus, Literal('confirmed', datatype=XSD.string)))
    for index, kind, implementation, slot in [
        (0, 'grasp_compatibility', S.grasp_Subtree_Impl, None),
        (1, 'placement_compatibility', S.Place_Subtree_Impl, str(S.Slot_B_01)),
    ]:
        iri = str(S[f'TaskCompletionEvidence{index}'])
        spec = evidence_spec(iri=iri, kind=kind, implementation=str(implementation), slot=slot,
                             valid_until='2099-01-01T00:00:00+00:00')
        graph = evidence.register_evidence(graph, spec, mode='real', now=NOW)
        graph = evidence.review_evidence(graph, iri, decision='accepted', actor='synthetic-test-fixture',
            reason='合成测试夹具；不证明现场适配', now=NOW)
    return graph


def ready_graph():
    return deepcopy(_ready_graph())


def inspect(graph, task=None, **kwargs):
    return api().inspect_gaps(graph, deepcopy(TASK if task is None else task), mode='real', now=NOW, **kwargs)


def items_of(result, kind):
    return [item for item in result['items'] if item['kind'] == kind]


def test_fixture_is_complete_for_existing_grounding_without_new_completion_module(monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    graph = ready_graph()
    result = grounding.resolve_request(graph, {k: TASK[k] for k in ('part', 'station', 'carrier', 'slot')})
    assert result['issues'] == []
    assert result['config'] == config()
    for index in range(2):
        assert evidence.evaluate_evidence(graph, str(S[f'TaskCompletionEvidence{index}']), now=NOW)['valid']


def test_complete_task_has_no_completion_form_and_inspection_is_read_only():
    module = api()
    graph = ready_graph()
    before = set(graph)
    task = deepcopy(TASK)
    result = module.inspect_gaps(graph, task, mode='real', now=NOW)
    assert result['status'] == 'ready'
    assert result['items'] == []
    assert result['execution_readiness'] == 'not_verified'
    assert task == TASK and set(graph) == before


@pytest.mark.parametrize('owner, field', [
    ('perception', 'objectName'), ('perception', 'modelName'), ('perception', 'threshold'),
    ('perception', 'exposureTime'), ('joints', 'rawValue'), ('pose', 'rawValue'),
])
def test_only_missing_public_parameter_becomes_an_input(owner, field):
    api()
    graph = ready_graph()
    entity = {'perception': S.Perception_Current,
              'joints': graph.value(S.Perception_Current, S.hasCaptureJoints),
              'pose': graph.value(S.Placement_Current, S.hasTargetPose)}[owner]
    graph.remove((entity, S[field], None))
    before = set(graph)
    result = inspect(graph)
    assert result['status'] == 'needs_information'
    fields = items_of(result, 'parameter')
    assert [(item['entity_iri'], item['field']) for item in fields] == [(str(entity), field)]
    assert fields[0]['label'] and fields[0]['reason'] and fields[0]['id']
    assert set(graph) == before


@pytest.mark.parametrize('value', ['__UNRESOLVED_threshold', '', 'NaN', '1.2'])
def test_present_but_invalid_parameter_is_still_a_gap(value):
    api()
    graph = ready_graph()
    graph.set((S.Perception_Current, S.threshold, Literal(value, datatype=XSD.string)))
    result = inspect(graph)
    assert result['status'] == 'needs_information'
    assert [(item['entity_iri'], item['field']) for item in items_of(result, 'parameter')] == [
        (str(S.Perception_Current), 'threshold')]


@pytest.mark.parametrize('entity, field', [
    (S.Part_A_001, 'hasPartType'), (S.Station_B, 'hasCarrier'), (S.Carrier_B_01, 'hasSlot'),
    (S.PartType_A, 'hasPerceptionProfile'), (S.Slot_B_01, 'hasPlacementProfile'),
    (S.Perception_Current, 'hasCaptureJoints'), (S.Placement_Current, 'hasTargetPose'),
])
def test_missing_relation_is_exposed_on_current_task_object(entity, field):
    api()
    graph = ready_graph()
    graph.remove((entity, S[field], None))
    result = inspect(graph)
    assert result['status'] == 'needs_information'
    assert any(item['entity_iri'] == str(entity) and item['field'] == field
               for item in items_of(result, 'mapping'))
    if field == 'hasPerceptionProfile':
        # Do not edit a configuration before the user has selected its mapping.
        assert not any(item['entity_iri'] == str(S.Perception_Current)
                       for item in items_of(result, 'parameter'))


def test_unrelated_incomplete_objects_do_not_expand_current_task_form():
    api()
    graph = ready_graph()
    graph.add((S.UnrelatedProfile, RDF.type, S.PerceptionProfile))
    graph.add((S.UnrelatedProfile, RDFS.label, Literal('其他任务配置')))
    graph.add((S.UnrelatedSlot, RDF.type, S.PlacementSlot))
    graph.add((S.UnrelatedSlot, RDFS.label, Literal('其他任务槽位')))
    assert inspect(graph)['items'] == []
    assert inspect(graph)['status'] == 'ready'


@pytest.mark.parametrize('entity', [S.Part_A_001, S.Perception_Current])
def test_unconfirmed_knowledge_requests_confirmation_without_reentering_valid_values(entity):
    api()
    graph = ready_graph()
    graph.set((entity, S.confirmationStatus, Literal('source_extracted', datatype=XSD.string)))
    result = inspect(graph)
    assert result['status'] == 'needs_information'
    assert [(item['entity_iri'], item['field']) for item in items_of(result, 'confirmation')] == [
        (str(entity), 'confirmationStatus')]
    assert items_of(result, 'parameter') == []
    assert str(graph.value(entity, S.confirmationStatus)) == 'source_extracted'


@pytest.mark.parametrize('case', ['missing', 'expired', 'revoked', 'wrong_scope', 'pending'])
def test_only_invalid_applicability_scope_is_requested_and_not_auto_confirmed(case):
    api()
    graph = ready_graph()
    subject = S.TaskCompletionEvidence1
    revocations = []
    if case == 'missing':
        graph.remove((subject, None, None))
    elif case == 'expired':
        graph.set((subject, S.validUntil, Literal('2000-01-01T00:00:00+00:00', datatype=XSD.string)))
    elif case == 'revoked':
        revocations = [{'evidence_iri': str(subject), 'kind': 'revoked'}]
    elif case == 'wrong_scope':
        graph.set((subject, S.forSlot, S.UnrelatedSlot))
    else:
        graph.set((subject, S.confirmationStatus, Literal('pending', datatype=XSD.string)))
    before = set(graph)
    result = inspect(graph, revocations=revocations)
    assert result['status'] == 'needs_information'
    assert [item['field'] for item in items_of(result, 'applicability')] == ['placement_compatibility']
    assert items_of(result, 'parameter') == []
    assert set(graph) == before and result['execution_readiness'] == 'not_verified'


def test_changed_parameter_reopens_applicability_without_requiring_valid_parameter_again():
    api()
    graph = ready_graph()
    graph.set((S.Perception_Current, S.threshold, Literal('0.71', datatype=XSD.string)))
    result = inspect(graph)
    assert result['status'] == 'needs_information'
    assert items_of(result, 'parameter') == []
    assert {item['field'] for item in items_of(result, 'applicability')} == {
        'grasp_compatibility', 'placement_compatibility'}


@pytest.mark.parametrize('case', ['missing', 'ambiguous'])
def test_task_choice_must_be_clear_before_knowledge_form_is_shown(case):
    api()
    graph = ready_graph()
    task = deepcopy(TASK)
    if case == 'missing':
        task['part'] = None
    else:
        graph.add((S.AnotherPart, RDF.type, S.Workpiece))
        graph.add((S.AnotherPart, RDFS.label, Literal(task['part'])))
    result = inspect(graph, task)
    assert result['status'] == 'needs_clarification'
    assert result['items'] == []


def test_unsupported_task_does_not_open_irrelevant_completion_forms():
    api()
    result = inspect(ready_graph(), {**TASK, 'unsupported_requests': ['重排技能步骤']})
    assert result['status'] == 'unsupported' and result['items'] == []


def test_same_gap_has_stable_identity_and_reinspection_after_fill_removes_it():
    api()
    graph = ready_graph()
    graph.remove((S.Perception_Current, S.exposureTime, None))
    first, second = inspect(graph), inspect(deepcopy(graph))
    assert first['items'] == second['items']
    ids = [item['id'] for item in first['items']]
    assert len(ids) == len(set(ids))
    # Fixture mutation models a newly saved snapshot; actual save is covered in T04.
    graph.set((S.Perception_Current, S.exposureTime, Literal(config()['Perception']['exposure_time'], datatype=XSD.string)))
    after = inspect(graph)
    assert items_of(after, 'parameter') == []
    assert after['status'] == 'ready'

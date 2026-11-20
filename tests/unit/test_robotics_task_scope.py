"""Final task configuration defines generation scope, publication remains global — DR-170."""
from copy import deepcopy
import hashlib

import pytest
from rdflib import RDF, Literal, XSD

from domain_runs.robotics_task_service import TaskService
from domains.robotics import consumer, evidence, generation
from domains.robotics.graph import S, ONTOLOGY
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_governance_support import NOW, TASK
from tests.robotics_task_support import (fixture, model, start, message, complete, synthetic_answers,
    assert_generated, assert_no_result, graph_of)
from tests.robotics_support import config


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)


@pytest.mark.parametrize('field,kind,original,parameter', [
    ('hasPerceptionProfile', 'PerceptionProfile', S.Perception_Current, 'objectName'),
    ('hasPlacementProfile', 'PlacementProfile', S.Placement_Current, None),
])
def test_switching_back_from_partly_saved_configuration_uses_final_selection_only(tmp_path, field, kind, original, parameter):
    root = tmp_path / 'assets'; assets, version, _ = fixture(root, ready=True)
    client, calls = model(); service = TaskService(root, model_client=client, generation_policy='offline')
    current = message(service, start(service, version)); assert_generated(root, current)
    original_payload = assets.read_payload(current['ontology_id'], current['version_id'])
    changed = service.configure(current['session_id'], field=field, create={'kind': kind, 'label': '临时未完成配置'},
        expected_revision=current['revision'], request_id='temporary-configuration')
    assert changed['status'] == 'needs_information'; assert_no_result(changed)
    draft = graph_of(root, changed).value(S.PartType_A if parameter else S.Slot_B_01, S[field])
    if parameter:
        item = next(g for g in changed['gaps'] if g['field'] == parameter)
        changed = complete(service, changed, [{'gap_id': item['id'], 'value': 'temporary-object'}], request_id='partial')
        assert changed['status'] == 'needs_information'; assert_no_result(changed)
    draft_payload = assets.read_payload(changed['ontology_id'], changed['version_id'])
    current = service.configure(changed['session_id'], field=field, target=str(original),
        expected_revision=changed['revision'], request_id='back-to-original')
    for index in range(4):
        if current['status'] == 'generated':
            break
        assert current['status'] == 'needs_information' and current['gaps']
        assert all(g['entity_iri'] != str(draft) for g in current['gaps'])
        current = complete(service, current, synthetic_answers(current), request_id='final-'+str(index))
    assert_generated(root, current)
    graph = graph_of(root, current)
    assert (draft, RDF.type, S[kind]) in graph, 'Unused drafts must stay in the saved ontology'
    assert graph.value(draft, S.objectName) == (Literal('temporary-object', datatype=XSD.string) if parameter else None)
    assert graph.value(draft, S.exposureTime) is None
    assert assets.read_payload(version['ontology_id'], version['version_id']) == original_payload
    assert draft_payload and len(calls.calls) == 1
    before = db_rows(root), blob_bytes(root)
    assert service.get(current['session_id'])['candidate'] == current['candidate']
    assert (db_rows(root), blob_bytes(root)) == before
    with pytest.raises(ValueError):
        assets.load_robotics_application(current['ontology_id'], current['version_id'], mode='real')
    assert current['result']['validation']['validation_scope']['kind'] == 'task_configuration'


def task_context(tmp_path, mutate=None):
    assets, version, _ = fixture(tmp_path / 'assets', ready=True, mutate=mutate)
    context = assets.load_robotics_application(version['ontology_id'], version['version_id'], task=TASK)
    return assets, version, context


@pytest.mark.parametrize('fault', ['contract', 'shapes', 'imports', 'execution', 'shared_value'])
def test_task_scope_retains_trust_execution_and_selected_dependency_validation(tmp_path, fault):
    from rdflib import OWL
    def mutate(graph):
        graph.add((S.UnusedDraft, RDF.type, S.PerceptionProfile))
        if fault == 'contract': graph.set((ONTOLOGY, S.artifactContract, Literal('invalid')))
        elif fault == 'shapes': graph.set((ONTOLOGY, S.validationShapes, Literal('invalid')))
        elif fault == 'imports': graph.add((ONTOLOGY, OWL.imports, S.UnusedDraft))
        elif fault == 'execution':
            call = next(graph.subjects(RDF.type, S.SkillInvocation)); graph.remove((call, S.sourcePath, None))
        else:
            joint = graph.value(S.Perception_Current, S.hasCaptureJoints)
            graph.add((S.UnusedDraft, S.hasCaptureJoints, joint)); graph.remove((joint, S.rawValue, None))
    with pytest.raises(ValueError):
        task_context(tmp_path, mutate)


def test_task_scope_preserves_full_snapshot_hash_and_rejects_another_task(tmp_path):
    def mutate(graph): graph.add((S.UnusedDraft, RDF.type, S.PerceptionProfile))
    assets, version, context = task_context(tmp_path, mutate)
    original = assets.read_payload(version['ontology_id'], version['version_id'])
    assert context['validation']['ontology_sha256'] == hashlib.sha256(original).hexdigest()
    assert (S.UnusedDraft, RDF.type, S.PerceptionProfile) in context['graph']
    before = set(context['graph'])
    changed_task = {**TASK, 'part': 'other-part'}
    result = generation.generate_task(context, changed_task, output_dir=tmp_path/'wrong')
    assert result['status'] == 'error' and result['issues'][0]['code'] == 'task_scope_mismatch'
    assert not result['candidate_written'] and set(context['graph']) == before
    result = generation.generate_task(context, deepcopy(TASK), output_dir=tmp_path/'correct', purpose='offline')
    assert result['status'] == 'generated' and result['validation']['symbolic_validation']['valid']
    assert result['grounding']['config'] == config()
    with pytest.raises(ValueError): consumer.validate_graph(context['graph'])

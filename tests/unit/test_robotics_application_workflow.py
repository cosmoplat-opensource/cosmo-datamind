"""T04 RED: genuine task completion, synchronized artifacts and source-independent execution — DR-072."""
from copy import deepcopy
import json
import xml.etree.ElementTree as ET

import pytest
from rdflib import Graph, RDF, Literal

from domain_runs import RunStore
from domain_runs.robotics_task_service import TaskService
from ontology_assets import AssetStore
from tests.build_pipeline_support import db_rows, blob_bytes
from tests.robotics_modeling_support import no_network
from tests.robotics_support import S, config, trees
from tests.robotics_task_support import assert_generated, model, fixture as legacy_fixture, message as legacy_message, start as legacy_start
from tests.robotics_application_integration_support import (
    service, adapted, current_bundle, archive_xml, fill_task, answers, begin, confirm, submit, finish, expect_error,
    restore_task_record,
)

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def checkpoint(root, state, snapshots):
    ref = {k: state[k] for k in ('ontology_id', 'version_id')}
    raw, document = current_bundle(root, ref)
    assert document['origin']['asset_ref']['ontology_id'] == state['ontology_id']
    assert document['execution_readiness'] == 'not_verified'
    snapshots[ref['version_id']] = (raw, json.dumps(document, sort_keys=True))
    if state['status'] != 'generated':
        assert state['candidate'] is None and state['run_id'] is None


def test_real_business_choices_and_confirmations_generate_without_recreating_old_examples(tmp_path):
    service(tmp_path); h, original, completed = adapted(tmp_path)
    ref = completed['output_asset_ref']; snapshots = {}
    state, transport = fill_task(tmp_path, ref, check_each=lambda s: checkpoint(tmp_path, s, snapshots))
    assert_generated(tmp_path, state)
    assert len(transport.calls) == 1 and len(h.runtime.calls) == 2
    assert len(snapshots) > 1 and state['version_id'] != ref['version_id']
    graph = Graph().parse(data=AssetStore(tmp_path).read_payload(state['ontology_id'], state['version_id']), format='xml')
    for field, kind in [('part', 'Workpiece'), ('station', 'Workstation'), ('carrier', 'Carrier'), ('slot', 'PlacementSlot')]:
        assert list(graph.subjects(RDF.type, S[kind]))
        assert state['task'][field]
    for vid, (raw, encoded) in snapshots.items():
        old_raw, old_doc = current_bundle(tmp_path, {'ontology_id': state['ontology_id'], 'version_id': vid})
        assert old_raw == raw and json.dumps(old_doc, sort_keys=True) == encoded
    assert AssetStore(tmp_path).get_version(**original['output_asset_ref'])['publication_state'] == 'candidate'


def test_replacing_perception_configuration_updates_total_json_and_xml_with_original_compiler(tmp_path):
    service(tmp_path); _, _, completed = adapted(tmp_path); ref = completed['output_asset_ref']
    initial, _ = fill_task(tmp_path, ref); original = current_bundle(tmp_path, {k: initial[k] for k in ('ontology_id', 'version_id')})
    tasks = TaskService(tmp_path, generation_policy='offline')
    state = tasks.configure(initial['session_id'], field='hasPerceptionProfile',
        create={'kind': 'PerceptionProfile', 'label': '合成测试新感知配置'},
        expected_revision=initial['revision'], request_id='replace-profile')
    for index in range(10):
        current_bundle(tmp_path, {k: state[k] for k in ('ontology_id', 'version_id')})
        if state['status'] == 'generated': break
        assert state['status'] == 'needs_information', state
        graph = Graph().parse(data=AssetStore(tmp_path).read_payload(state['ontology_id'], state['version_id']), format='xml')
        selected = answers(state, graph)
        for answer in selected:
            gap = next(g for g in state['gaps'] if g['id'] == answer['gap_id'])
            if gap['kind'] == 'parameter' and gap['field'] == 'threshold': answer['value'] = '0.91'
        state = tasks.complete(state['session_id'], answers=selected, expected_revision=state['revision'], request_id=f'profile-fill-{index}')
    values = config(); values['Perception']['threshold'] = '0.91'
    raw = assert_generated(tmp_path, state, values=values)
    graph = Graph().parse(data=AssetStore(tmp_path).read_payload(state['ontology_id'], state['version_id']), format='xml')
    selected_profiles = {o for _, _, o in graph.triples((None, S.hasPerceptionProfile, None))}
    assert len(selected_profiles) == 1
    assert str(graph.value(next(iter(selected_profiles)), S.threshold)) == '0.91'
    document = current_bundle(tmp_path, {k: state[k] for k in ('ontology_id', 'version_id')})[1]
    assert document['origin']['design_snapshot']['abox'] == original[1]['origin']['design_snapshot']['abox']
    # Compare to the real legacy task path with the same explicit six values.
    _, old_version, _ = legacy_fixture(tmp_path / 'old', ready=True,
        mutate=lambda g: g.set((S.Perception_Current, S.threshold, Literal('0.91'))))
    client, _ = model(); old_tasks = TaskService(tmp_path / 'old', model_client=client, generation_policy='offline')
    old = legacy_message(old_tasks, legacy_start(old_tasks, old_version))
    assert_generated(tmp_path / 'old', old, values=values)
    old_artifact = old['candidate']
    assert trees(ET.fromstring(raw)) == trees(ET.fromstring(RunStore(tmp_path / 'old').read_artifact(**old_artifact)))


@pytest.mark.parametrize('phase', ['before_task', 'after_completion'])
def test_archived_original_xml_is_not_needed_for_prepare_complete_generate_or_history(tmp_path, phase):
    service(tmp_path); _, original, completed = adapted(tmp_path); ref = completed['output_asset_ref']
    if phase == 'before_task': archive_xml(tmp_path, original['output_asset_ref'])
    state, transport = fill_task(tmp_path, ref)
    expected = assert_generated(tmp_path, state)
    if phase == 'after_completion': archive_xml(tmp_path, original['output_asset_ref'])
    task_service = TaskService(tmp_path, generation_policy='offline')
    assert task_service.get(state['session_id']) == state
    continued = task_service.generate(state['session_id'], expected_revision=state['revision'], request_id='generate-again')
    assert assert_generated(tmp_path, continued) == expected
    raw, _ = current_bundle(tmp_path, {k: continued[k] for k in ('ontology_id', 'version_id')})
    assert raw and len(transport.calls) == 1
    from domain_runs.robotics_task_history import TaskHistory
    history = TaskHistory(tmp_path).get(state['session_id'], state['run_id'])
    assert history['run_id'] == state['run_id']
    assert TaskHistory(tmp_path).candidate(state['session_id'], state['run_id']) == expected


def test_strict_mode_remains_blocked_without_applicability_evidence(tmp_path):
    service(tmp_path); _, _, completed = adapted(tmp_path)
    state, _ = fill_task(tmp_path, completed['output_asset_ref'], policy='strict', supply_evidence=False)
    assert state['status'] == 'needs_information' and any(g['kind'] == 'applicability' for g in state['gaps'])
    assert state['candidate'] is None
    tasks = TaskService(tmp_path, generation_policy='strict')
    result = tasks.generate(state['session_id'], expected_revision=state['revision'], request_id='no-evidence')
    assert result['status'] != 'generated' and result['candidate'] is None
    current_bundle(tmp_path, {k: result[k] for k in ('ontology_id', 'version_id')})


def test_explicit_synthetic_evidence_can_complete_strict_software_flow_without_claiming_field_execution(tmp_path):
    service(tmp_path); _, _, completed = adapted(tmp_path)
    state, transport = fill_task(tmp_path, completed['output_asset_ref'], policy='strict', supply_evidence=True)
    assert_generated(tmp_path, state)
    assert state['execution_readiness'] == 'not_verified' and len(transport.calls) == 1
    _, document = current_bundle(tmp_path, {k: state[k] for k in ('ontology_id', 'version_id')})
    assert document['execution_readiness'] == 'not_verified'


def test_return_to_original_task_uses_cas_and_explicit_new_version_binding(tmp_path):
    service(tmp_path); _, original, completed = adapted(tmp_path)
    current, _ = fill_task(tmp_path, completed['output_asset_ref'])
    tasks = TaskService(tmp_path, generation_policy='offline'); before = deepcopy(tasks.get(current['session_id']))
    # Explicitly adapt the original design as a new branch, preserving the requesting task.
    flow = begin(tmp_path, original['output_asset_ref'], request_id='re-adapt-for-task',
                 task_ref={'session_id': current['session_id']})
    after = finish(tmp_path, submit(tmp_path, confirm(tmp_path, flow, request_id='confirm-new-branch'), request_id='submit-new-branch'))
    assert restore_task_record(tmp_path, current) == before
    resumed = service(tmp_path).resume_task(after['adaptation_id'], expected_revision=after['revision'],
        expected_task_revision=current['revision'], request_id='resume-original')
    assert resumed['session_id'] == current['session_id']
    assert resumed['version_id'] == after['output_asset_ref']['version_id']
    assert resumed['candidate'] is None and resumed['revision'] > current['revision']
    before_replay = db_rows(tmp_path)
    replay = service(tmp_path).resume_task(after['adaptation_id'], expected_revision=after['revision'],
        expected_task_revision=current['revision'], request_id='resume-original')
    assert replay == resumed and db_rows(tmp_path) == before_replay


def test_original_task_revision_change_blocks_late_return(tmp_path):
    service(tmp_path); _, original, completed = adapted(tmp_path)
    current, _ = fill_task(tmp_path, completed['output_asset_ref'])
    flow = begin(tmp_path, original['output_asset_ref'], request_id='prepare-new-branch', task_ref={'session_id': current['session_id']})
    after = finish(tmp_path, submit(tmp_path, confirm(tmp_path, flow), request_id='new-branch-run'))
    task_service = TaskService(tmp_path, generation_policy='offline')
    newer = task_service.generate(current['session_id'], expected_revision=current['revision'], request_id='concurrent-task-edit')
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    expect_error('session_conflict', lambda: service(tmp_path).resume_task(after['adaptation_id'],
        expected_revision=after['revision'], expected_task_revision=current['revision'], request_id='late-return'))
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files
    assert restore_task_record(tmp_path, newer) == newer


def test_failed_completion_does_not_leave_new_version_or_half_json(tmp_path):
    service(tmp_path); _, _, completed = adapted(tmp_path)
    current, _ = fill_task(tmp_path, completed['output_asset_ref'])
    tasks = TaskService(tmp_path, generation_policy='offline')
    state = tasks.configure(current['session_id'], field='hasPerceptionProfile',
        create={'kind': 'PerceptionProfile', 'label': '合成测试待填配置'}, expected_revision=current['revision'], request_id='new-empty-profile')
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    from domain_runs.robotics_task_service import TaskError
    with pytest.raises(TaskError):
        tasks.complete(state['session_id'], answers=[{'gap_id': 'forged-gap', 'value': '1'}],
            expected_revision=state['revision'], request_id='invalid-completion')
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files
    current_bundle(tmp_path, {k: state[k] for k in ('ontology_id', 'version_id')})


def test_real_design_cannot_be_silently_reinterpreted_as_demo_during_adaptation(tmp_path):
    service(tmp_path)
    from tests.robotics_application_integration_support import candidate
    _, _, ref = candidate(tmp_path); before = db_rows(tmp_path)
    expect_error('mode_conflict', lambda: begin(tmp_path, ref, mode='demo'))
    assert db_rows(tmp_path) == before


def test_existing_legacy_demo_rejects_real_task_without_model_calls(tmp_path):
    # Existing mode guard is a genuine baseline independent of new service availability.
    from domain_runs.robotics_task_demo import build_example
    version = build_example(tmp_path, mode='demo')
    client, transport = model(); tasks = TaskService(tmp_path, model_client=client, generation_policy='offline')
    before = db_rows(tmp_path)
    from domain_runs.robotics_task_service import TaskError
    with pytest.raises(TaskError): tasks.start(ontology_id=version['ontology_id'], version_id=version['version_id'], mode='real', request_id='mixed-mode')
    assert db_rows(tmp_path) == before and transport.calls == []
    allowed = tasks.start(ontology_id=version['ontology_id'], version_id=version['version_id'], mode='demo', request_id='correct-demo')
    assert allowed['status'] == 'idle' and allowed['mode'] == 'demo' and transport.calls == []

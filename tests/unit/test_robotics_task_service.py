"""T04 RED: candidate consumption, persisted dialogue and scoped atomic completion — DR-061 / DR-064 / DR-066."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from rdflib import Literal, RDF, RDFS, XSD

from domain_runs import RunStore
from domains.robotics import evidence
from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService
from ontology_assets.storage import Storage
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_governance_support import NOW, TASK
from tests.robotics_support import S, config
from tests.robotics_task_support import (BASIS, EXPIRY, TEXT, assert_generated, assert_no_result, complete,
    finish_information, fixture, gap, graph_of, message, model, start, synthetic_answers, task_api)
from tests.unit.test_robotics_task_model import envelope


@pytest.fixture(autouse=True)
def synthetic_clock(monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)


def setup(tmp_path, *, ready=False, mutate=None, responses=()):
    api = task_api()
    root = tmp_path / 'assets'
    assets, version, build_run = fixture(root, ready=ready, mutate=mutate)
    client, transport = model(*responses)
    service = api.TaskService(root, model_client=client)
    session = start(service, version)
    return root, assets, version, build_run, service, session, transport


def test_candidate_application_loader_checks_structure_without_publishing(tmp_path):
    assets, version, _ = fixture(tmp_path / 'assets')
    loader = getattr(assets, 'load_robotics_application', None)
    assert callable(loader), 'T05 must add an application snapshot loader, retaining published-only load_robotics'
    before = db_rows(tmp_path / 'assets')
    context = loader(version['ontology_id'], version['version_id'], mode='real')
    assert context['validation']['ontology_sha256'] == version['payload_sha256']
    assert db_rows(tmp_path / 'assets') == before
    with pytest.raises(ValueError, match='发布'):
        assets.load_robotics(version['ontology_id'], version['version_id'], mode='real')


@pytest.mark.parametrize('damage', ['bytes', 'contract', 'mode'])
def test_application_loader_rejects_tampered_or_wrong_mode_snapshot(tmp_path, damage):
    task_api()
    assets, version, _ = fixture(tmp_path / 'assets')
    if damage == 'bytes':
        (tmp_path / 'assets' / version['path']).write_bytes(b'<corrupt/>')
    elif damage == 'contract':
        # Valid RDF storage is not automatically a valid robotics consumption contract.
        version = assets.add_version(version['ontology_id'], b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',
                                     payload_kind='robotics_rdf')
    with pytest.raises(ValueError):
        assets.load_robotics_application(version['ontology_id'], version['version_id'],
                                         mode='demo' if damage == 'mode' else 'real')


def test_complete_candidate_generates_real_xml_matching_independent_frozen_oracle(tmp_path):
    root, assets, version, _, service, session, transport = setup(tmp_path, ready=True)
    result = message(service, session)
    assert_generated(root, result)
    assert assets.get_version(version['ontology_id'], version['version_id'])['publication_state'] == 'candidate'
    assert len(transport.calls) == 1
    assert 'synthetic-secret' not in json.dumps(db_rows(root))


def test_fresh_build_completes_in_stages_and_compiles_saved_owl_without_demo_bypass(tmp_path):
    root, assets, version, build_run, service, session, transport = setup(tmp_path)
    original = assets.read_payload(version['ontology_id'], version['version_id'])
    current = message(service, session)
    assert current['status'] == 'needs_information'
    assert {g['field'] for g in current['gaps'] if g['kind'] == 'mapping'} == {
        'hasPerceptionProfile', 'hasPlacementProfile'}
    assert not [g for g in current['gaps'] if g['kind'] in ('parameter', 'applicability')]
    final = finish_information(service, current)
    assert final['session_id'] == session['session_id'] and final['version_id'] != version['version_id']
    assert final['task'] == current['task'] and final['messages'][:len(current['messages'])] == current['messages']
    assert_generated(root, final)
    graph = graph_of(root, final)
    assert (S.PartType_A, S.hasPerceptionProfile, S.Perception_Current) in graph
    assert (S.Slot_B_01, S.hasPlacementProfile, S.Placement_Current) in graph
    assert evidence.policy(graph) == evidence.POLICY
    subjects = list(graph.subjects(RDF.type, S.ReadinessEvidence))
    assert len(subjects) == 2
    for subject in subjects:
        assert evidence.evaluate_evidence(graph, str(subject), now=NOW)['valid']
        assert str(graph.value(subject, S.evidenceReference)) == BASIS
    assert not list(graph.triples((None, S.confirmationStatus, Literal('simulated', datatype=XSD.string))))
    assert assets.read_payload(version['ontology_id'], version['version_id']) == original
    assert RunStore(root).get_run(build_run['run_id']) == build_run
    assert all(v['publication_state'] == 'candidate' for v in assets.list_versions(version['ontology_id']))
    assert len(transport.calls) == 1, 'Completion is deterministic, not another LLM extraction'


@pytest.mark.parametrize('field,owner,value', [
    ('exposureTime', 'perception', '155'), ('rawValue', 'pose', '[200,-900,500,1,0,0,0]')])
def test_only_missing_parameter_is_saved_and_changes_exact_public_xml_slot(tmp_path, field, owner, value):
    def mutate(graph):
        entity = S.Perception_Current if owner == 'perception' else graph.value(S.Placement_Current, S.hasTargetPose)
        graph.remove((entity, S[field], None))
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True, mutate=mutate)
    current = message(service, session)
    items = [g for g in current['gaps'] if g['kind'] == 'parameter']
    assert len(items) == 1 and items[0]['field'] == field
    after = complete(service, current, [{'gap_id': items[0]['id'], 'value': value}])
    assert_no_result(after)
    assert not [g for g in after['gaps'] if g['kind'] == 'parameter']
    assert any(g['kind'] == 'confirmation' for g in after['gaps']), 'Saving a value cannot confirm it'
    assert any(g['kind'] == 'applicability' for g in after['gaps']), 'Changed configuration invalidates scope evidence'
    assert str(graph_of(root, after).value(S[items[0]['entity_iri'].split('#')[-1]], S[field])) == value
    final = finish_information(service, after)
    values = config()
    values['Perception' if owner == 'perception' else 'Place'][
        'exposure_time' if owner == 'perception' else 'place_pose'] = value
    assert_generated(root, final, values=values)


@pytest.mark.parametrize('value', ['', 'NaN', '-1', '{exposure}'])
def test_invalid_parameter_rejects_entire_batch_and_keeps_session(tmp_path, value):
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True,
        mutate=lambda g: g.remove((S.Perception_Current, S.exposureTime, None)))
    current = message(service, session)
    before = db_rows(root), blob_bytes(root)
    with pytest.raises(task_api().TaskError) as caught:
        complete(service, current, [{'gap_id': gap(current, 'exposureTime')['id'], 'value': value}])
    assert caught.value.code == 'invalid_answer'
    assert (db_rows(root), blob_bytes(root)) == before
    assert service.get(current['session_id']) == current


def test_selecting_mapping_preserves_sibling_carriers_slots_and_unrelated_semantics(tmp_path):
    def mutate(graph):
        graph.remove((S.Station_B, S.hasCarrier, S.Carrier_B_01))
        graph.add((S.SiblingCarrier, RDF.type, S.Carrier))
        graph.add((S.Station_B, S.hasCarrier, S.SiblingCarrier))
        graph.add((S.SiblingSlot, RDF.type, S.PlacementSlot))
        graph.add((S.Carrier_B_01, S.hasSlot, S.SiblingSlot))
        graph.add((S.Unrelated, RDFS.comment, Literal('keep me')))
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True, mutate=mutate)
    current = message(service, session)
    item = gap(current, 'hasCarrier')
    after = complete(service, current, [{'gap_id': item['id'], 'target': str(S.Carrier_B_01)}])
    graph = graph_of(root, after)
    assert set(graph.objects(S.Station_B, S.hasCarrier)) == {S.Carrier_B_01, S.SiblingCarrier}
    assert set(graph.objects(S.Carrier_B_01, S.hasSlot)) == {S.Slot_B_01, S.SiblingSlot}
    assert (S.Unrelated, RDFS.comment, Literal('keep me')) in graph


@pytest.mark.parametrize('field,owner,kind', [
    ('hasPartType', S.Part_A_001, 'PartType'),
    ('hasCarrier', S.Station_B, 'Carrier'), ('hasSlot', S.Carrier_B_01, 'PlacementSlot'),
    ('hasPerceptionProfile', S.PartType_A, 'PerceptionProfile'),
    ('hasPlacementProfile', S.Slot_B_01, 'PlacementProfile'),
    ('hasCaptureJoints', S.Perception_Current, 'JointConfiguration'),
    ('hasTargetPose', S.Placement_Current, 'CartesianPose')])
def test_inline_creation_assigns_identity_and_does_not_merge_same_name(tmp_path, field, owner, kind):
    def mutate(graph):
        graph.remove((owner, S[field], None))
        graph.add((S.SameName, RDF.type, S[kind]))
        graph.add((S.SameName, RDFS.label, Literal('新测试对象')))
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True, mutate=mutate)
    current = message(service, session)
    answers = [{'gap_id': gap(current, field)['id'], 'create': {'kind': kind, 'label': '新测试对象'}}]
    after = complete(service, current, answers)
    graph = graph_of(root, after)
    target = graph.value(owner, S[field])
    assert target != S.SameName and (target, RDF.type, S[kind]) in graph
    assert str(graph.value(target, RDFS.label)) == '新测试对象'
    assert (S.SameName, RDF.type, S[kind]) in graph
    assert str(graph.value(target, S.confirmationStatus)) != 'confirmed'
    if field in ('hasCarrier', 'hasSlot'):
        assert after['task']['carrier' if field == 'hasCarrier' else 'slot'] == str(target)
    assert_no_result(after)
    # Replaying a request returns the same new identity/version, despite an older revision.
    count = len(AssetStore(root).list_versions(after['ontology_id']))
    assert complete(service, current, answers) == after
    assert len(AssetStore(root).list_versions(after['ontology_id'])) == count


def test_unknown_task_entity_needs_explicit_create_intent(tmp_path):
    root, _, _, _, service, session, _ = setup(tmp_path, responses=(envelope({**TASK, 'part': '新零件'}),))
    current = message(service, session, text='生成把新零件放到工位B载具B01放置位B01的行为树')
    assert current['status'] == 'needs_clarification'
    assert not list(graph_of(root, current).subjects(RDFS.label, Literal('新零件')))
    after = service.choose(current['session_id'], field='part', create={'kind': 'Workpiece', 'label': '新零件'},
        expected_revision=current['revision'], request_id='explicit-create')
    graph = graph_of(root, after)
    created = list(graph.subjects(RDFS.label, Literal('新零件')))
    assert len(created) == 1 and (created[0], RDF.type, S.Workpiece) in graph
    assert after['task']['part'] == str(created[0])
    assert any(g['field'] == 'hasPartType' for g in after['gaps'])
    assert_no_result(after)


def test_new_perception_configuration_is_completed_then_used_by_actual_compiler(tmp_path):
    root, _, _, _, service, session, _ = setup(tmp_path)
    current = message(service, session)
    after = complete(service, current, [{'gap_id': gap(current, 'hasPerceptionProfile')['id'],
        'create': {'kind': 'PerceptionProfile', 'label': '新任务感知配置'}}])
    created = graph_of(root, after).value(S.PartType_A, S.hasPerceptionProfile)
    assert created != S.Perception_Current
    final = finish_information(service, after)
    assert graph_of(root, final).value(S.PartType_A, S.hasPerceptionProfile) == created
    assert_generated(root, final)


@pytest.mark.parametrize('bad_create', [
    {'kind': 'SkillImplementation', 'label': '不支持的技能'},
    {'kind': 'PerceptionProfile', 'label': ''},
    {'kind': 'PerceptionProfile', 'label': '配置', 'iri': str(S.Perception_Current)},
    {'kind': 'PerceptionProfile', 'label': '配置', 'confirmationStatus': 'confirmed'}])
def test_inline_creation_cannot_rewrite_protected_identity_or_self_confirm(tmp_path, bad_create):
    root, _, _, _, service, session, _ = setup(tmp_path)
    current = message(service, session)
    before = db_rows(root), blob_bytes(root)
    with pytest.raises(task_api().TaskError):
        complete(service, current, [{'gap_id': gap(current, 'hasPerceptionProfile')['id'], 'create': bad_create}])
    assert (db_rows(root), blob_bytes(root)) == before


@pytest.mark.parametrize('attack', ['unrelated', 'protected', 'wrong_type', 'client_scope', 'duplicate', 'mixed_invalid'])
def test_gap_ids_are_not_permission_to_edit_arbitrary_knowledge(tmp_path, attack):
    root, _, _, _, service, session, _ = setup(tmp_path)
    current = message(service, session)
    answer = {'gap_id': gap(current, 'hasPerceptionProfile')['id'], 'target': str(S.Perception_Current)}
    answers = [answer]
    if attack == 'unrelated': answer['gap_id'] = 'gap_for_another_task'
    elif attack == 'protected': answer.update(field='rawXML', value='<root/>')
    elif attack == 'wrong_type': answer['target'] = str(S.Slot_B_01)
    elif attack == 'client_scope': answer['entity_iri'] = str(S.Unrelated)
    elif attack == 'duplicate': answers.append(deepcopy(answer))
    else: answers.append({'gap_id': 'nonexistent', 'value': 'bad'})
    before = db_rows(root), blob_bytes(root)
    with pytest.raises(task_api().TaskError): complete(service, current, answers)
    assert (db_rows(root), blob_bytes(root)) == before
    assert service.get(current['session_id']) == current


@pytest.mark.parametrize('case', ['unconfirmed', 'blank_basis', 'expired', 'wrong_scope'])
def test_invalid_or_unconfirmed_applicability_does_not_allow_candidate(tmp_path, case):
    def mutate(graph):
        graph.remove((S.TaskCompletionEvidence1, None, None))
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True, mutate=mutate)
    current = message(service, session)
    answer = {'gap_id': gap(current, 'placement_compatibility')['id'],
              'reference': BASIS, 'valid_until': EXPIRY, 'confirmed': True}
    if case == 'unconfirmed': answer['confirmed'] = False
    elif case == 'blank_basis': answer['reference'] = '  '
    elif case == 'expired': answer['valid_until'] = '2000-01-01T00:00:00+00:00'
    else: answer['scope'] = {'slot': str(S.AnotherSlot)}
    if case == 'unconfirmed':
        after = complete(service, current, [answer])
        assert after['status'] == 'needs_information'
        assert gap(after, 'placement_compatibility')
    else:
        before = db_rows(root), blob_bytes(root)
        with pytest.raises(task_api().TaskError): complete(service, current, [answer])
        assert (db_rows(root), blob_bytes(root)) == before
        after = service.get(current['session_id'])
    assert_no_result(after)


def test_chat_confirmation_never_accepts_knowledge_or_applicability(tmp_path):
    root, _, _, _, service, session, _ = setup(tmp_path, responses=(envelope(TASK), envelope(TASK)))
    current = message(service, session)
    original = AssetStore(root).read_payload(current['ontology_id'], current['version_id'])
    after = message(service, current, text='确认，直接生成吧', request_id='confirm-chat')
    assert after['status'] == 'needs_information' and after['version_id'] == current['version_id']
    assert AssetStore(root).read_payload(after['ontology_id'], after['version_id']) == original
    assert_no_result(after)


def test_task_change_rejects_old_gap_form_and_removes_old_download(tmp_path):
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True,
        responses=(envelope(TASK), envelope({**TASK, 'slot': '未知目标'})))
    first = message(service, session)
    assert_generated(root, first)
    after = message(service, first, text='改成未知目标', request_id='change-target')
    assert after['status'] == 'needs_clarification'
    assert_no_result(after)
    with pytest.raises(task_api().TaskError) as caught:
        service.read_candidate(after['session_id'], **first['candidate'], expected_revision=first['revision'])
    assert caught.value.code == 'stale_result'
    with pytest.raises(task_api().TaskError) as caught:
        complete(service, first, [{'gap_id': 'old-gap', 'value': '1'}])
    assert caught.value.code == 'session_conflict'
    # The previous immutable run remains historical evidence, but is not this task's active download.
    assert_generated(root, first)


def test_model_error_clears_active_result_and_persists_error_without_secrets(tmp_path):
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True,
        responses=(envelope(TASK), TimeoutError('synthetic-secret')))
    first = message(service, session)
    assert_generated(root, first)
    result = message(service, first, text='再生成一次', request_id='failed-model')
    assert result['status'] == 'error' and result['error']['code'] == 'model_timeout'
    assert_no_result(result)
    assert service.get(result['session_id']) == result
    assert 'synthetic-secret' not in json.dumps(db_rows(root))


def test_dialogue_clarifies_then_generates_only_explicit_user_selection(tmp_path):
    root, _, _, _, service, session, transport = setup(tmp_path, ready=True,
        responses=(envelope(TASK), envelope(TASK)))
    first = message(service, session, text='生成把零部件A放到工位B的行为树')
    assert first['status'] == 'needs_clarification'
    assert_no_result(first)
    assert first['task']['slot'] is None
    result = message(service, first, text='选择载具B01的放置位B01', request_id='explicit-choice')
    assert_generated(root, result)
    sent = transport.calls[-1]['payload']['messages']
    assert any(m['role'] == 'assistant' for m in sent)
    assert any(m['role'] == 'user' and m['content'] == '选择载具B01的放置位B01' for m in sent)


def test_session_and_result_survive_fresh_process_and_new_session_is_empty(tmp_path):
    root, _, version, _, service, session, _ = setup(tmp_path, ready=True)
    current = message(service, session)
    code = ('import json,sys; from domain_runs.robotics_task_service import TaskService; '
            'print(json.dumps(TaskService(sys.argv[1]).get(sys.argv[2]),ensure_ascii=False))')
    process = subprocess.run([sys.executable, '-c', code, str(root), current['session_id']],
        cwd=Path(__file__).resolve().parents[2], env={**os.environ, 'DATAMIND_WORKDIR': str(tmp_path / 'isolated')},
        text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == current
    fresh = start(service, version, request_id='new-task')
    assert fresh['session_id'] != current['session_id'] and fresh['messages'] == []
    assert_no_result(fresh)
    assert_generated(root, current)


def test_save_failure_rolls_back_version_and_session_and_same_request_can_retry(tmp_path, monkeypatch):
    root, assets, version, _, service, session, _ = setup(tmp_path)
    current = message(service, session)
    answers = [{'gap_id': gap(current, 'hasPerceptionProfile')['id'], 'target': str(S.Perception_Current)}]
    before = db_rows(root), blob_bytes(root)
    original = Storage.write_bytes
    def fail_owl(self, relative, data):
        if str(relative).endswith('.owl'): raise OSError('synthetic full disk')
        return original(self, relative, data)
    with monkeypatch.context() as patch:
        patch.setattr(Storage, 'write_bytes', fail_owl)
        with pytest.raises(task_api().TaskError) as caught: complete(service, current, answers)
        assert caught.value.code == 'save_failed'
    assert (db_rows(root), blob_bytes(root)) == before
    assert service.get(current['session_id']) == current
    after = complete(service, current, answers)
    assert after['version_id'] != current['version_id']
    assert len(assets.list_versions(version['ontology_id'])) == 2


def test_revoked_evidence_is_rechecked_before_new_generation(tmp_path):
    root, _, _, _, service, session, _ = setup(tmp_path, ready=True,
        responses=(envelope(TASK), envelope(TASK)))
    first = message(service, session)
    assert_generated(root, first)
    GovernanceService(root).revoke_evidence(first['ontology_id'], evidence_iri=str(S.TaskCompletionEvidence1),
        version_id=first['version_id'], actor='synthetic tester', reason=BASIS, request_id='revoke')
    after = message(service, first, text=TEXT, request_id='retry-after-revoke')
    assert after['status'] == 'needs_information'
    assert [g['field'] for g in after['gaps'] if g['kind'] == 'applicability'] == ['placement_compatibility']
    assert_no_result(after)


def test_repeated_message_is_idempotent_and_changed_reuse_conflicts(tmp_path):
    root, _, _, _, service, session, transport = setup(tmp_path, ready=True)
    first = message(service, session)
    before = db_rows(root), blob_bytes(root)
    assert message(service, session) == first
    assert len(transport.calls) == 1 and (db_rows(root), blob_bytes(root)) == before
    with pytest.raises(task_api().TaskError) as caught:
        message(service, session, text='换个任务')
    assert caught.value.code == 'request_conflict'


def test_completion_reports_honest_local_origin_not_authenticated_review(tmp_path):
    root, _, _, _, service, session, _ = setup(tmp_path)
    current = message(service, session)
    after = complete(service, current, synthetic_answers(current))
    version = AssetStore(root).get_version(after['ontology_id'], after['version_id'])
    origin = version['metadata']['task_completion']
    assert origin['session_id'] == current['session_id']
    assert origin['origin'] == 'local_session' and origin['authenticated_actor'] is False
    assert version['publication_state'] == 'candidate'


def test_initializing_task_storage_preserves_legacy_rows_and_immutable_files(tmp_path):
    api = task_api()
    from tests.robotics_governance_support import restore_v4
    root = tmp_path / 'legacy'
    restore_v4(root)
    # Bring this captured old store to the currently delivered v5 before adding task tables.
    AssetStore(root)
    before, files = db_rows(root), blob_bytes(root)
    api.TaskService(root)
    after = db_rows(root)
    assert all(after[name] == rows for name, rows in before.items())
    assert blob_bytes(root) == files


def test_preexisting_incomplete_candidate_can_be_inspected_but_not_strictly_consumed(tmp_path):
    root, assets, version, _, service, session, _ = setup(tmp_path, ready=True,
        mutate=lambda graph: graph.remove((S.Perception_Current, S.exposureTime, None)))
    with pytest.raises(ValueError):
        assets.load_robotics_application(version['ontology_id'], version['version_id'], mode='real')
    current = message(service, session)
    assert current['status'] == 'needs_information'
    assert gap(current, 'exposureTime')['kind'] == 'parameter'
    assert_no_result(current)
    assert not any(a['kind'] == 'candidate' for r in RunStore(root).list_runs()
                   for a in RunStore(root).artifacts(r['run_id']))


def test_corrupt_executable_structure_cannot_be_treated_as_fillable_parameter(tmp_path):
    def mutate(graph):
        graph.remove((S.grasp_Subtree_Impl, None, None))
    api = task_api()
    root = tmp_path / 'assets'
    assets, version, _ = fixture(root, ready=True, mutate=mutate)
    client, _ = model()
    service = api.TaskService(root, model_client=client)
    try:
        current = message(service, start(service, version))
    except api.TaskError as exc:
        assert exc.code == 'invalid_ontology'
    else:
        assert current['status'] == 'error' and current['error']['code'] == 'invalid_ontology'
        assert_no_result(current)
    assert assets.get_version(version['ontology_id'], version['version_id'])['publication_state'] == 'candidate'

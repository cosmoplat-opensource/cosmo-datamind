"""Automatic canonical matching and explicit manual exceptions — DR-072."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from application_contracts.adapters import service as check_service
from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.robotics_design import DesignAssets
from tests.build_pipeline_support import db_rows, blob_bytes
from tests.robotics_application_integration_support import (
    candidate, check_body, service, finish, current_bundle, archive_xml,
)
from tests.robotics_auto_adaptation_support import custom_candidate
from tests.build_runtime_support import BASE_URL, http_app


def repair_args(report):
    problem = next(i for i in report['issues'] if any(a['action_id'] == 'robotics.adapt_execution' for a in i['repair_actions']))
    action = next(a for a in problem['repair_actions'] if a['action_id'] == 'robotics.adapt_execution')
    return {'check_id': report['check_id'], 'issue_id': problem['issue_id'], 'action_id': action['action_id'],
        'input': {}, 'expected_revision': action['target']['expected_revision'], 'request_id': 'automatic-repair'}


def start(root, ref):
    checks = check_service(root)
    report = checks.evaluate(**check_body(ref))['report']
    reply = checks.repair(**repair_args(report))
    return service(root).get(reply['target']['resource_id']), report


def test_canonical_match_queues_on_explicit_repair_and_saves_automatic_provenance(tmp_path):
    h, original, ref = candidate(tmp_path)
    old_json = DesignAssets(tmp_path).export_json(**ref)
    old_owl = AssetStore(tmp_path).read_payload(**ref)
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    checks = check_service(tmp_path); report = checks.evaluate(**check_body(ref))['report']
    assert db_rows(tmp_path)['runs'] == before['runs'] and blob_bytes(tmp_path) == files
    args = repair_args(report); reply = checks.repair(**args)
    state = service(tmp_path).get(reply['target']['resource_id'])
    assert state['status'] == 'queued' and state['run_id'] and state['role_map']['confirmed'] is True
    assert state['matching']['method'] == 'automatic' and state['matching']['rule_version']
    assert all(b['iri'].startswith(original['namespace']) for b in state['role_map']['bindings'])
    assert len(db_rows(tmp_path)['runs']) == len(before['runs']) + 1
    replay = checks.repair(**args)
    assert replay['replayed'] and replay['target'] == reply['target']
    done = finish(tmp_path, state); _, document = current_bundle(tmp_path, done['output_asset_ref'])
    assert document['components']['mapping_method'] == 'automatic'
    assert DesignAssets(tmp_path).export_json(**ref) == old_json
    assert AssetStore(tmp_path).read_payload(**ref) == old_owl
    assert len(h.runtime.calls) == 2
    after, artifacts = db_rows(tmp_path), blob_bytes(tmp_path)
    assert service(tmp_path, read_only=True).get(state['adaptation_id']) == done
    assert db_rows(tmp_path) == after and blob_bytes(tmp_path) == artifacts


def test_concurrent_same_repair_creates_one_job(tmp_path):
    _, _, ref = candidate(tmp_path); checks = check_service(tmp_path)
    report = checks.evaluate(**check_body(ref))['report']; args = repair_args(report)
    before = len(RunStore(tmp_path).list_runs())
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda _: checks.repair(**args), range(3)))
    assert len({r['target']['resource_id'] for r in replies}) == 1
    assert len(RunStore(tmp_path).list_runs()) == before + 1


def test_unknown_correspondence_waits_for_one_manual_submit(tmp_path, monkeypatch):
    h, _, ref = custom_candidate(tmp_path, monkeypatch)
    before = len(RunStore(tmp_path).list_runs())
    state, report = start(tmp_path, ref)
    assert state['run_id'] is None and state['role_map'] is None
    assert state['matching']['method'] == 'manual'
    assert any(i['expected']['role'] == 'SkillInvocation' for i in report['issues'])
    assert any(t['id'] == 'CallSite' for t in state['role_proposal']['term_options'])
    assert not any(b['role'] == 'CallSite' for b in state['role_proposal']['bindings'])
    assert len(RunStore(tmp_path).list_runs()) == before
    bindings = deepcopy(state['role_proposal']['bindings'])
    source = DesignAssets(tmp_path).detail(**ref)['tbox']['namespace'] + 'CallSite'
    bindings.append({'kind': 'classes', 'role': 'SkillInvocation', 'iri': source})
    queued = service(tmp_path).submit(state['adaptation_id'], bindings=bindings,
        expected_revision=state['revision'], request_id='manual-and-generate')
    assert queued['status'] == 'queued' and queued['matching']['method'] == 'manual'
    assert queued['role_map']['confirmed'] and len(RunStore(tmp_path).list_runs()) == before + 1
    done = finish(tmp_path, queued); _, document = current_bundle(tmp_path, done['output_asset_ref'])
    assert document['components']['mapping_method'] == 'manual' and len(h.runtime.calls) == 2


def test_unknown_vocabulary_does_not_auto_approve_even_with_same_ids(tmp_path, monkeypatch):
    _, _, ref = custom_candidate(tmp_path, monkeypatch, rename=False)
    state, _ = start(tmp_path, ref)
    assert state['run_id'] is None and state['matching']['method'] == 'manual'
    assert state['matching']['reason'] == 'vocabulary_review_required'


def test_invalid_manual_mapping_cannot_enqueue_or_persist_confirmation(tmp_path, monkeypatch):
    _, _, ref = custom_candidate(tmp_path, monkeypatch)
    state, _ = start(tmp_path, ref)
    bindings = deepcopy(state['role_proposal']['bindings'])
    wrong = next(t['iri'] for t in state['role_proposal']['term_options'] if t['kind'] == 'object_properties')
    bindings.append({'kind': 'classes', 'role': 'SkillInvocation', 'iri': wrong})
    before, files = db_rows(tmp_path), blob_bytes(tmp_path)
    with pytest.raises(ValueError) as error:
        service(tmp_path).submit(state['adaptation_id'], bindings=bindings,
            expected_revision=state['revision'], request_id='invalid-mapping')
    assert error.value.code == 'role_conflict'
    assert db_rows(tmp_path) == before and blob_bytes(tmp_path) == files


def test_automatic_extraction_failure_stays_failed_until_explicit_retry(tmp_path):
    _, _, ref = candidate(tmp_path); state, _ = start(tmp_path, ref)
    assert state['status'] == 'queued'
    moved = archive_xml(tmp_path, ref)
    try:
        assert service(tmp_path).execute(state['run_id'])['status'] == 'failed'
        failed = service(tmp_path).get(state['adaptation_id'])
        count = len(RunStore(tmp_path).list_runs())
        assert service(tmp_path, read_only=True).get(state['adaptation_id']) == failed
        assert failed['status'] == 'failed' and failed['output_asset_ref'] is None
        assert len(RunStore(tmp_path).list_runs()) == count
    finally:
        for source, archived in moved: archived.rename(source)
    retry = service(tmp_path).submit(state['adaptation_id'], expected_revision=failed['revision'], request_id='retry')
    assert RunStore(tmp_path).get_run(retry['run_id'])['retry_of'] == state['run_id']
    finish(tmp_path, retry)


def test_manual_http_confirmation_and_queue_are_one_idempotent_request(tmp_path, monkeypatch):
    with http_app(tmp_path) as (_, client, _, root):
        _, _, ref = custom_candidate(root, monkeypatch)
        state, _ = start(root, ref)
        bindings = deepcopy(state['role_proposal']['bindings'])
        source = next(t['iri'] for t in state['role_proposal']['term_options'] if t['id'] == 'CallSite')
        bindings.append({'kind': 'classes', 'role': 'SkillInvocation', 'iri': source})
        path = '/api/robotics/application-adaptations/' + state['adaptation_id'] + '/submit'
        body = {'bindings': bindings, 'expected_revision': state['revision'], 'request_id': 'one-click'}
        before = db_rows(root)
        for claim in ('confirmed', 'automatic', 'matching', 'ready'):
            rejected = client.post(path, json={**body, claim: True}, base_url=BASE_URL)
            assert rejected.status_code == 400 and db_rows(root) == before
        forged = [{**bindings[0], 'confirmed': True}, *bindings[1:]]
        rejected = client.post(path, json={**body, 'bindings': forged}, base_url=BASE_URL)
        assert rejected.status_code == 400 and db_rows(root) == before
        response = client.post(path, json=body, base_url=BASE_URL)
        assert response.status_code == 202, response.json
        assert response.json['status'] == 'queued' and response.json['matching']['method'] == 'manual'
        assert response.json['role_map']['confirmed'] is True
        assert client.post(path, json=body, base_url=BASE_URL).json == response.json
        assert len(db_rows(root)['runs']) == len(before['runs']) + 1

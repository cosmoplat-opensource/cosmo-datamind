"""IR-029 T04 RED: actual legacy host, HTTP, cache and lifecycle races — DR-157."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
import server

from ontology_assets.lifecycle import LifecycleService
from tests.unit.test_deepqa_application_http import qa_http, ask
from tests.unit.test_asset_lifecycle import apply, make_run

__all__ = ['qa_http']


def activate(fixture):
    assert hasattr(server, 'init_asset_lifecycle'), 'DR-157 lifecycle host is not connected'
    server.init_asset_lifecycle()
    service = LifecycleService(fixture[1] / 'domain_assets')
    binding = service.resolve_legacy('built_scope')
    return service, binding['ontology_id']


def request(client, oid, op='delete', **overrides):
    endpoint = 'deletion-preview' if op == 'delete' else 'restoration-preview'
    response = client.post(f'/api/ontology-assets/{oid}/{endpoint}', json={})
    assert response.status_code == 200, response.json
    preview = response.json
    body = {'expected_revision': preview['revision'], 'impact_sha256': preview['impact_sha256'],
            'confirmed': True, 'request_id': op + '-http', **overrides}
    return client.post(f'/api/ontology-assets/{oid}/{op}', json=body)


def test_real_catalog_preview_confirmation_delete_restore_preserves_raw_files(qa_http):
    service, oid = activate(qa_http); client, work, *_ = qa_http; original = (work / 'built_scope.json').read_bytes()
    assert client.post(f'/api/ontology-assets/{oid}/deletion-preview', json={}).status_code == 200
    response = request(client, oid, confirmed=False)
    assert response.status_code == 400 and response.json['error']['code'] == 'confirmation_required'
    assert request(client, oid).status_code == 200
    assert service.get(oid)['state'] == 'deleted' and (work / 'built_scope.json').read_bytes() == original
    assert oid not in [a.get('ontology_id') for a in client.get('/api/ontology-assets').json['items']]
    assert oid in [a.get('ontology_id') for a in client.get('/api/ontology-assets?state=deleted').json['items']]
    assert request(client, oid, 'restore').status_code == 200
    assert service.get(oid)['state'] == 'active' and (work / 'built_scope.json').read_bytes() == original


@pytest.mark.parametrize('route', ['/api/chat', '/api/chat/stream'])
@pytest.mark.parametrize('mode', ['off', 'shadow', 'enforce'])
def test_deleted_legacy_chat_rejects_before_cache_model_or_query(qa_http, route, mode, monkeypatch):
    _, oid = activate(qa_http); monkeypatch.setenv('DATAMIND_DEEPQA_CONTRACT_MODE', mode)
    first, _, _ = ask(qa_http, route); assert first['results']; before = dict(qa_http[-1]); before['sql'] = list(before['sql'])
    assert request(qa_http[0], oid).status_code == 200
    answer, _, _ = ask(qa_http, route)
    assert answer['code'] == 'ontology_deleted' and qa_http[-1] == before
    assert request(qa_http[0], oid, 'restore').status_code == 200
    answer, _, _ = ask(qa_http, route)
    assert answer['results'] and not answer.get('cached')


@pytest.mark.parametrize('route,body', [('/api/build/delete', {'key':'built_scope'}),
                                     ('/api/ont/forged/delete', {'id':'onto_test'})])
def test_migrated_old_delete_routes_cannot_bypass_confirmation(qa_http, route, body):
    service, _ = activate(qa_http)
    response = qa_http[0].post(route, json=body)
    assert response.status_code == 400 and response.json['error']['code'] == 'confirmation_required'
    key = body.get('key') or 'forged_' + body['id']
    assert service.resolve_legacy(key)['lifecycle']['state'] == 'active'


def test_catalog_scan_and_changed_raw_file_cannot_resurrect_deleted_binding(qa_http):
    service, oid = activate(qa_http); client, work, *_ = qa_http
    assert request(client, oid).status_code == 200
    (work / 'built_scope.json').write_text('{"objects":[]}')
    assert 'built_scope' not in [g['id'] for g in client.get('/api/graphs').json]
    assert oid not in [a.get('ontology_id') for a in client.get('/api/ontology-assets').json['items']]
    server._QA_CACHE.clear(); server._GRAPH_ROW_CACHE.clear()
    server.init_asset_lifecycle()
    assert service.resolve_legacy('built_scope')['lifecycle']['state'] == 'deleted'
    answer, _, _ = ask(qa_http, '/api/chat'); assert answer['code'] == 'ontology_deleted'


def test_restore_uses_fixed_effective_snapshot_instead_of_changed_raw_file(qa_http):
    _, oid = activate(qa_http); client, work, *_ = qa_http
    assert request(client, oid).status_code == 200
    (work / 'built_scope.json').write_text('{"objects":[]}')
    assert request(client, oid, 'restore').status_code == 200
    answer, _, _ = ask(qa_http, '/api/chat'); assert answer['results'][0]['data']['rows'] == [{'记录数':3}]


def test_new_run_admission_and_delete_have_one_atomic_winner(qa_http):
    service, oid = activate(qa_http); p = service.preview(oid); barrier = threading.Barrier(2)
    from domain_runs import RunStore
    version = service.resolve_legacy('built_scope')['version_id']; root = qa_http[1] / 'domain_assets'
    def delete():
        barrier.wait()
        try:
            service.delete(oid, expected_revision=p['revision'], impact_sha256=p['impact_sha256'], request_id='race', confirmed=True)
            return 'deleted'
        except ValueError as exc: return getattr(exc,'code',None)
    def enqueue():
        barrier.wait()
        try:
            run = RunStore(root).create_run(ontology_id=oid,version_id=version,operation='generate_behavior_tree',
                task={},mode='real',session_ref={'namespace':'race','id':'one'},
                components={'profile_id':None,'compiler_version':None,'rule_digest':None,'prompt_version':None,'model':{'invoked':False}})
            return run['status']
        except ValueError as exc: return getattr(exc,'code',None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(delete);b=pool.submit(enqueue);answers=[a.result(10),b.result(10)]
    assert answers in (['deleted','ontology_deleted'], ['asset_busy','queued'], ['impact_changed','queued'])


@pytest.mark.parametrize('status',['queued','running'])
def test_busy_run_rejects_delete_and_retains_files(qa_http,status):
    service,oid=activate(qa_http)
    binding=service.resolve_legacy('built_scope');root=qa_http[1]/'domain_assets'
    sample={'root':root};ref={'asset':{'ontology_id':oid},'versions':[{'version_id':binding['version_id']}]}
    runs,run=make_run(sample,ref)
    if status=='running':runs.start(run['run_id'])
    response=request(qa_http[0],oid)
    assert response.status_code==409 and response.json['error']['code']=='asset_busy'
    assert service.get(oid)['state']=='active'


def test_waiting_task_is_readable_but_writes_are_frozen_after_delete_restore(qa_http):
    service,oid=activate(qa_http);root=qa_http[1]/'domain_assets';binding=service.resolve_legacy('built_scope')
    from domain_runs.robotics_task_service import TaskService
    task=TaskService(root,model_client=object())
    state={'session_id':'task_wait','ontology_id':oid,'version_id':binding['version_id'],
           'revision':0,'status':'idle','messages':[],'task':None,'generation_policy':'strict','configurations':[]}
    with task.storage.transaction(write=True) as conn:task._put(conn,state)
    before=json.dumps(state,sort_keys=True)
    apply(service,oid)
    got=task.get('task_wait');assert got['lifecycle']['state']=='deleted' and got['write_frozen'] is True
    with pytest.raises(ValueError) as exc:
        task.message('task_wait',text='new',expected_revision=0,request_id='write')
    assert getattr(exc.value,'code',None)=='ontology_deleted'
    apply(service,oid,'restore','restore')
    with pytest.raises(ValueError) as exc:
        task.message('task_wait',text='new',expected_revision=0,request_id='write-after-restore')
    assert getattr(exc.value,'code',None)=='stale_lifecycle'
    assert before==json.dumps(state,sort_keys=True)


def test_history_payload_and_version_get_stay_readable_after_delete(qa_http):
    service,oid=activate(qa_http);binding=service.resolve_legacy('built_scope');client=qa_http[0]
    path=f'/api/ontology-assets/{oid}/versions/{binding["version_id"]}'
    assert client.get(path).status_code==200
    assert request(client,oid).status_code==200
    assert client.get(path).status_code==409
    response=client.get(path+"?state=deleted");assert response.status_code==200 and response.json["lifecycle"]["state"]=="deleted"


def test_malformed_http_fields_and_if_match_cannot_modify_state(qa_http):
    service,oid=activate(qa_http);client=qa_http[0];p=service.preview(oid)
    body={'expected_revision':0,'impact_sha256':p['impact_sha256'],'request_id':'invalid','confirmed':True}
    response=client.post(f'/api/ontology-assets/{oid}/delete',json={**body,'path':'/tmp/forged'})
    assert response.status_code==400
    response=client.post(f'/api/ontology-assets/{oid}/delete',json=body,headers={'If-Match':'"9"'})
    assert response.status_code==409 and service.get(oid)['state']=='active'

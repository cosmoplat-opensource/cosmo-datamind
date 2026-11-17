"""T05 supplemental host bridges and atomic admission, no frozen RED edits — DR-156."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

from flask import Flask
import pytest
import server

from bp_application_contracts import init_app
from ontology_assets import AssetStore
from tests.unit.test_deepqa_application_http import qa_http, ask, report, ROUTES

__all__ = ['qa_http']


@pytest.mark.parametrize('robotics_enabled',[True,False])
def test_fixed_data_check_and_read_are_durable_without_enabling_robotics_execution(qa_http,robotics_enabled):
    _,work,_,ir,calls=qa_http;root=work/'domain_assets'
    store=AssetStore(root);asset=store.create_asset(name='客户',domain='data')
    version=store.add_version(asset['ontology_id'],json.dumps(ir).encode(),payload_kind='datamind_ir')
    ref={'ontology_id':asset['ontology_id'],'version_id':version['version_id']}
    app=Flask(__name__);init_app(app,root=root,data_host=server,enabled=robotics_enabled)
    client=app.test_client()
    entries=client.get('/api/applications').json['items']
    assert next(e for e in entries if e['application_id']=='data.deep_qa')['availability']=='available'
    response=client.post('/api/application-checks',json={'entrypoint':'data.ask','asset_refs':[ref],
        'context':{'mode':'real'},'request_id':'fixed-data-check'})
    assert response.status_code==201,response.json
    saved=response.json['report'];assert saved['asset_refs']==[ref]
    assert saved['preparation_status']=='available' and saved['execution_status']=='not_evaluated'
    reloaded=client.get('/api/application-checks/'+saved['check_id'])
    assert reloaded.status_code==200 and reloaded.json['freshness']=='current'
    assert calls=={'model':0,'sql':[]}


def test_static_data_registration_does_not_create_storage(qa_http):
    root=qa_http[1]/'no-assets';app=Flask(__name__);init_app(app,root=root,data_host=server)
    assert app.test_client().get('/api/applications').status_code==200
    assert not root.exists()


@pytest.mark.parametrize('route',ROUTES)
def test_null_optional_graphs_preserves_default_demo(qa_http,route):
    answer,_,_=ask(qa_http,route,graphs=None)
    assert answer['results'][0]['data']['rows']==[{'记录数':3}]


def test_legacy_delete_and_sql_admission_are_serialized(qa_http,monkeypatch):
    _,_,_,_,calls=qa_http;entered=threading.Event();release=threading.Event();deleting=threading.Event();events=[]
    original=server.q
    def query(sql,**kwargs):
        entered.set();assert release.wait(5)
        value=original(sql,**kwargs);events.append('query');return value
    monkeypatch.setattr(server,'q',query)
    def deletion():
        deleting.set();value=server.app.test_client().post('/api/build/delete',json={'key':'built_scope'})
        events.append('delete');return value.status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        query_future=workers.submit(lambda:server.app.test_client().post('/api/chat',json={'q':'客户有多少条记录？','graphs':['built_scope']}))
        try:
            assert entered.wait(5)
            delete_future=workers.submit(deletion);assert deleting.wait(5)
            assert not delete_future.done()
        finally:release.set()
        assert query_future.result(timeout=5).status_code==200
        assert delete_future.result(timeout=5)==200
    assert events==['query','delete'] and len(calls['sql'])==1


def test_corrupt_deletion_ledger_prevents_file_removal(qa_http):
    client,work,*_=qa_http;(work/'qa_deleted_graphs.json').write_text('[]')
    assert client.post('/api/build/delete',json={'key':'built_scope'}).status_code==503
    assert (work/'built_scope.json').exists()


@pytest.mark.parametrize('route',ROUTES)
def test_off_mode_keeps_original_sql_guard(qa_http,route,monkeypatch):
    monkeypatch.setenv('DATAMIND_DEEPQA_CONTRACT_MODE','off')
    monkeypatch.setattr(server,'_match_qa_skill',lambda q:{'analyses':[{'sql':'SELECT * FROM outsiders'}]})
    answer,_,_=ask(qa_http,route)
    assert answer['results']==[] and qa_http[-1]['sql']==[]
    assert report(answer)['mode']=='off'


@pytest.mark.parametrize('route',ROUTES)
def test_report_hides_raw_ir_database_paths_and_client_claims(qa_http,route):
    answer,_,_=ask(qa_http,route,application_check={'password':'never-trusted'})
    value=report(answer)
    assert 'never-trusted' not in json.dumps(value)
    assert str(qa_http[1].parent) not in json.dumps(value)
    assert 'objects' not in value and 'environment' not in value


@pytest.mark.parametrize('route',ROUTES)
def test_upload_focus_narrows_multitable_graph(qa_http,route,monkeypatch):
    _,work,_,ir,calls=qa_http
    ir['objects'].append({'id':'upload','cn':'上传数据','table':'uploaded'})
    (work/'built_scope.json').write_text(json.dumps(ir))
    monkeypatch.setattr(server,'_match_qa_skill',lambda q:{'analyses':[{'sql':'SELECT COUNT(*) FROM customers'}]})
    answer,_,_=ask(qa_http,route,tables=['up.uploaded'])
    assert answer['results']==[] and calls['sql']==[]
    monkeypatch.setattr(server,'_match_qa_skill',lambda q:{'analyses':[{'sql':'SELECT COUNT(*) AS n FROM up.uploaded'}]})
    answer,_,_=ask(qa_http,route,tables=['up.uploaded'],nocache=True)
    assert answer['results'][0]['data']['rows']==[{'n':0}]


@pytest.mark.parametrize('route',ROUTES)
def test_foreign_asset_guard_honors_configured_storage_root(qa_http,route,monkeypatch):
    root=qa_http[1]/'custom-assets';store=AssetStore(root)
    asset=store.create_asset(name='机器人',domain='robotics')
    store.add_version(asset['ontology_id'],b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',payload_kind='robotics_rdf')
    monkeypatch.setitem(server.app.config,'DATAMIND_DOMAIN_STORAGE_ROOT',str(root))
    answer,_,_=ask(qa_http,route,graphs=[asset['ontology_id']])
    assert answer['code']=='unsupported_payload' and qa_http[-1]=={'model':0,'sql':[]}


@pytest.mark.parametrize('route',ROUTES)
def test_resolution_failure_is_structured_and_redacted(qa_http,route,monkeypatch):
    def broken(*args):raise RuntimeError('private-endpoint-token')
    monkeypatch.setattr(server,'_qa_anchor_ir',broken)
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='resolution_failed' and 'private-endpoint-token' not in json.dumps(answer)
    assert qa_http[-1]=={'model':0,'sql':[]}


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('domain,identifier',[('data','ontology_id'),('robotics','version_id')])
def test_registered_identifiers_cannot_be_misread_as_missing_legacy_graph(qa_http,route,domain,identifier):
    root=qa_http[1]/'domain_assets';store=AssetStore(root)
    asset=store.create_asset(name='固定资产',domain=domain)
    payload=json.dumps(qa_http[3]).encode() if domain=='data' else b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
    version=store.add_version(asset['ontology_id'],payload,payload_kind='datamind_ir' if domain=='data' else 'robotics_rdf')
    key=asset['ontology_id'] if identifier=='ontology_id' else version['version_id']
    answer,_,_=ask(qa_http,route,graphs=[key])
    assert answer['code']==('unsupported_payload' if domain=='robotics' else 'unsupported_asset_reference')
    assert qa_http[-1]=={'model':0,'sql':[]}


def _delete_in_worker(args):
    from types import SimpleNamespace
    from application_contracts.deepqa_host import record_delete
    from srv_context import _atomic_json
    root,key=args
    host=SimpleNamespace(WORK=root,_atomic_json=_atomic_json,_bad_gkey=lambda k:not isinstance(k,str) or '..' in k)
    record_delete(host,key)


def test_concurrent_worker_deletes_preserve_all_keys(qa_http):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing
    root=str(qa_http[1]);keys=['built_worker_'+str(i) for i in range(24)]
    # Real OS processes and real atomic JSON; process-local thread locks cannot protect this journal.
    with ProcessPoolExecutor(max_workers=6,mp_context=multiprocessing.get_context('fork')) as workers:
        list(workers.map(_delete_in_worker,[(root,key) for key in keys]))
    state=json.loads((qa_http[1]/'qa_deleted_graphs.json').read_text())
    assert set(state['graphs'])==set(keys)
    assert all(v=={'deleted':True,'revision':1} for v in state['graphs'].values())


def test_unregistered_importlib_host_and_extracted_issue_module(qa_http):
    import importlib.util
    import sys
    spec=importlib.util.spec_from_file_location('_qa_unregistered_host',server.__file__)
    module=importlib.util.module_from_spec(spec)
    assert module.__name__ not in sys.modules
    spec.loader.exec_module(module)
    try:
        assert module._QA_HOST.app is module.app
        assert module._QA_HOST._qa_scope({'graphs':['demo']})==([],['demo'])
        assert module.app.test_client().get('/assets/modules/qa-contract.js').status_code==200
    finally:module.app.extensions['robotics'].close()


@pytest.mark.parametrize('kind',['built','forged'])
def test_nonqueryable_legacy_filename_delete_cannot_poison_journal(qa_http,kind):
    client,work,_,ir,calls=qa_http
    key='built_测试' if kind=='built' else 'forged_测试'
    path=work/'built_测试.json' if kind=='built' else work/'forged'/'测试.json'
    path.write_text(json.dumps(ir))
    route='/api/build/delete' if kind=='built' else '/api/ont/forged/delete'
    response=client.post(route,json={'key':key} if kind=='built' else {'id':'测试'})
    assert response.status_code==200 and not path.exists()
    assert not (work/'qa_deleted_graphs.json').exists()
    answer,_,_=ask(qa_http,'/api/chat',graphs=[key])
    assert answer['code']=='invalid_scope' and calls=={'model':0,'sql':[]}


def test_legacy_edit_stat_invalidation_is_preserved_alongside_content_hash(qa_http):
    import os
    _,work,*_=qa_http;path=work/'edits_built_scope.json';path.write_text('{"ops":[]}')
    key=server._qa_key('缓存键验证',[],[],['built_scope']);stamp=path.stat()
    os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns+2000000000))
    assert server._qa_key('缓存键验证',[],[],['built_scope'])!=key


def test_column_lookup_tolerates_concurrent_cache_clear(qa_http,monkeypatch):
    class ClearingCache(dict):
        def __contains__(self,key):
            present=super().__contains__(key)
            self.clear()
            return present
    monkeypatch.setattr(server,'_COLS_CACHE',ClearingCache(customers=['id','amount']))
    # A lookup must retain its value safely even if another request clears the shared mapping.
    assert server._table_cols('customers')==['id','amount']

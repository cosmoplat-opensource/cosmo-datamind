"""T04 RED: real sync/SSE, edited files, SQLite and legacy deletion — DR-156."""
from copy import deepcopy
import importlib
import json
import os
import sqlite3

import pytest
import server
import usage_stat
from ontology_assets import AssetStore

ROUTES = ['/api/chat', '/api/chat/stream']
QUESTION = '客户有多少条记录？'


@pytest.fixture
def qa_http(tmp_path, monkeypatch):
    work = tmp_path / 'work'; work.mkdir()
    db, uploads = tmp_path / 'main.db', tmp_path / 'uploads.db'
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE customers(id INTEGER, amount REAL);'
                           'INSERT INTO customers VALUES(1,10),(2,20),(3,30);'
                           'CREATE TABLE outsiders(id INTEGER); INSERT INTO outsiders VALUES(9);')
    with sqlite3.connect(uploads) as conn: conn.execute('CREATE TABLE uploaded(id INTEGER)')
    ir = {'scenario': {'name': '客户本体'}, 'objects': [{'id':'customer','name':'customers','cn':'客户','table':'customers'}], 'links':[]}
    for key in ['built_scope','built_second','demo']:
        (work/(key+'.json')).write_text(json.dumps(ir))
    forged=work/'forged';forged.mkdir();(forged/'onto_test.json').write_text(json.dumps(ir))
    monkeypatch.setattr(server,'WORK',str(work));monkeypatch.setattr(server,'DB',str(db));monkeypatch.setattr(server,'UPLOAD_DB',str(uploads))
    monkeypatch.setattr(server,'FORGED_DIR',str(forged));monkeypatch.setattr(server,'_FORGED_ENGINE',str(work/'no-forged-engine'))
    monkeypatch.setattr(server,'IR_SOURCES',{'demo':{'name':'示例','paths':[str(work/'demo.json')]}})
    monkeypatch.setattr(server,'_INV_CACHE',{'sig':None,'val':None});monkeypatch.setattr(server,'_COLS_CACHE',{})
    monkeypatch.setattr(server,'_QA_CACHE',{})
    monkeypatch.setenv('DATAMIND_DEEPQA_CONTRACT_MODE','enforce')
    calls={'model':0,'sql':[]};real_q=server.q
    def model(*args,**kwargs):calls['model']+=1;return None
    def query(sql,**kwargs):calls['sql'].append(sql);return real_q(sql,**kwargs)
    monkeypatch.setattr(server,'agent_sql_plan',model);monkeypatch.setattr(server,'narrative_llm',lambda *a,**kw:None)
    monkeypatch.setattr(server,'_match_qa_skill',lambda q:None);monkeypatch.setattr(server,'q',query)
    monkeypatch.setattr(usage_stat,'record',lambda *a,**kw:None)
    monkeypatch.setattr(server,'_record_metric_usage',lambda cards,*a:cards)
    return server.app.test_client(),work,db,ir,calls


def ask(fixture,route,**overrides):
    client,*_=fixture
    response=client.post(route,json={'q':QUESTION,'graphs':['built_scope'],**overrides})
    if route.endswith('/stream'):
        events=[json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith('data: ')]
        assert events and events[-1]['type'] in ('done','error')
        return events[-1],response,events
    return response.get_json(),response,[]


def report(answer):
    value=answer['application_check']
    assert value['application_id']=='data.deep_qa' and value['contract_version']=='1'
    assert 'basis_sha256' in value and 'environment_revision' not in value
    return value


@pytest.mark.parametrize('route',ROUTES)
def test_host_binds_data_contract_and_preserves_offline_response(qa_http,route):
    answer,response,events=ask(qa_http,route)
    assert response.status_code==200
    assert answer['results'][0]['data']['rows']==[{'记录数':3}]
    assert all(k in answer for k in ('steps','results','narrative','summary','anchor','metric_cards'))
    assert report(answer)['execution_readiness']=='not_verified'
    assert report(answer)['comparison']=={'legacy_blocked':False,'contract_blocked':False}
    if events:assert [e['type'] for e in events].index('anchor') < [e['type'] for e in events].index('result')


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('missing',['unbound','unavailable'])
def test_sync_and_sse_return_same_structured_repair_before_model_or_sql(qa_http,route,missing):
    _,work,_,ir,calls=qa_http
    if missing=='unbound':ir['objects'][0].pop('table')
    else:ir['objects'][0]['table']='not_connected'
    (work/'built_scope.json').write_text(json.dumps(ir))
    answer,response,events=ask(qa_http,route)
    assert answer['code']=='ontology_not_queryable'
    if not events:assert response.status_code==422
    issues=report(answer)['issues'];assert issues and any(i['severity']=='blocking' for i in issues)
    assert any('绑定' in i['message'] or '接入' in i['message'] for i in issues)
    assert calls=={'model':0,'sql':[]}
    if events:assert not any(e['type'] in ('result','done') for e in events)


@pytest.mark.parametrize('route',ROUTES)
def test_partial_connection_keeps_available_subset_and_warnings(qa_http,route):
    _,work,_,ir,_=qa_http
    ir['objects'].append({'id':'remote','table':'not_connected'})
    (work/'built_scope.json').write_text(json.dumps(ir))
    answer,_,_=ask(qa_http,route)
    assert answer['results'][0]['data']['rows']==[{'记录数':3}]
    assert any(i['severity']=='warning' and 'not_connected' in i['entity_refs'] for i in report(answer)['issues'])


@pytest.mark.parametrize('route',ROUTES)
def test_unchanged_cache_hit_rechecks_scope_without_model_or_query(qa_http,route,monkeypatch):
    # DR-158/master: offline fallback is degraded and intentionally never cached.
    monkeypatch.setattr(server, "_match_qa_skill", lambda q: {"analyses":[{"sql":"SELECT COUNT(*) AS 记录数 FROM customers"}]})
    first,_,_=ask(qa_http,route);calls=deepcopy(qa_http[-1])
    second,_,_=ask(qa_http,route)
    assert second['cached'] is True and second['results']==first['results']
    assert report(second)['basis_sha256']==report(first)['basis_sha256']
    assert qa_http[-1]==calls


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('change',['base','edits','connection'])
def test_effective_graph_or_connection_change_blocks_old_cache_before_return(qa_http,route,change):
    answer,_,_=ask(qa_http,route);assert answer['results']
    _,work,db,ir,calls=qa_http;before=deepcopy(calls)
    if change=='base':
        ir['objects'][0]['table']='missing';(work/'built_scope.json').write_text(json.dumps(ir))
    elif change=='edits':
        (work/'edits_built_scope.json').write_text(json.dumps({'ops':[{'op':'remove_object','target':'obj:customer'}]}))
    else:
        with sqlite3.connect(db) as conn:conn.execute('DROP TABLE customers')
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='ontology_not_queryable' and not answer.get('cached')
    assert report(answer)['preparation_status']=='blocked' and calls==before


@pytest.mark.parametrize('route',ROUTES)
def test_same_stat_edit_content_changes_cache_identity(qa_http,route):
    _,work,_,_,calls=qa_http;edits=work/'edits_built_scope.json'
    def text(name):return json.dumps({'ops':[{'op':'set_alias','target':'obj:customer','params':{'aliases':[name]}}]})
    edits.write_text(text('AAA'));first,_,_=ask(qa_http,route);stamp=edits.stat();before=len(calls['sql'])
    edits.write_text(text('BBB'));os.utime(edits,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    second,_,_=ask(qa_http,route)
    assert not second.get('cached') and len(calls['sql'])==before+1
    assert report(first)['basis_sha256']!=report(second)['basis_sha256']


@pytest.mark.parametrize('route',ROUTES)
def test_schema_change_with_preserved_stat_invalidates_cached_columns(qa_http,route,monkeypatch):
    _,_,db,_,calls=qa_http
    monkeypatch.setattr(server,'_match_qa_skill',lambda q:{'analyses':[{'sql':'SELECT amount FROM customers'}]})
    first,_,_=ask(qa_http,route);assert first['results'];stamp=db.stat();before=len(calls['sql'])
    with sqlite3.connect(db) as conn:conn.execute('ALTER TABLE customers RENAME COLUMN amount TO changed')
    os.utime(db,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    answer,_,_=ask(qa_http,route)
    assert not answer.get('cached') and answer['results']==[] and len(calls['sql'])==before
    assert any(i['code']=='data.sql_rejected' for i in report(answer)['issues'])


def test_cache_identity_preserves_multigraph_order(qa_http):
    first=server._qa_key(QUESTION,[],[],['built_scope','built_second'])
    second=server._qa_key(QUESTION,[],[],['built_second','built_scope'])
    assert first!=second


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('mode',['enforce','shadow','off'])
def test_delete_marks_durable_key_and_cannot_fallback_or_hit_cache(qa_http,route,mode,monkeypatch):
    client,work,_,_,calls=qa_http
    ask(qa_http,route);before=deepcopy(calls)
    assert client.post('/api/build/delete',json={'key':'built_scope'}).status_code==200
    monkeypatch.setenv('DATAMIND_DEEPQA_CONTRACT_MODE',mode)
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='ontology_deleted' and calls==before
    # A copied legacy file cannot resurrect the deleted key, even after process cache loss.
    (work/'built_scope.json').write_text((work/'built_second.json').read_text());server._QA_CACHE.clear()
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='ontology_deleted' and calls==before


@pytest.mark.parametrize('route',ROUTES)
def test_deleted_key_inside_multigraph_is_rejected(qa_http,route):
    qa_http[0].post('/api/build/delete',json={'key':'built_scope'})
    answer,_,_=ask(qa_http,route,graphs=['built_second','built_scope'])
    assert answer['code']=='ontology_deleted' and qa_http[-1]['sql']==[]


@pytest.mark.parametrize('route',ROUTES)
def test_forged_delete_does_not_fallback_to_demo(qa_http,route):
    assert qa_http[0].post('/api/ont/forged/delete',json={'id':'onto_test'}).status_code==200
    answer,_,_=ask(qa_http,route,graphs=['forged_onto_test'])
    assert answer['code']=='ontology_deleted' and qa_http[-1]['sql']==[]


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('field',['graphs','asset_refs'])
def test_foreign_registered_asset_rejected_before_planning(qa_http,route,field):
    store=AssetStore(qa_http[1]/'domain_assets');asset=store.create_asset(name='机器人',domain='robotics')
    v=store.add_version(asset['ontology_id'],b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',payload_kind='robotics_rdf')
    request={field:[asset['ontology_id']]} if field=='graphs' else {'asset_refs':[{'ontology_id':asset['ontology_id'],'version_id':v['version_id']}]}
    answer,_,_=ask(qa_http,route,**request)
    assert answer['code']=='unsupported_payload' and qa_http[-1]=={'model':0,'sql':[]}


@pytest.mark.parametrize('route',ROUTES)
def test_forged_client_readiness_never_bypasses_real_checks(qa_http,route):
    _,work,_,ir,calls=qa_http;ir['objects'][0]['table']='missing'
    (work/'built_scope.json').write_text(json.dumps(ir))
    answer,_,_=ask(qa_http,route,application_check={'execution_status':'ready'},check_id='forged',ready=True)
    assert answer['code']=='ontology_not_queryable' and calls=={'model':0,'sql':[]}


@pytest.mark.parametrize('route',ROUTES)
def test_binding_change_during_planning_blocks_execution_and_cache(qa_http,route,monkeypatch):
    _,work,_,ir,calls=qa_http
    def plan(*a, **kwargs):
        ir['objects'][0]['table']='outsiders';(work/'built_scope.json').write_text(json.dumps(ir))
        return {'analyses':[{'sql':'SELECT COUNT(*) FROM customers'}]}
    monkeypatch.setattr(server,'agent_sql_plan',plan)
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='stale_result' and calls['sql']==[] and not server._QA_CACHE


@pytest.mark.parametrize('route',ROUTES)
def test_each_sql_rechecks_after_first_query_changes_graph(qa_http,route,monkeypatch):
    _,work,_,ir,calls=qa_http;real=server.q
    monkeypatch.setattr(server,'_match_qa_skill',lambda q:{'analyses':[{'sql':'SELECT COUNT(*) FROM customers'}]*2})
    def query(sql,**kw):
        value=real(sql,**kw);ir['objects'][0]['table']='outsiders';(work/'built_scope.json').write_text(json.dumps(ir));return value
    monkeypatch.setattr(server,'q',query)
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='stale_result' and len(calls['sql'])==1 and not server._QA_CACHE


@pytest.mark.parametrize('route',ROUTES)
def test_cached_sql_is_revalidated_before_reusing_results(qa_http,route,monkeypatch):
    monkeypatch.setattr(server, "_match_qa_skill", lambda q: {"analyses":[{"sql":"SELECT COUNT(*) AS 记录数 FROM customers"}]})
    ask(qa_http,route);entry=next(iter(server._QA_CACHE.values()))
    entry['results'][0]['sql']='SELECT * FROM outsiders'
    before=len(qa_http[-1]['sql'])
    answer,_,_=ask(qa_http,route)
    assert not answer.get('cached') and len(qa_http[-1]['sql'])==before+1
    assert 'customers' in answer['results'][0]['sql']


@pytest.mark.parametrize('route',ROUTES)
def test_corrupt_deletion_record_fails_closed_without_private_error(qa_http,route):
    (qa_http[1]/'qa_deleted_graphs.json').write_text('{not-json')
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='lifecycle_unavailable'
    assert str(qa_http[1]) not in json.dumps(answer) and qa_http[-1]=={'model':0,'sql':[]}


@pytest.mark.parametrize('route',ROUTES)
def test_unknown_legacy_key_preserves_documented_fallback_warning(qa_http,route):
    answer,_,_=ask(qa_http,route,graphs=['built_never_created'])
    assert answer['results'][0]['data']['rows']==[{'记录数':3}]
    assert any(i['code']=='data.legacy_fallback' for i in report(answer)['issues'])


@pytest.mark.parametrize('route',ROUTES)
def test_shadow_comparison_matches_legacy_boundary(qa_http,route,monkeypatch):
    monkeypatch.setenv('DATAMIND_DEEPQA_CONTRACT_MODE','shadow')
    answer,_,_=ask(qa_http,route)
    value=report(answer)
    assert value['mode']=='shadow' and value['comparison']=={'legacy_blocked':False,'contract_blocked':False}


def test_contract_version_participates_in_cache_identity(qa_http,monkeypatch):
    first=server._qa_key(QUESTION,[],[],['built_scope'])
    cls=importlib.import_module('application_contracts.deepqa').DataQAAdapter;old=cls.descriptor
    def descriptor(self):return {**old(self),'contract_version':'2'}
    monkeypatch.setattr(cls,'descriptor',descriptor)
    assert server._qa_key(QUESTION,[],[],['built_scope'])!=first


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('graphs',['built_scope',['../secret'],[{'application_id':'data.deep_qa'}]])
def test_invalid_graph_scope_cannot_silently_become_demo(qa_http,route,graphs):
    answer,_,_=ask(qa_http,route,graphs=graphs)
    assert answer['code']=='invalid_scope' and qa_http[-1]=={'model':0,'sql':[]}


@pytest.mark.parametrize('route',ROUTES)
@pytest.mark.parametrize('scenario',['unbound','partial'])
def test_shadow_matches_legacy_for_binding_and_partial_subset(qa_http,route,scenario,monkeypatch):
    monkeypatch.setenv('DATAMIND_DEEPQA_CONTRACT_MODE','shadow')
    _,work,_,ir,_=qa_http
    if scenario=='unbound':ir['objects'][0].pop('table')
    else:ir['objects'].append({'id':'missing','table':'missing'})
    (work/'built_scope.json').write_text(json.dumps(ir))
    answer,_,_=ask(qa_http,route)
    assert report(answer)['comparison']=={'legacy_blocked':scenario=='unbound','contract_blocked':scenario=='unbound'}

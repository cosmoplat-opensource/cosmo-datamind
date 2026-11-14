"""Database selection must isolate data, ontology and read-only execution."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import pytest

import server


@pytest.fixture
def scoped_databases(make_sqlite, monkeypatch):
    a = make_sqlite({'shared': ('id INTEGER, value INTEGER', [(1, 3)]),
                     'only_a': ('id INTEGER', [(1,)])}, fname='a.db')
    b = make_sqlite({'shared': ('id INTEGER, value INTEGER', [(1, 97)]),
                     'only_b': ('id INTEGER', [(2,), (3,)])}, fname='b.db')
    conns = [{'id': 'conn_a', 'name': 'Source A', 'kind': 'sqlite', 'path': a},
             {'id': 'conn_b', 'name': 'Source B', 'kind': 'sqlite', 'path': b}]
    monkeypatch.setattr(server, '_load_conns', lambda: conns)
    original = server.load_ir_edited
    def load(key):
        if key not in ('built_scope_a', 'built_scope_b'): return original(key)
        sid = 'conn_a' if key.endswith('_a') else 'conn_b'
        return {'scenario': {'name': sid}, 'build_manifest': {'source': {'id': sid}},
                'objects': [{'id': 'shared', 'name': 'shared', 'table': 'shared', 'cn': sid,
                             'attrs': [{'col': 'id', 'type': 'INTEGER'}, {'col': 'value', 'type': 'INTEGER'}]}],
                'links': [], 'metric_layers': {'atomic': [{'name': 'Sum', 'table': 'shared',
                    'value_col': 'value', 'measure': {'agg': 'sum', 'col': 'value'},
                    'grain': ['id'], 'status': 'candidate'}]}}
    monkeypatch.setattr(server, 'load_ir_edited', load)
    monkeypatch.setattr(server, 'IR_SOURCES', {'built_scope_a': {'paths': [], 'name': 'A'},
                                             'built_scope_b': {'paths': [], 'name': 'B'}})
    return a, b


def test_catalog_overview_and_preview_use_selected_database(scoped_databases):
    client = server.app.test_client()
    for sid, own, other, value in [('conn_a', 'only_a', 'only_b', 3), ('conn_b', 'only_b', 'only_a', 97)]:
        headers = {'X-DataMind-Source': sid}
        tables = client.get('/api/tables', headers=headers).get_json()
        assert {t['name'] for t in tables} == {'shared', own}
        assert client.get('/api/table/shared', headers=headers).get_json()['preview']['rows'][0]['value'] == value
        assert client.get('/api/table/'+other, headers=headers).status_code == 404
        overview = client.get('/api/overview', headers=headers).get_json()
        assert overview['kpi']['tables'] == 2
        assert overview['kpi']['objects'] == 1
        assert client.get('/api/quality', headers=headers).get_json()['scanned'] == 2


def test_graphs_and_metrics_are_scoped_and_cross_source_graph_is_rejected(scoped_databases):
    client = server.app.test_client(); headers = {'X-DataMind-Source': 'conn_b'}
    graphs = client.get('/api/graphs', headers=headers).get_json()
    assert [g['id'] for g in graphs] == ['built_scope_b']
    assert client.get('/api/metric/quick?name=Sum', headers=headers).get_json()['data']['rows'] == [{'Sum': 97}]
    assert client.get('/api/graph/built_scope_a', headers=headers).status_code == 409
    assert client.post('/api/chat', headers=headers, json={'q': 'count', 'graphs': ['built_scope_a']}).status_code == 409


def test_unknown_source_never_falls_back_to_demo(scoped_databases):
    client = server.app.test_client()
    for route in ('/api/tables', '/api/overview', '/api/graphs'):
        r = client.get(route, headers={'X-DataMind-Source': 'missing'})
        assert r.status_code == 400 and r.get_json()['code'] == 'source_unavailable'


def test_concurrent_sources_and_worker_context_do_not_cross(scoped_databases):
    def read(sid):
        with server.app.test_client() as client:
            r = client.post('/api/query', headers={'X-DataMind-Source': sid}, json={'sql': 'SELECT value FROM shared'})
            return r.get_json()['rows'][0]['value']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(read, ['conn_a','conn_b'] * 6)) == [3,97] * 6
    with server.app.test_request_context('/api/query', headers={'X-DataMind-Source': 'conn_b'}):
        assert server.app.preprocess_request() is None
        assert server._bounded(lambda: server.q('SELECT value FROM shared')['rows'], 5) == [{'value': 97}]


def test_readonly_and_explicit_viz_source_survive_global_selection(scoped_databases):
    client = server.app.test_client(); a,b = scoped_databases
    before = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (a,b)]
    headers = {'X-DataMind-Source': 'conn_b'}
    assert client.post('/api/query', headers=headers, json={'sql': 'DELETE FROM shared'}).status_code == 400
    assert client.post('/api/viz/run', headers=headers, json={'src': 'conn_a', 'sql': 'SELECT value FROM shared'}).get_json()['rows'] == [{'value': 3}]
    assert [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (a,b)] == before


def test_stream_qa_keeps_selected_source_and_default_graph(scoped_databases, monkeypatch):
    monkeypatch.setattr(server, '_drv_order', lambda: [])
    monkeypatch.setattr(server, '_match_qa_skill', lambda *a: None)
    monkeypatch.setattr(server, 'agent_sql_plan', lambda *a, **k: {'analyses': [{'title': 'value', 'sql': 'SELECT value FROM shared'}]})
    with server.app.test_client() as client:
        text = client.post('/api/chat/stream', headers={'X-DataMind-Source': 'conn_b'},
                           json={'q': 'shared', 'cache_policy': 'bypass'}).get_data(as_text=True)
    events = [json.loads(s[6:]) for s in text.splitlines() if s.startswith('data: ')]
    done = next(e for e in events if e['type']=='done')
    assert done['results'][0]['data']['rows'] == [{'value': 97}]
    assert done['anchor']['ontology']['keys'] == ['built_scope_b']


def test_source_without_ontology_has_data_but_never_uses_demo(scoped_databases, monkeypatch):
    monkeypatch.setattr(server, 'IR_SOURCES', {})
    client = server.app.test_client(); headers = {'X-DataMind-Source': 'conn_b'}
    assert client.get('/api/data/context', headers=headers).get_json()['has_ontology'] is False
    assert client.get('/api/overview', headers=headers).get_json()['kpi']['objects'] == 0
    r = client.post('/api/chat', headers=headers, json={'q': '记录总数'})
    assert r.status_code == 422


def test_metric_and_profile_blueprints_use_selected_default(scoped_databases):
    client = server.app.test_client(); headers = {'X-DataMind-Source': 'conn_b'}
    r = client.get('/api/metric/contract?name=Sum', headers=headers)
    assert r.status_code == 200 and r.get_json()['graph'] == 'built_scope_b'
    r = client.get('/api/ont/profile', headers=headers)
    assert r.status_code == 200 and r.get_json()['graph'] == 'built_scope_b'


def test_data_statistics_do_not_reuse_another_databases_cache(make_sqlite):
    paths = [make_sqlite({'fact_sales_order': ('order_date TEXT, amount REAL', [(date, value)])}, fname=filename)
             for date,value,filename in [('2024-02-01',3,'stats_a.db'),('2026-09-01',97,'stats_b.db')]]
    for path,date,value in zip(paths,['2024-02-01','2026-09-01'],[3,97]):
        token = server._QA_DB.set(path)
        try:
            assert date in server._data_window()
            stats = server._metric_baselines([{'table':'fact_sales_order','col':'amount','name':'Amount'}])
            assert f'[{float(value)}, {float(value)}]' in stats[0]
        finally: server._QA_DB.reset(token)


def test_default_viz_uses_global_source_but_explicit_demo_uses_demo(scoped_databases):
    client=server.app.test_client(); headers={'X-DataMind-Source':'conn_b'}
    r=client.post('/api/viz/run',headers=headers,json={'sql':'SELECT value FROM shared'})
    assert r.get_json()['rows']==[{'value':97}]
    r=client.post('/api/query',headers=headers,json={'src':'demo','sql':'SELECT COUNT(*) AS n FROM dim_customer'})
    assert r.status_code==200 and r.get_json()['rows'][0]['n']>0


def test_saved_sql_skills_are_not_reused_on_another_source(scoped_databases, monkeypatch, tmp_path):
    path=tmp_path/'skills.json'; monkeypatch.setattr(server,'_QA_SKILLS_F',str(path))
    client=server.app.test_client()
    r=client.post('/api/chat/save_skill',headers={'X-DataMind-Source':'conn_a'},json={
        'question':'shared value','results':[{'title':'value','sql':'SELECT value FROM shared'}]})
    assert r.status_code==200
    with server.app.test_request_context('/api/chat',headers={'X-DataMind-Source':'conn_b'}):
        server.app.preprocess_request()
        assert server._match_qa_skill('shared value') is None
    with server.app.test_request_context('/api/chat',headers={'X-DataMind-Source':'conn_a'}):
        server.app.preprocess_request()
        assert server._match_qa_skill('shared value') is not None
        fields=server._source_record_fields()
    monkeypatch.setattr(server,'_load_fb',lambda:[{'id':'foreign_feedback',**fields}])
    assert client.post('/api/chat/feedback/resolve',headers={'X-DataMind-Source':'conn_b'},
                       json={'id':'foreign_feedback'}).status_code==409


def test_source_evaluation_references_are_from_current_database(scoped_databases):
    client=server.app.test_client(); h={'X-DataMind-Source':'conn_b'}
    items=client.get('/api/eval/set',headers=h).get_json()['items']
    assert items and any(it['gold']==2 and 'only_b' in it['gold_tables'] for it in items)
    assert not any('only_a' in it['gold_tables'] for it in items)
    assert client.get('/api/eval/results',headers=h).get_json().get('empty') is True
    terms=client.get('/api/glossary',headers=h).get_json()['terms']
    assert any(t['en']=='only_b' for t in terms) and not any(t['en']=='only_a' for t in terms)


def test_action_records_keep_database_and_do_not_mix_in_other_scope(scoped_databases, monkeypatch):
    import bp_actions as actions
    records=[]
    monkeypatch.setattr(actions,'load_action_types',lambda:[{'id':'test_action','cn':'Check','object':'shared','object_table':'shared','risk':'high','params':[]}])
    monkeypatch.setattr(actions,'load_action_log',lambda:records)
    monkeypatch.setattr(actions,'_atomic_json',lambda _path,data: records.__setitem__(slice(None),data))
    client=server.app.test_client()
    r=client.post('/api/action/invoke',headers={'X-DataMind-Source':'conn_a'},json={'action_id':'test_action','operator':'Test'})
    assert r.status_code==200 and records[0]['source_id']=='conn_a'
    assert client.get('/api/actions',headers={'X-DataMind-Source':'conn_a'}).get_json()['pending']==1
    assert client.get('/api/actions',headers={'X-DataMind-Source':'conn_b'}).get_json()['pending']==0
    assert client.get('/api/action/log',headers={'X-DataMind-Source':'conn_b'}).get_json()['items']==[]
    r=client.post('/api/action/approve',headers={'X-DataMind-Source':'conn_b'},json={'id':records[0]['id'],'decision':'approve','approver':'Test'})
    assert r.status_code==409 and records[0]['status']=='pending'
    own_type={'id':'own_type','cn':'Check','params':[],**{k:records[0][k] for k in ('source_id','source_path_fp')}}
    monkeypatch.setattr(actions,'load_action_types',lambda:[own_type])
    for route in ('/api/action/type/update','/api/action/type/delete'):
        assert client.post(route,headers={'X-DataMind-Source':'conn_b'},json={'id':'own_type','desc':'wrong source'}).status_code==409


def test_quick_build_request_uses_registered_source_and_preserves_provenance(scoped_databases, monkeypatch, tmp_path):
    import io
    import quick_build
    argv=[]

    class _Proc:                                   # 兜底构建的子进程替身:只记录命令行,不产出文件
        stdout=io.StringIO('')

        def wait(self,timeout=None): return 0

        def poll(self): return 0
    monkeypatch.setattr(server,'_llm_extract_ontology',lambda *a,**k: None)
    monkeypatch.setattr(server,'_build_intent',lambda *a,**k: {})
    monkeypatch.setattr(server.subprocess,'Popen',lambda cmd,**kw: argv.extend(cmd) or _Proc())
    client=server.app.test_client()
    r=client.post('/api/build/inquire',json={'q':'构建本体','source':'conn_b','name':'Test'})
    r.get_data()                                   # 读完事件流:构建在服务端作业里跑完
    assert r.status_code==200 and argv[2]==scoped_databases[1]
    ir=quick_build.build(scoped_databases[1],str(tmp_path/'graph.json'),'Test',source={'id':'conn_b','name':'Source B'})
    assert ir['build_manifest']['source']['id']=='conn_b'


@pytest.mark.parametrize('case',['missing_source','mixed_sources'])
def test_legacy_graph_only_qa_does_not_fall_back_to_another_database(scoped_databases, monkeypatch, case):
    monkeypatch.setattr(server,'DB',scoped_databases[0])
    monkeypatch.setattr(server,'agent_sql_plan',lambda *a,**k:{'analyses':[{'title':'value','sql':'SELECT value FROM shared'}]})
    if case=='missing_source':
        Path(scoped_databases[1]).unlink()
        graphs=['built_scope_b']
    else: graphs=['built_scope_a','built_scope_b']
    response=server.app.test_client().post('/api/chat',json={'q':'shared value','graphs':graphs,'nocache':True})
    assert response.status_code==422


def test_ontology_conversation_history_does_not_cross_databases(scoped_databases, monkeypatch):
    monkeypatch.setattr(server,'_chats',lambda:{'a':{'id':'a','graph':'built_scope_a','messages':[]},
                                              'b':{'id':'b','graph':'built_scope_b','messages':[]}})
    client=server.app.test_client(); h={'X-DataMind-Source':'conn_b'}
    rows=client.get('/api/ont/chats',headers=h).get_json()
    assert [r['id'] for r in rows]==['b']
    assert client.get('/api/ont/chats/a',headers=h).status_code==409
    assert client.post('/api/ont/chats/delete',headers=h,json={'id':'a'}).status_code==409
    monkeypatch.setattr(server,'_chats_w',lambda d:None)
    assert client.post('/api/ont/chats/new',headers=h,json={}).get_json()['graph']=='built_scope_b'

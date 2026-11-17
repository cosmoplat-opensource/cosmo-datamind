"""DR-158: current master planning and source semantics under contract admission."""
import sqlite3

import pytest
import server
from application_contracts.deepqa_host import _catalog
from tests.unit.test_deepqa_application_http import qa_http, ask
from tests.unit.test_asset_lifecycle_http import activate, request

__all__ = ['qa_http']


@pytest.mark.parametrize('route',['/api/chat','/api/chat/stream'])
def test_invalid_first_sql_can_be_repaired_before_contract_execution(qa_http,monkeypatch,route):
    monkeypatch.setattr(server,'_data_window',lambda:'')
    monkeypatch.setattr(server,'agent_sql_plan',lambda *a,**k:{'analyses':[{'sql':'SELECT missing FROM customers'}]})
    repaired=[]
    def repair(*args):
        repaired.append(args)
        return 'SELECT COUNT(*) AS n FROM customers'
    monkeypatch.setattr(server,'_repair_sql',repair)
    answer,_,_=ask(qa_http,route)
    assert repaired
    assert answer['results'][0]['data']['rows']==[{'n':3}]
    assert qa_http[-1]['sql']==['SELECT COUNT(*) AS n FROM customers']


def test_catalog_fingerprint_tracks_actual_source_wal(qa_http,tmp_path):
    db=tmp_path/'source.db'
    with sqlite3.connect(db) as conn:conn.execute('CREATE TABLE records(id INTEGER)')
    token=server._QA_DB.set(str(db))
    try:
        first=_catalog(server)
        with sqlite3.connect(db) as conn:conn.execute('INSERT INTO records VALUES(1)')
        assert _catalog(server)!=first
        with sqlite3.connect(db) as conn:
            conn.execute('PRAGMA journal_mode=WAL');conn.execute('PRAGMA wal_autocheckpoint=0')
            conn.execute('INSERT INTO records VALUES(2)');conn.commit()
            second=_catalog(server)
            conn.execute('INSERT INTO records VALUES(3)');conn.commit()
            assert _catalog(server)!=second
    finally:server._QA_DB.reset(token)


@pytest.mark.parametrize('route',['/api/chat','/api/chat/stream'])
def test_deleted_graph_remains_structured_after_master_before_request_guards(qa_http,route):
    _,oid=activate(qa_http)
    assert request(qa_http[0],oid).status_code==200
    answer,_,_=ask(qa_http,route)
    assert answer['code']=='ontology_deleted'
    assert qa_http[-1]['sql']==[]


def test_strict_publication_is_not_bypassed_by_contract_or_cache(qa_http,monkeypatch):
    monkeypatch.setenv('DATAMIND_QA_REQUIRE_PUBLISHED','1')
    result=qa_http[0].post('/api/chat',json={'q':'客户数量','graphs':['built_scope']})
    assert result.status_code==409 and result.json['code']=='graph_not_released'
    assert qa_http[-1]['sql']==[]


def test_empty_current_database_does_not_fall_back_to_demo_contract(qa_http,tmp_path):
    db=tmp_path/'empty.db'
    with sqlite3.connect(db) as conn:conn.execute('CREATE TABLE unrelated(id INTEGER)')
    # Use the actual master connection registry via monkeypatch in the following context.
    from unittest.mock import patch
    with patch.object(server,'_load_conns',return_value=[{'id':'empty','kind':'sqlite','path':str(db)}]):
        response=qa_http[0].post('/api/chat',headers={'X-DataMind-Source':'empty'},json={'q':'有多少数据'})
    assert response.status_code==422
    assert response.json['code']=='ontology_not_queryable'
    assert qa_http[-1]['sql']==[]


@pytest.mark.parametrize('fixed',[False,True])
def test_multigraph_contract_keeps_role_relations_and_semantic_layers(qa_http,tmp_path,monkeypatch,fixed):
    from copy import deepcopy
    import json
    from application_contracts.deepqa import DataQAAdapter
    from ontology_assets import AssetStore
    documents = {}
    for role in ('first','last'):
        documents[role] = {'objects':[{'id':'customer','table':'customers'},{'id':'other','table':'outsiders'}],
            'relations':[{'source_concept':'customer','target_concept':'other','role':role}],
            'concepts':[{'name':role,'table':'customers','column':'amount','filter':'id=1'}]}
    if fixed:
        store = AssetStore(tmp_path/'fixed'); refs=[]
        for role, doc in documents.items():
            asset=store.create_asset(name=role,domain='data')
            version=store.add_version(asset['ontology_id'],json.dumps(doc).encode(),payload_kind='datamind_ir')
            refs.append({'ontology_id':asset['ontology_id'],'version_id':version['version_id']})
        data=DataQAAdapter(server,assets=store).resolve(asset_refs=refs)
    else:
        monkeypatch.setattr(server,'load_ir_edited',lambda key:deepcopy(documents.get(key)))
        data=DataQAAdapter(server).resolve(graph_keys=list(documents))
    ir=data['environment']['deep_qa']['ir']
    assert {r.get('role') for r in ir['links']}=={'first','last'}
    assert {c['name'] for c in ir['concepts']}=={'first','last'}


@pytest.mark.parametrize('route',['/api/chat','/api/chat/stream'])
def test_model_probes_cannot_bypass_semantic_validation(qa_http,monkeypatch,route):
    validate=server._qa_validate_sql
    bad='SELECT amount FROM customers'
    good='SELECT COUNT(*) AS n FROM customers'
    monkeypatch.setattr(server,'_qa_validate_sql',lambda sql,*a,**k:
                        (False,'未通过本体口径') if sql==bad else validate(sql,*a,**k))
    observations=[]
    def plan(*args,probe_runner=None,**kwargs):
        observations.extend([probe_runner(bad),probe_runner(good)])
        return {'analyses':[{'sql':good}]}
    monkeypatch.setattr(server,'agent_sql_plan',plan)
    answer,_,_=ask(qa_http,route)
    assert bad not in qa_http[-1]['sql']
    assert any(word in observations[0] for word in ('拒绝','错误'))
    assert '3' in observations[1]
    assert answer['results'][0]['data']['rows']==[{'n':3}]

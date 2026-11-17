"""DR-158: lifecycle snapshots coexist with master editing and replay semantics."""
import pytest
import server
from tests.unit.test_deepqa_application_http import qa_http
from tests.unit.test_asset_lifecycle_http import activate

__all__ = ['qa_http']


def alias(client, value='客户别称'):
    response = client.post('/api/ont/apply', json={'graph':'built_scope',
        'op':{'op':'set_alias','target':'obj:customer','params':{'aliases':value}}})
    assert response.status_code == 200, response.json


def test_undo_keeps_later_base_maturity(qa_http):
    activate(qa_http); client = qa_http[0]
    alias(client)
    response = client.post('/api/ont/maturity',json={'graph':'built_scope','object':'customer','maturity':'Released'})
    assert response.status_code == 200, response.json
    response = client.post('/api/ont/undo',json={'graph':'built_scope'})
    assert response.status_code == 200, response.json
    obj = server.load_ir_edited('built_scope')['objects'][0]
    assert obj['maturity'] == 'Released'
    assert not obj.get('aliases')


def test_rebuild_reverts_edits_and_next_edit_is_not_lost(qa_http):
    activate(qa_http); client = qa_http[0]
    alias(client)
    response = client.post('/api/ont/rebuild',json={'graph':'built_scope','confirm':True})
    assert response.status_code == 200, response.json
    assert not server.load_ir_edited('built_scope')['objects'][0].get('aliases')
    alias(client,'新别称')
    assert server.load_ir_edited('built_scope')['objects'][0]['aliases'] == ['新别称']


@pytest.mark.parametrize('count,expected',[(0,[]),(1,['第一版']),(2,['第二版'])])
def test_paired_feedback_replay_uses_requested_edit_prefix(qa_http,count,expected):
    activate(qa_http); client = qa_http[0]
    alias(client,'第一版'); alias(client,'第二版')
    token = server._IR_PIN.set({'built_scope':count})
    try:
        assert server.load_ir_edited('built_scope')['objects'][0].get('aliases',[]) == expected
    finally:
        server._IR_PIN.reset(token)
    assert server.load_ir_edited('built_scope')['objects'][0]['aliases'] == ['第二版']


def test_deleted_legacy_asset_is_removed_from_source_defaults(qa_http):
    from tests.unit.test_asset_lifecycle_http import request
    service, oid = activate(qa_http)
    assert 'built_scope' in server._source_graph_keys(str(qa_http[2]))
    assert request(qa_http[0], oid).status_code == 200
    assert 'built_scope' not in server._source_graph_keys(str(qa_http[2]))
    assert service.get(oid)['state'] == 'deleted'


def test_edit_after_restore_keeps_frozen_baseline_for_later_undo(qa_http):
    import json
    from tests.unit.test_asset_lifecycle_http import request
    _,oid=activate(qa_http);client,work,*_=qa_http
    alias(client,'原别称')
    assert request(client,oid).status_code==200
    (work/'built_scope.json').write_text(json.dumps({'objects':[{'id':'changed','table':'outsiders'}]}))
    assert request(client,oid,'restore').status_code==200
    alias(client,'新别称')
    assert client.post('/api/ont/undo',json={'graph':'built_scope'}).status_code==200
    obj=server.load_ir_edited('built_scope')['objects'][0]
    assert obj['id']=='customer' and obj['aliases']==['原别称']


def test_master_decision_write_updates_lifecycle_snapshot(qa_http):
    activate(qa_http);client=qa_http[0]
    result=client.post('/api/ont/constraints/built_scope',json={'constraint':{
        'id':'limit_amount','on':'customer','cn':'金额上限','kind':'capacity','rule':'金额不超过100'}})
    assert result.status_code==200,result.json
    saved=client.get('/api/ont/constraints/built_scope').json
    assert [row['id'] for row in saved['constraints']]==['limit_amount']
    assert client.post('/api/ont/constraints/built_scope/delete',json={'id':'limit_amount'}).status_code==200
    assert client.get('/api/ont/constraints/built_scope').json['constraints']==[]

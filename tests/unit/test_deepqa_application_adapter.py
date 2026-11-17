"""T02 RED: actual QA helpers, edited IR and SQLite; no model/SQL execution — DR-156."""
from copy import deepcopy
import importlib
import json
import sqlite3

import pytest
import server

from application_contracts import ContractError, compare_freshness
from application_contracts.adapters import registry
from ontology_assets import AssetStore


@pytest.fixture
def qa(tmp_path, monkeypatch):
    main, uploads = tmp_path / 'main.db', tmp_path / 'uploads.db'
    for path, tables in [(main, ['customers', 'orders', 'outside']), (uploads, ['upload_orders'])]:
        with sqlite3.connect(path) as db:
            for table in tables:
                db.execute(f'CREATE TABLE "{table}" (id INTEGER, amount REAL)')
                db.execute(f'INSERT INTO "{table}" VALUES (1,10)')
    graphs = {
        'demo': {'objects': [{'id': 'customer', 'cn': '客户', 'table': 'customers'}], 'links': []},
        'one': {'objects': [{'id': 'customer', 'cn': '客户', 'tables': ['customers']},
                            {'id': 'missing', 'table': 'missing_orders'}], 'links': []},
        'two': {'objects': [{'id': 'orders', 'cn': '订单', 'table': 'orders'}], 'links': []},
        'upload': {'objects': [{'id': 'upload', 'cn': '上传订单', 'tables': ['upload_orders']}], 'links': []},
        'unbound': {'objects': [{'id': 'bare', 'cn': '无绑定对象'}], 'links': []},
        'missing': {'objects': [{'id': 'missing', 'table': 'missing_orders'}], 'links': []},
    }
    monkeypatch.setattr(server, 'DB', str(main)); monkeypatch.setattr(server, 'UPLOAD_DB', str(uploads))
    monkeypatch.setattr(server, '_INV_CACHE', {'sig': None, 'val': None})
    monkeypatch.setattr(server, 'load_ir', lambda key: deepcopy(graphs.get(key)))
    monkeypatch.setattr(server, '_load_edits', lambda key: {'ops': []})
    monkeypatch.setattr(server, '_graph_name', lambda key: key)
    def forbidden(*args, **kwargs):
        pytest.fail('Contract inspection cannot execute business SQL or call a model')
    for name in ('q', 'agent_sql_plan', 'narrative_llm'):
        monkeypatch.setattr(server, name, forbidden)
    return graphs, main, uploads


def adapter(**kwargs):
    return importlib.import_module('application_contracts.deepqa').DataQAAdapter(server, **kwargs)


def ask(sql='SELECT COUNT(*) FROM customers', **kw):
    return adapter().evaluate(graph_keys=['one'], question='客户有多少条记录？',
                              plan={'analyses': [{'sql': sql}]}, **kw)


def test_dedicated_registration_has_no_robotics_handlers_or_executor(qa):
    spec = registry(data_qa=adapter()).resolve('data.ask')
    assert spec['application_id'] == 'data.deep_qa' and spec['availability'] == 'available'
    assert spec['accepted_payload_kinds'] == ['datamind_ir']
    assert spec['asset_cardinality']['max'] > 1
    assert spec['repair_handlers'] == {} and spec['executor_id'] == 'data.ask'
    assert registry().resolve('data.ask')['availability'] == 'unavailable'


def test_effective_edits_change_snapshot_without_mutating_source_or_creating_store(qa, tmp_path, monkeypatch):
    graphs, _, _ = qa
    edits = tmp_path / 'edits.json'
    edits.write_text(json.dumps({'ops': [{'op': 'set_alias', 'target': 'obj:customer', 'params': {'aliases': ['客户登记']}}]}))
    monkeypatch.setattr(server, '_load_edits', lambda key: json.loads(edits.read_text()) if key == 'one' else {'ops': []})
    a = adapter(); before = deepcopy(graphs)
    data = a.resolve(graph_keys=['one']); raw = json.loads(data['snapshots'][0]['payload'])
    assert raw['objects'][0]['aliases'] == ['客户登记']
    original = data['snapshots'][0]['version_id']
    edits.write_text(json.dumps({'ops': []}))
    assert a.resolve(graph_keys=['one'])['snapshots'][0]['version_id'] != original
    assert graphs == before and not list(tmp_path.rglob('domain.sqlite3'))


@pytest.mark.parametrize('keys,expected', [(None, ['demo']), ([], ['demo']), (['absent'], ['demo']),
    (['one','two','one'], ['one','two']), (['two','one'], ['two','one'])])
def test_legacy_resolution_preserves_actual_order_and_recorded_fallback(qa, keys, expected):
    data = adapter().resolve(graph_keys=keys)
    info = data['environment']['deep_qa']
    assert info['actual_keys'] == expected
    assert len(data['snapshots']) == len(expected)
    if keys == ['absent']:
        report = adapter().evaluate(graph_keys=keys)
        assert any(i['code'] == 'data.legacy_fallback' and i['severity'] == 'warning' for i in report['issues'])


def test_multigraph_report_and_basis_keep_order_and_queryable_union(qa):
    a = adapter(); left = a.evaluate(graph_keys=['one','two']); right = a.evaluate(graph_keys=['two','one'])
    assert left['basis']['assets'] != right['basis']['assets']
    assert left['preparation_status'] == 'available'
    data = a.resolve(graph_keys=['one','two'])
    assert set(data['environment']['deep_qa']['profile']['available_tables']) == {'customers','orders'}


@pytest.mark.parametrize('key,code,needle', [('unbound','data.table_unbound','绑定'),('missing','data.table_unavailable','接入')])
def test_unqueryable_report_locates_binding_or_connection_repair(qa, key, code, needle):
    report = adapter().evaluate(graph_keys=[key])
    assert report['preparation_status'] == 'blocked'
    issue = next(i for i in report['issues'] if i['code'] == code)
    assert issue['severity'] == 'blocking' and needle in issue['message']
    assert issue['entity_refs'] and issue['evidence_refs']


def test_partial_tables_warn_but_usable_plan_is_ready(qa):
    report = ask()
    assert report['execution_status'] == 'ready'
    issue = next(i for i in report['issues'] if i['code'] == 'data.table_unavailable')
    assert issue['severity'] == 'warning' and 'missing_orders' in issue['entity_refs']
    assert report['execution_readiness'] == 'not_verified'


@pytest.mark.parametrize('sql', ['SELECT * FROM missing_orders', 'SELECT * FROM outside',
    'SELECT * FROM "outside"', 'SELECT missing_column FROM customers',
    'DELETE FROM customers', 'SELECT 1; SELECT 2', 'WITH x AS (SELECT * FROM outside) SELECT * FROM x'])
def test_sql_guards_block_unavailable_unselected_columns_write_and_stacked_queries(qa, sql):
    report = ask(sql)
    assert report['execution_status'] == 'blocked'
    assert any(i['layer']=='task' and i['severity']=='blocking' for i in report['issues'])


@pytest.mark.parametrize('sql', ['SELECT COUNT(*) FROM customers', 'SELECT ";" FROM customers',
    'WITH c AS (SELECT id FROM customers) SELECT COUNT(*) FROM c'])
def test_plain_queries_do_not_require_metric_contracts(qa, sql):
    assert ask(sql)['execution_status'] == 'ready'


def test_focus_can_only_narrow_ontology_scope(qa):
    assert ask(focus_tables=['outside'])['execution_status'] == 'blocked'
    assert ask(focus_tables=['customers'])['execution_status'] == 'ready'


def test_upload_binding_uses_existing_qualified_scope(qa):
    a=adapter(); data=a.resolve(graph_keys=['upload'])
    assert data['environment']['deep_qa']['ir']['objects'][0]['table']=='up.upload_orders'
    report=a.evaluate(graph_keys=['upload'], question='多少记录', plan={'analyses':[{'sql':'SELECT COUNT(*) FROM up.upload_orders'}]})
    assert report['execution_status']=='ready'


@pytest.mark.parametrize('question,plan', [(None,None),('客户多少条',None),('客户多少条',{'analyses':[]})])
def test_absent_or_empty_plan_never_authorizes_execution(qa, question, plan):
    report=adapter().evaluate(graph_keys=['one'],question=question,plan=plan)
    assert report['preparation_status']=='available' and report['execution_status']!='ready'


@pytest.mark.parametrize('status',['certified','verified','candidate','heuristic'])
def test_metric_consumer_keeps_existing_status_and_card_semantics(qa,status):
    graphs,_,_=qa
    graphs['one']['metric_layers']={'atomic':[{'name':'订单金额','status':status,'table':'customers','value_col':'amount',
        'measure':{'agg':'SUM','col':'amount'},'filters':[],'time':{}}]}
    data=adapter().resolve(graph_keys=['one'],question='订单金额',plan={'analyses':[{'sql':'SELECT SUM(amount) FROM customers'}]})
    assert data['environment']['deep_qa']['metric_cards']==server._metric_cards('订单金额',graphs['one'])
    assert data['environment']['deep_qa']['metric_cards'][0]['status']==status
    assert adapter().evaluate(graph_keys=['one'],question='订单金额',plan={'analyses':[{'sql':'SELECT SUM(amount) FROM customers'}]})['execution_status']=='ready'


def test_no_model_offline_planner_reuses_effective_ontology_and_focus(qa):
    a=adapter(); data=a.resolve(graph_keys=['one'],question='客户有多少条记录？',focus_tables=['customers'])
    plan=a.offline_plan(data)
    assert plan==server.fallback_plan('客户有多少条记录？',ir=data['environment']['deep_qa']['ir'],focus_tables=['customers'])
    assert 'customers' in plan['analyses'][0]['sql']


def test_catalog_revision_and_effective_content_invalidate_report_basis(qa):
    graphs,main,_=qa
    a=adapter(); before=a.evaluate(graph_keys=['one'])
    graphs['one']['objects'][0]['cn']='新客户'
    after=a.evaluate(graph_keys=['one'])
    assert compare_freshness(before,after['basis'])=='stale'
    with sqlite3.connect(main) as db: db.execute('CREATE TABLE missing_orders (id INTEGER)')
    changed=a.evaluate(graph_keys=['one'])
    assert compare_freshness(after,changed['basis'])=='stale'


def test_registered_version_is_readonly_and_does_not_require_legacy_key(qa,tmp_path):
    store=AssetStore(tmp_path/'assets'); asset=store.create_asset(name='客户',domain='data')
    version=store.add_version(asset['ontology_id'],json.dumps(qa[0]['one']).encode(),payload_kind='datamind_ir')
    refs=[{'ontology_id':asset['ontology_id'],'version_id':version['version_id']}]
    before={str(p):p.read_bytes() for p in (tmp_path/'assets').rglob('*') if p.is_file()}
    report=adapter(assets=AssetStore(tmp_path/'assets',read_only=True)).evaluate(asset_refs=refs)
    assert report['asset_refs']==refs and report['preparation_status']=='available'
    assert before=={str(p):p.read_bytes() for p in (tmp_path/'assets').rglob('*') if p.is_file()}


@pytest.mark.parametrize('kwargs',[{'graph_keys':['../private']},{'graph_keys':'one'},
    {'graph_keys':['one'],'asset_refs':[{'ontology_id':'a','version_id':'v'}]},
    {'asset_refs':[{'ontology_id':'a','version_id':'latest'}]}])
def test_invalid_or_mixed_references_fail_before_query_callbacks(qa,kwargs):
    with pytest.raises(ContractError): adapter().evaluate(**kwargs)


def test_foreign_registered_asset_never_enters_sql_validation(qa,tmp_path,monkeypatch):
    store=AssetStore(tmp_path/'assets'); asset=store.create_asset(name='机器人',domain='robotics')
    version=store.add_version(asset['ontology_id'],b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',payload_kind='robotics_rdf')
    def forbidden(*a,**kw): pytest.fail('Foreign payload must not reach SQL validation')
    monkeypatch.setattr(server,'_qa_validate_sql',forbidden)
    with pytest.raises(ContractError) as error:
        adapter(assets=store).evaluate(asset_refs=[{'ontology_id':asset['ontology_id'],'version_id':version['version_id']}],question='多少',plan={'analyses':[{'sql':'SELECT 1'}]})
    assert error.value.code=='unsupported_payload'


def test_checker_failure_is_error_without_leaking_exception(qa,monkeypatch):
    a=adapter(); data=a.resolve(graph_keys=['one'],question='客户数量',plan={'analyses':[{'sql':'SELECT 1'}]})
    def broken(*a,**kw): raise RuntimeError('secret-token-private-db')
    monkeypatch.setattr(server,'_qa_validate_sql',broken)
    from application_contracts import evaluate
    report=evaluate(registry=registry(data_qa=a),entrypoint='data.ask',**data)
    assert report['execution_status']=='blocked'
    assert any(i['code']=='system.checker_failed' for i in report['issues'])
    assert 'secret-token' not in json.dumps(report)


@pytest.mark.parametrize('plan',[{'analyses':[{}]},{'analyses':'invalid'},{'analyses':[{'sql':'SELECT 1'}]*5}])
def test_malformed_or_oversized_plan_is_rejected(qa,plan):
    with pytest.raises(ContractError): adapter().resolve(graph_keys=['one'],question='客户数量',plan=plan)


def test_snapshots_and_offline_plans_are_detached_from_inputs(qa):
    graphs,_,_=qa; original=deepcopy(graphs)
    focus=['customers']; plan={'analyses':[{'sql':'SELECT COUNT(*) FROM customers'}]}
    data=adapter().resolve(graph_keys=['one'],question='客户数量',plan=plan,focus_tables=focus)
    plan['analyses'][0]['sql']='DELETE FROM customers';focus.append('outside')
    assert data['task']['content']['plan']['analyses'][0]['sql'].startswith('SELECT')
    assert data['environment']['deep_qa']['focus_tables']==['customers']
    assert graphs==original


def test_legacy_resolution_failure_does_not_silently_pass(qa,monkeypatch):
    def broken(*a): raise OSError('private-connection-token')
    monkeypatch.setattr(server,'_qa_table_inventory',broken)
    with pytest.raises(ContractError) as error: adapter().resolve(graph_keys=['one'])
    assert error.value.code=='resolution_failed' and 'private' not in str(error.value)

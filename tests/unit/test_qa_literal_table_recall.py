"""T06 user acceptance: literal table names beside Chinese text — DR-074."""
import json
import sqlite3

import pytest
import server
import intent_check

from tests.unit.test_deepqa_application_http import qa_http, ask, ROUTES

__all__ = ['qa_http']


@pytest.fixture
def literal_qa(qa_http):
    _, work, db, ir, _ = qa_http
    objects = [
        {'id': 'fact_ap', 'name': 'fact_ap', 'cn': '应付账款', 'table': 'fact_ap'},
        {'id': 'fact_ap_archive', 'table': 'fact_ap_archive'},
    ]
    # Generic words such as “记录” must not crowd an explicitly named table out.
    objects += [{'id': f'noise_{i}', 'table': f'noise_{i}', 'cn': '记录',
                 'attrs': [{'col': 'id', 'cn': '数量'}]} for i in range(10)]
    ir['objects'] = objects
    for key in ('built_scope', 'built_second', 'demo'):
        (work / (key + '.json')).write_text(json.dumps(ir))
    with sqlite3.connect(db) as connection:
        for obj in objects:
            connection.execute(f'CREATE TABLE "{obj["table"]}"(id INTEGER)')
        connection.executemany('INSERT INTO fact_ap VALUES(?)', [(i,) for i in range(80)])
    return qa_http


@pytest.mark.parametrize('question,table', [
    ('fact_ap这个表有多少条记录?', 'fact_ap'),
    ('FACT_AP这张表的记录数量是多少？', 'fact_ap'),
    ('请统计fact_ap的记录数量', 'fact_ap'),
    ('fact_ap_archive这个表有多少条记录？', 'fact_ap_archive'),
])
def test_literal_table_survives_chinese_adjacency_and_generic_hits(literal_qa, question, table):
    trace = {}
    context = server.build_context(question, trace=trace, graph_keys=['built_scope'])
    assert trace['objects'][0]['table'] == table
    assert table in [obj['table'] for obj in trace['objects']]
    assert f'表 {table}(' in context
    assert any(hit.lower() == table for hit in trace['objects'][0]['hits'])


@pytest.mark.parametrize('name', ['fact_ap_backup', 'myfact_ap', 'fact_ap2'])
def test_literal_table_does_not_match_a_different_identifier(literal_qa, name):
    trace = {}
    server.build_context(name + '这个表有多少条记录？', trace=trace, graph_keys=['built_scope'])
    assert 'fact_ap' not in [obj['table'] for obj in trace['objects']]


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('question', [
    'fact_ap这个表有多少条记录?',
    'fact_ap这张表有多少条记录？',
    'fact_ap 有多少条记录？',
    '请问fact_ap这个表有多少条记录？',
    'FACT_AP这个表有多少条记录？',
])
def test_literal_count_offline_returns_actual_rows_in_both_flows(literal_qa, route, question):
    answer, response, _ = ask(literal_qa, route, q=question, nocache=True)
    assert response.status_code == 200
    assert answer['results'][0]['data']['rows'] == [{'记录数': 80}]
    assert answer['anchor']['used'] == ['fact_ap']
    assert answer['application_check']['preparation_status'] == 'available'


@pytest.mark.parametrize('route', ROUTES)
@pytest.mark.parametrize('question', [
    'fact_ap这个表最近一个月有多少条记录？',
    'fact_ap这个表有多少条记录属于高风险？',
    'fact_ap这个表金额大于100的有多少条记录？',
])
def test_natural_count_never_drops_business_filters(literal_qa, route, question):
    answer, _, _ = ask(literal_qa, route, q=question, nocache=True)
    assert answer['results'] == []
    assert literal_qa[-1]['sql'] == []
    assert '未覆盖' in answer['narrative']


@pytest.mark.parametrize('route', ROUTES)
def test_literal_name_cannot_override_explicit_table_scope(literal_qa, route):
    answer, _, _ = ask(literal_qa, route, q='fact_ap这个表有多少条记录？',
                       tables=['fact_ap_archive'], nocache=True)
    assert answer['results'] == []
    assert literal_qa[-1]['sql'] == []


@pytest.mark.parametrize('sql', [
    'SELECT COUNT(*) FROM "fact_ap"',
    'SELECT COUNT(*) FROM `fact_ap`',
    'SELECT COUNT(*) FROM [fact_ap]',
    'WITH "picked" AS (SELECT * FROM "fact_ap") SELECT COUNT(*) FROM "picked"',
    'SELECT COUNT(*) FROM "up"."fact_ap"',
])
def test_successful_quoted_sql_is_counted_as_actual_table_use(sql):
    explanation = server._qa_anchor_explanation({'objects': [{'table': 'fact_ap'}]}, [{'sql': sql}])
    assert explanation['used_tables'] == ['fact_ap']
    assert explanation['context_only_tables'] == []


@pytest.mark.parametrize('sql', [
    'SELECT COUNT(*) FROM "fact_ap"',
    'SELECT COUNT(*) FROM `fact_ap`',
    'SELECT COUNT(*) FROM [fact_ap]',
    'WITH "picked" AS (SELECT * FROM "fact_ap") SELECT COUNT(*) FROM "picked"',
    'SELECT COUNT(*) FROM "up"."fact_ap"',
])
def test_quoted_table_does_not_trigger_false_intent_mismatch(sql):
    ir = {'objects': [{'id': 'fact_ap', 'table': 'fact_ap'}]}
    result = intent_check.cross_check('fact_ap这个表有多少条记录？', sql, ir)
    assert result['verdict'] == 'aligned'
    assert result['actual']['tables'] == ['fact_ap']

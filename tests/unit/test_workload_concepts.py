"""DR-093: automatic proposals remain candidates, queries are strictly read-only."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest


def _build(ask, questions=None):
    import workload_concepts as wc
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE facts(code TEXT, value REAL); INSERT INTO facts VALUES ('A',1),('B',2);")
    try:
        return wc.build(con, {"objects": [{"name": "fact", "table": "facts"}]}, {},
                        questions or ["amount"], ask, json.loads)
    finally:
        con.close()


def test_proposals_have_replayable_evidence_but_no_semantic_certification():
    reply = {"concepts": [{"name": "Amount", "table": "facts", "column": "value", "filter": "code='A'"}],
             "derived": [{"name": "Twice", "formula": "Amount * 2", "uses": ["Amount"]}],
             "themes": [{"name": "Totals", "table": "facts", "name_col": "code", "codes": ["A", "B", "absent"]}],
             "caveats": [{"statement": "Review duplicates", "check_sql": "SELECT COUNT(*) FROM facts"}]}
    out = _build(lambda p: json.dumps(reply))
    c = out["concepts"][0]
    assert c["status"] == "candidate" and c["semantic_status"] == "not_reviewed"
    assert c["evidence"]["query_verified"] is True and c["evidence"]["rows"] == 1
    assert "SELECT" in c["evidence"]["sql"]
    assert out["derived"][0]["status"] == "candidate"
    assert len(out["themes"][0]["codes"]) == 2 and out["rejected"]
    assert out["proposed"] == out["accepted"] + out["dropped"]


@pytest.mark.parametrize("sql", ["WITH x AS (SELECT 1) DELETE FROM facts", "DELETE FROM facts",
                                  "SELECT load_extension('x')", "SELECT 1; DELETE FROM facts"])
def test_proposal_queries_cannot_modify_even_a_writable_connection(sql):
    import workload_concepts as wc
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE facts(value INTEGER); INSERT INTO facts VALUES (1);")
    with pytest.raises((ValueError, sqlite3.Error)):
        wc._run(con, sql)
    assert con.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    con.close()


def test_all_1000_questions_are_processed_and_invalid_input_is_reported():
    calls = []
    out = _build(lambda p: calls.append(p) or '{}', [f"question {i}" for i in range(1000)])
    assert out["questions_used"] == 1000 and out["questions_omitted"] == 0
    assert "question 999" in calls[-1]
    out = _build(lambda p: '{}', ["one", {"question": "not a string"}, "", "two"])
    assert out["questions_used"] == 2 and out["invalid_questions"] == 2


def test_duplicate_filters_preserve_distinct_literal_values_and_rejections_are_local():
    reply = {"concepts": [{"name": "Upper", "table": "facts", "column": "value", "filter": "code='A'"},
                          {"name": "Lower", "table": "facts", "column": "value", "filter": "code='a'"}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda r: _build(lambda p: json.dumps(r)),
                             [reply, {"concepts": [{"name": "Missing", "table": "missing"}]}]))
    assert len(a["concepts"]) == 1 and len(a["rejected"]) == 1
    assert len(b["concepts"]) == 0 and b["rejected"][0]["name"] == "Missing"


def test_sql_literals_may_contain_semicolons_and_saved_filters_equal_executed_filters():
    import workload_concepts as wc
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE facts(value TEXT); INSERT INTO facts VALUES ('a;b');")
    assert wc._run(con, "SELECT value FROM facts WHERE value='a;b'") == [('a;b',)]
    long_filter = "value='a;b'" + " AND 1=1" * 100
    c = wc.verify_concept(con, {"name": "Semicolon", "table": "facts", "column": "value", "filter": long_filter},
                          {"facts": "facts"})
    assert c["filter"] == long_filter and long_filter in c["evidence"]["sql"]
    con.close()


def test_refresh_revokes_removed_data_and_merge_replaces_revalidated_binding():
    import workload_concepts as wc
    con = sqlite3.connect(':memory:')
    con.executescript("CREATE TABLE facts(code TEXT, value REAL); INSERT INTO facts VALUES ('A',1),('a',2);")
    out = wc.build(con, {'objects': [{'table': 'facts'}]}, {}, ['amount'],
                   lambda p: json.dumps({'concepts': [
                       {'name': 'Amount', 'table': 'facts', 'column': 'value', 'filter': "code='A'"},
                       {'name': 'Amount', 'table': 'facts', 'column': 'value', 'filter': "code='a'"}]}), json.loads)
    assert len(out['concepts']) == 2
    con.execute("DELETE FROM facts WHERE code='A'")
    assert wc.refresh(con, out) == 1
    text, _ = wc.context('Amount', out)
    assert "code='A'" not in text and "code='a'" in text
    revived = wc.verify_concept(con, {'name': 'Amount', 'table': 'facts', 'column': 'value', 'filter': "code='a'"}, {'facts': 'facts'})
    revived['binding_status'] = 'supported'
    merged = wc.merge_layers(out, {'concepts': [revived]})
    assert len(merged['concepts']) == 2
    con.close()


def test_derived_inputs_require_unambiguous_bindings_across_sources():
    import workload_concepts as wc
    reply = {"concepts": [{"name": "Amount", "table": "facts", "column": "value", "filter": "code='A'"},
                          {"name": "Amount", "table": "facts", "column": "value", "filter": "code='B'"}],
             "derived": [{"name": "Double", "formula": "Amount*2", "uses": ["Amount"]}]}
    out = _build(lambda p: json.dumps(reply))
    assert not out['derived']
    assert any(c['reason'] == 'derived_inputs_missing_or_ambiguous' for c in out['rejected'])
    ir = {'concepts': reply['concepts'], 'derived': reply['derived'], 'objects': [{'table': 'facts'}]}
    assert not wc.scoped(ir, ['facts'])['derived']


def test_theme_refresh_does_not_silently_change_a_reviewed_binding():
    import workload_concepts as wc
    con = sqlite3.connect(':memory:')
    con.executescript("CREATE TABLE facts(code TEXT); INSERT INTO facts VALUES ('A'),('B'),('C');")
    th = wc.verify_theme(con, {'name': 'Totals', 'table': 'facts', 'name_col': 'code', 'codes': ['A','B','C']},
                         {'facts': 'facts'}, {})
    th.update(status='asserted', semantic_status='asserted', review_by='Reviewer')
    con.execute("DELETE FROM facts WHERE code='C'")
    ir = {'themes': [th]}
    assert wc.refresh(con, ir) == 1
    assert [x['code'] for x in th['codes']] == ['A','B','C']
    assert th['binding_status'] == 'stale' and th['review_by'] == 'Reviewer'
    con.close()


def test_caveat_query_respects_selected_table_scope():
    import workload_concepts as wc
    con = sqlite3.connect(':memory:')
    con.executescript('CREATE TABLE facts(value INTEGER); CREATE TABLE other(value INTEGER); INSERT INTO other VALUES (1);')
    ir = {'objects': [{'table': 'facts'}]}
    out = wc.build(con, ir, {}, ['amount'], lambda p: json.dumps({'caveats': [
        {'statement': 'Outside table advice', 'check_sql': 'SELECT value FROM other'}]}), json.loads)
    assert not out['caveats']
    con.close()


def test_expired_refresh_marks_unknown_without_claiming_data_disappeared():
    import time
    import workload_concepts as wc
    con = sqlite3.connect(':memory:')
    con.executescript('CREATE TABLE facts(value INTEGER); INSERT INTO facts VALUES (1);')
    c = wc.verify_concept(con, {'name': 'Amount', 'table': 'facts', 'column': 'value'}, {'facts': 'facts'})
    ir = {'concepts': [c]}
    assert wc.refresh(con, ir, deadline=time.monotonic()-1) == 0
    assert c['binding_status'] == 'unresolved' and c['evidence']['query_verified'] is None
    assert not wc.context('Amount', ir)[0]
    con.close()


def test_caveats_require_data_and_serializable_evidence():
    import workload_concepts as wc
    con = sqlite3.connect(':memory:')
    con.executescript("CREATE TABLE facts(value BLOB); INSERT INTO facts VALUES (X'ABCD');")
    assert wc.verify_caveat(con, {'statement': 'unsupported', 'check_sql': 'SELECT 1'}) is None
    assert wc.verify_caveat(con, {'statement': 'blob', 'check_sql': 'SELECT value FROM facts'}) is None
    con.close()


def test_saved_formula_binding_does_not_switch_when_name_collides():
    import workload_concepts as wc
    reply = {'concepts': [{'name': 'Amount', 'table': 'facts', 'column': 'value', 'filter': "code='A'"}],
             'derived': [{'name': 'Double', 'formula': 'Amount*2', 'uses': ['Amount']}]}
    out = _build(lambda p: json.dumps(reply))
    second = dict(out['concepts'][0], table='other')
    ir = {**out, 'objects': [{'table': 'facts'}, {'table': 'other'}], 'concepts': [*out['concepts'], second]}
    assert wc.scoped(ir, ['facts'])['derived']
    assert not wc.scoped(ir, ['other'])['derived']


def test_interrupted_theme_recheck_does_not_claim_codes_disappeared(monkeypatch):
    import workload_concepts as wc
    con = sqlite3.connect(':memory:')
    con.executescript("CREATE TABLE facts(code TEXT); INSERT INTO facts VALUES ('A'),('B');")
    th = wc.verify_theme(con, {'name': 'Totals', 'table': 'facts', 'name_col': 'code', 'codes': ['A','B']},
                         {'facts': 'facts'}, {})
    def interrupted(*args, **kwargs):
        raise sqlite3.OperationalError('interrupted')
    monkeypatch.setattr(wc, '_run', interrupted)
    assert wc.refresh(con, {'themes': [th]}) == 0
    assert th['binding_status'] == 'unresolved' and th['evidence']['query_verified'] is None
    con.close()

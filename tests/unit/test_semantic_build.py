"""DR-093: declared FK checks and semantic consumption use production functions."""
import sqlite3

import server


def test_workload_validation_rejects_non_lists():
    response = server.app.test_client().post('/api/build/inquire', json={'q': 'Build', 'workload': 'question'})
    assert response.status_code == 400 and response.get_json()['field'] == 'workload'


def test_concept_alias_recalls_bound_table_and_respects_focus(monkeypatch, make_sqlite):
    db = make_sqlite({'facts': ('value REAL', [(2,)]), 'other': ('value REAL', [(1,)])})
    ir = {'objects': [{'name': 'f', 'table': 'facts', 'attrs': [{'col': 'value'}]},
                      {'name': 'o', 'table': 'other', 'attrs': [{'col': 'value'}]}],
          'relations': [], 'concepts': [{'name': 'Output', 'aliases': ['产量'], 'table': 'facts',
                                       'column': 'value', 'status': 'candidate'}]}
    monkeypatch.setattr(server, '_anchor_ir', lambda *a: (ir, ['built_test'], False))
    token = server._QA_DB.set(db)
    try:
        trace = {}
        ctx = server.build_context('产量是多少', trace=trace)
        assert 'Output' in ctx and any(o['table'] == 'facts' and '产量' in o['hits'] for o in trace['objects'])
        assert 'Output' not in server.build_context('产量是多少', focus_tables=['other'])
    finally:
        server._QA_DB.reset(token)


def test_candidate_concepts_are_counted_in_quality_review():
    import build_quality
    ir = {'objects': [{'name': 'f', 'table': 'facts'}], 'relations': [],
          'concepts': [{'name': 'Output', 'status': 'candidate'}]}
    q = build_quality.evaluate(ir)
    assert any(x['type'] == 'semantic_proposals' and x['count'] == 1 for x in q['review_queue'])


def test_declared_role_keys_require_their_own_complete_check():
    import build_quality
    ev = {'source': 'declared_fk', 'child_key': 'first_ref', 'parent_key': 'id', 'schema_validated': True,
          'orphan_count': 0, 'referencing_rows': 2,
          'role_keys': [{'source': 'declared_fk', 'child_key': 'second_ref', 'parent_key': 'id',
                         'schema_validated': True, 'orphan_count': 1, 'referencing_rows': 2}]}
    ir = {'objects': [], 'relations': [{'source_concept': 'r', 'target_concept': 'l',
                                       'status': 'verified', 'evidence': ev}]}
    assert not build_quality.audit_verified_evidence(ir)['valid']


def test_semantic_human_review_is_audited_reversible_and_never_formula_verified(tmp_path, monkeypatch):
    import json
    import workload_concepts as wc
    ir = {'objects': [{'name': 'f', 'table': 'facts', 'kind': 'object'}], 'relations': [],
          'derived': [{'name': 'Twice', 'formula': 'Output * 2', 'uses': ['Output'], 'status': 'candidate',
                       'origin': 'workload_proposal', 'evidence': {'formula_verified': False}}]}
    monkeypatch.setattr(server, 'WORK', str(tmp_path))
    (tmp_path / 'built_semantic.json').write_text(json.dumps(ir))
    client = server.app.test_client()
    row = client.get('/api/ont/review?graph=built_semantic').get_json()['semantic'][0]
    payload = {'graph': 'built_semantic', 'op': {'op': 'confirm_semantic', 'target': row['target']}}
    assert client.post('/api/ont/apply', json=payload).status_code == 400
    payload['reviewer'] = 'Reviewer'
    assert client.post('/api/ont/apply', json=payload).get_json()['ok']
    c = server.load_ir_edited('built_semantic')['derived'][0]
    assert c['status'] == 'asserted' and c['review_by'] == 'Reviewer' and c['evidence']['formula_verified'] is False
    client.post('/api/ont/undo', json={'graph': 'built_semantic'})
    assert server.load_ir_edited('built_semantic')['derived'][0]['status'] == 'candidate'
    assert row['target'].endswith(wc.proposal_id('derived', ir['derived'][0]))


def test_llm_build_recovers_declared_role_keys_without_name_guessing(make_sqlite):
    db = make_sqlite({"lookup": ("id INTEGER PRIMARY KEY, name TEXT", [(1, 'A'), (2, 'B')]),
                      "record": ("id INTEGER, first_ref INTEGER REFERENCES lookup(id), second_ref INTEGER REFERENCES lookup(id)",
                                 [(10, 1, 2), (11, 2, 1)])})
    proposal = {"objects": [{"name": "record", "table": "record"}, {"name": "lookup", "table": "lookup"}],
                "relations": []}
    ir = server._adjudicate_ir(db, "test", proposal, server._gather_evidence(db))
    relation = ir["relations"][0]
    assert relation["status"] == "verified" and relation["evidence"]["source"] == "declared_fk"
    keys = {relation["evidence"]["child_key"]} | {r["child_key"] for r in relation["evidence"].get("role_keys", [])}
    assert keys == {"first_ref", "second_ref"}
    sql = "SELECT r.id FROM record r JOIN lookup l ON r.second_ref=l.id"
    assert server._validate_sql_ontology(sql, ir, strict=True)[0]
    assert "record.second_ref = lookup.id" in "\n".join(server._join_hints(ir, ["record", "lookup"]))


def test_declared_orphans_and_empty_references_stay_candidates(make_sqlite):
    db = make_sqlite({"lookup": ("id INTEGER PRIMARY KEY", [(1,)]) ,
                      "record": ("id INTEGER, lookup_id INTEGER REFERENCES lookup(id)", [(10, 1), (11, 9)])})
    p = {"objects": [{"name": "record", "table": "record"}, {"name": "lookup", "table": "lookup"}],
         "relations": [{"source": "record", "target": "lookup", "child_key": "lookup_id", "parent_key": "id"}]}
    ir = server._adjudicate_ir(db, "test", p, server._gather_evidence(db))
    assert ir["relations"][0]["status"] == "candidate"
    assert ir["relations"][0]["evidence"]["orphan_count"] == 1
    with sqlite3.connect(db) as con:
        con.execute("UPDATE record SET lookup_id=NULL")
    ir = server._adjudicate_ir(db, "test", p, server._gather_evidence(db))
    assert ir["relations"][0]["status"] == "candidate"
    assert ir["relations"][0]["evidence"]["referencing_rows"] == 0


def test_multigraph_anchor_keeps_semantics_without_cross_source_names_colliding(monkeypatch):
    irs = {"built_a": {"objects": [{"name": "A", "table": "a"}], "relations": [],
                       "concepts": [{"name": "Amount", "table": "a", "column": "value"}]},
           "built_b": {"objects": [{"name": "B", "table": "b"}], "relations": [],
                       "concepts": [{"name": "Amount", "table": "b", "column": "value"}]}}
    monkeypatch.setattr(server, "load_ir_edited", lambda k: irs.get(k))
    ir, _, _ = server._anchor_ir(["built_a", "built_b"])
    assert {c["table"] for c in ir["concepts"]} == {"a", "b"}


def test_reviewed_semantic_binding_that_is_stale_still_requires_review(monkeypatch):
    import build_quality
    ir = {'objects': [{'name': 'f', 'table': 'facts'}], 'relations': [],
          'concepts': [{'name': 'Amount', 'status': 'asserted', 'binding_status': 'stale'}]}
    report = build_quality.evaluate(ir)
    assert any(x['type'] == 'semantic_bindings_unavailable' and x['count'] == 1 for x in report['review_queue'])
    monkeypatch.setattr(server, 'load_ir_edited', lambda key: ir)
    data = server.app.test_client().get('/api/ont/review?graph=built_semantic').get_json()
    assert data['counts']['semantic_pending'] == 1


def test_declared_fk_recheck_preserves_human_rejection_and_assertion():
    import declared_fk
    with sqlite3.connect(':memory:') as con:
        con.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY);'
                          'CREATE TABLE child(id INTEGER, ref INTEGER REFERENCES parent(id));'
                          'INSERT INTO parent VALUES(1); INSERT INTO child VALUES(2,1);')
        for status in ('asserted', 'rejected'):
            relation = {'source_concept': 'c', 'target_concept': 'p', 'status': status,
                        'human_review': 'approved' if status == 'asserted' else 'rejected',
                        'review_by': 'Reviewer', 'review_reason': 'Reviewed business meaning',
                        'evidence': {'child_key': 'ref', 'parent_key': 'id'}, 'note': 'Reviewed note'}
            ir = {'objects': [{'name': 'c', 'table': 'child'}, {'name': 'p', 'table': 'parent'}],
                  'relations': [relation]}
            declared_fk.apply(con, ir)
            assert relation['status'] == status
            assert relation['note'] == 'Reviewed note' and relation['review_by'] == 'Reviewer'
            assert relation['declared_checks'][0]['schema_validated'] is True
            assert relation['declared_checks'][0]['referencing_rows'] == 1

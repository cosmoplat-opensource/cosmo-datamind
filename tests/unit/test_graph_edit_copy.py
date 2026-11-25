"""编辑副本以完整显式 RDF 为依据 —— DR-188。"""
import pytest
from rdflib import BNode, Graph, Literal, Namespace, OWL, RDF, RDFS, XSD

from tests.build_runtime_support import BASE_URL, http_app
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_task_support import fixture

N = Namespace('urn:sample:')


def graph():
    g = Graph()
    for s, p, o in [
        (N.A, RDF.type, OWL.Class), (N.B, RDF.type, OWL.Class), (N.B, RDFS.subClassOf, N.A),
        (N.p, RDF.type, OWL.ObjectProperty), (N.p, RDFS.domain, N.A),
        (N.v, RDF.type, OWL.DatatypeProperty), (N.x, RDF.type, N.A), (N.y, RDF.type, N.B),
        (N.x, N.p, N.y), (N.y, N.p, N.x), (N.x, N.v, Literal(str(N.y))),
        (N.x, N.v, Literal('v', lang='en')), (N.x, N.v, Literal('v', lang='zh')),
    ]:
        g.add((s, p, o))
    return g


def apply(g, **op):
    from ontology_assets.graph_edit_copy import apply_edit
    return apply_edit(g, {k: str(v) for k, v in op.items()})


def test_create_edit_and_link_preserve_unrelated_facts():
    g = graph()
    apply(g, op='create', iri=N.C, kind='class', name='新增', parent=N.A)
    apply(g, op='create', iri=N.z, kind='individual', name='实例', parent=N.C)
    apply(g, op='link', source=N.z, predicate=N.p, target=N.y)
    g.add((N.z, RDFS.label, Literal('Old', lang='en')))
    apply(g, op='edit', iri=N.z, new_iri=N.new, name='新名称', comment='说明')
    assert (N.C, RDFS.subClassOf, N.A) in g
    assert (N.new, N.p, N.y) in g and (N.new, RDF.type, N.C) in g
    assert (N.new, RDFS.label, Literal('Old', lang='en')) in g
    assert (N.new, RDFS.label, Literal('新名称', lang='zh')) in g


def test_unlink_and_delete_exact_facts_preserve_other_identities():
    g = graph()
    before = set(g)
    apply(g, op='unlink', source=N.x, predicate=N.p, target=N.y)
    assert before - set(g) == {(N.x, N.p, N.y)}
    apply(g, op='remove_value', source=N.x, predicate=N.v, value='v', language='ZH', datatype='')
    assert (N.x, N.v, Literal('v', lang='en')) in g
    assert (N.x, N.v, Literal('v', lang='zh')) not in g
    apply(g, op='delete', iri=N.y)
    assert not any(N.y in triple for triple in g)
    assert (N.x, N.v, Literal(str(N.y))) in g
    apply(g, op='delete', iri=N.A)
    assert (N.x, RDF.type, OWL.NamedIndividual) in g
    assert (N.B, RDF.type, OWL.Class) in g and (N.p, RDF.type, OWL.ObjectProperty) in g
    apply(g, op='delete', iri=N.v)
    assert not any(N.v in triple for triple in g)


@pytest.mark.parametrize('action', ['collision', 'missing', 'anonymous', 'protected', 'punning', 'bad_input'])
def test_rejected_edit_is_atomic(action):
    g = graph()
    op = dict(op='delete', iri=N.x)
    if action == 'collision':
        op = dict(op='create', iri=N.p, kind='class', name='冲突')
    elif action == 'missing':
        op = dict(op='link', source=N.x, predicate=N.p, target=N.absent)
    elif action == 'anonymous':
        g.add((BNode(), N.unknown, N.x))
    elif action == 'protected':
        op['iri'] = XSD.string
    elif action == 'punning':
        g.add((N.x, RDF.type, OWL.Class))
    else:
        op['extra'] = 'unsupported'
    before = set(g)
    with pytest.raises(ValueError):
        apply(g, **op)
    assert set(g) == before


def test_preview_export_and_lifecycle_keep_source_immutable(tmp_path):
    from ontology_assets.lifecycle import LifecycleService
    from tests.unit.test_asset_lifecycle import apply as transition
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        oid, vid = version['ontology_id'], version['version_id']
        path = f'/api/ontology-assets/{oid}/versions/{vid}/graph-edit-copy'
        body = {'base_sha256': version['payload_sha256'], 'operations': [
            {'op': 'create', 'kind': 'class', 'name': '样例', 'iri': str(N.C)}], 'format': 'view'}
        before = db_rows(root), blob_bytes(root)
        def post(**changes):
            return client.post(path, json={**body, **changes}, base_url=BASE_URL)
        result = post()
        assert result.status_code == 200, result.json
        assert str(N.C) in {n['id'] for n in result.json['view']['nodes']}
        assert result.json['impact']['added'] == 2
        for fmt, parser in [('owl', 'xml'), ('ttl', 'turtle'), ('json', 'json-ld')]:
            exported = post(format=fmt)
            assert exported.status_code == 200
            assert (N.C, RDF.type, OWL.Class) in Graph().parse(data=exported.json['content'], format=parser)
        assert post(base_sha256='0' * 64).status_code == 409
        assert post(operations=body['operations'] * 2).status_code == 422
        assert (db_rows(root), blob_bytes(root)) == before
        transition(LifecycleService(root), oid)
        assert post().status_code == 409


def test_delete_preserves_implicit_property_and_exposes_it_in_copy():
    from ontology_assets.graph_edit_copy import render_copy
    from tests.unit.test_interactive_ontology_graph_projection import fixed
    g = graph()
    g.add((N.x, N.implicit, N.y))
    version, _ = fixed()
    preview = render_copy(g, version, [], 'view')['view']
    assert str(N.implicit) in {p['id'] for p in preview['properties']}
    apply(g, op='delete', iri=N.y)
    assert (N.implicit, RDF.type, OWL.ObjectProperty) in g


def test_annotated_value_removal_includes_nested_annotations_only():
    g = graph()
    v = Literal('v', lang='zh')
    a, b = BNode(), BNode()
    for node, sub, prop, obj in [(a, N.x, N.v, v), (b, a, RDFS.comment, Literal('原注解'))]:
        for p, o in [(RDF.type, OWL.Axiom), (OWL.annotatedSource, sub),
                     (OWL.annotatedProperty, prop), (OWL.annotatedTarget, obj)]:
            g.add((node, p, o))
    g.add((a, RDFS.comment, Literal('原注解')))
    g.add((b, RDFS.comment, Literal('第二层')))
    before = set(g)
    impact = apply(g, op='remove_value', source=N.x, predicate=N.v, value='v', datatype='', language='zh')
    expected = {t for t in before if t[0] in {a, b}} | {(N.x, N.v, v)}
    assert before - set(g) == expected and impact['removed'] == len(expected)


def test_typed_lexical_forms_survive_edit_export_and_precise_value_deletion():
    from ontology_assets.graph_edit_copy import parse_source, render_copy
    from tests.unit.test_interactive_ontology_graph_projection import fixed
    g = graph()
    for value in ['01', '1']:
        g.add((N.x, N.v, Literal(value, datatype=XSD.integer, normalize=False)))
    version, raw = fixed(g.serialize(format='xml', encoding='utf-8'))
    edited = parse_source(version, raw)
    assert len(set(edited.objects(N.x, N.v))) == len(set(g.objects(N.x, N.v)))
    op = dict(op='remove_value', source=str(N.x), predicate=str(N.v), value='1', datatype=str(XSD.integer), language='')
    content = render_copy(edited, version, [op], 'owl')['content'].encode()
    saved = parse_source(*fixed(content))
    assert (N.x, N.v, Literal('01', datatype=XSD.integer, normalize=False)) in saved
    assert (N.x, N.v, Literal('1', datatype=XSD.integer, normalize=False)) not in saved


def test_delete_named_member_preserves_shared_anonymous_restriction():
    g = graph()
    expr = BNode()
    g.add((expr, RDF.type, OWL.Restriction))
    g.add((expr, OWL.onProperty, N.p))
    g.add((expr, OWL.someValuesFrom, N.B))
    g.add((N.A, RDFS.subClassOf, expr))
    g.add((N.Other, RDFS.subClassOf, expr))
    apply(g, op='delete', iri=N.A)
    assert (N.Other, RDFS.subClassOf, expr) in g and len(list(g.triples((expr, None, None)))) == 3
    apply(g, op='delete', iri=N.p)
    assert not any(expr in t or N.p in t for t in g)
    assert (N.B, RDF.type, OWL.Class) in g


def test_invalid_value_datatype_and_cross_role_range_are_rejected():
    g = graph()
    before = set(g)
    with pytest.raises(ValueError):
        apply(g, op='add_value', source=N.x, predicate=N.v, value='bad', datatype=XSD.integer, language='')
    with pytest.raises(ValueError):
        apply(g, op='link', source=N.v, predicate=RDFS.range, target=N.A)
    assert set(g) == before


def test_named_complex_axiom_cannot_be_left_incomplete():
    g = graph()
    g.add((N.constraint, RDF.type, OWL.Restriction))
    g.add((N.constraint, OWL.onProperty, N.p))
    g.add((N.constraint, OWL.someValuesFrom, N.A))
    before = set(g)
    with pytest.raises(ValueError):
        apply(g, op='delete', iri=N.p)
    assert set(g) == before


@pytest.mark.parametrize('ops', [None, [{}], [{'op': []}], [{'op': 'delete', 'iri': 'urn:sample:x'}] * 41])
def test_malformed_or_oversized_edit_requests(ops):
    from ontology_assets.graph_edit_copy import render_copy
    from tests.unit.test_interactive_ontology_graph_projection import fixed
    with pytest.raises(ValueError):
        render_copy(graph(), fixed()[0], ops, 'view')


def test_annotations_are_not_editable_individual_data_values():
    g = graph()
    g.add((N.note, RDF.type, OWL.AnnotationProperty))
    g.add((N.x, N.note, Literal('说明')))
    with pytest.raises(ValueError):
        apply(g, op='remove_value', source=N.x, predicate=N.note, value='说明', language='', datatype='')
    assert (N.x, N.note, Literal('说明')) in g


def test_deleting_class_retains_undeclared_child_identity():
    g = graph()
    g.add((N.Child, RDFS.subClassOf, N.A))
    apply(g, op='delete', iri=N.A)
    assert (N.Child, RDF.type, OWL.Class) in g


def test_input_bounds_apply_to_source_before_replay(tmp_path):
    from ontology_assets.graph_edit_copy import render_copy
    from tests.unit.test_interactive_ontology_graph_projection import fixed
    g = Graph()
    for i in range(20001):
        g.add((N.x, N.v, Literal(i)))
    with pytest.raises(ValueError, match='两万'):
        render_copy(g, fixed()[0], [], 'view')

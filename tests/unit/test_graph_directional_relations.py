"""Directional schema and data assertions in editable RDF — DR-190."""
import pytest
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD

from ontology_assets.graph_edit_copy import apply_edit
from tests.unit.test_graph_relation_endpoints import sample
from domains.robotics.graph import S

N = Namespace('urn:example:')


def edit(graph, action, source, predicate, target, **kwargs):
    return apply_edit(graph, dict(op=action, source=str(source), predicate=str(predicate), target=str(target)), **kwargs)


def test_subclass_rejects_reverse_paths_and_projected_alias_cycles():
    graph = sample()
    graph.add((N.Place, RDFS.subClassOf, N.Cell))
    roles = dict(version='1', confirmed=True, bindings=[dict(kind='classes', iri=str(N.Cell), role='Workstation')])
    for child, parent in [(N.Place, N.Substation), (S.Workstation, N.Substation), (S.Workstation, N.Cell)]:
        with pytest.raises(ValueError, match='循环|同一'):
            edit(graph, 'link', child, RDFS.subClassOf, parent, role_map=roles)
        assert (child, RDFS.subClassOf, parent) not in graph
    edit(graph, 'link', N.Mobile, RDFS.subClassOf, N.Substation)
    graph.add((N.Cell, RDFS.subClassOf, N.Substation))
    edit(graph, 'unlink', N.Cell, RDFS.subClassOf, N.Substation)
    for predicate in (OWL.disjointWith, OWL.equivalentClass):
        for source, target in [(N.Mobile, S.Carrier), (S.Carrier, N.Mobile)]:
            assert edit(graph, 'link', source, predicate, target)['added'] == 1


def test_schema_relation_roles_and_datatype_range_are_checked_on_create_and_link():
    graph = sample()
    graph.add((N.value, RDF.type, OWL.DatatypeProperty))
    graph.add((N.code, RDF.type, RDFS.Datatype))
    for source, predicate, target in [(N.Cell, RDF.type, N.a), (N.Cell, RDFS.domain, S.hasCarrier),
                                      (N.value, RDFS.domain, XSD.string), (S.hasCarrier, RDFS.range, XSD.string),
                                      (N.value, RDFS.range, N.Cell), (N.value, RDFS.range, URIRef(str(XSD)+'madeUp'))]:
        with pytest.raises(ValueError):
            edit(graph, 'link', source, predicate, target)
    with pytest.raises(ValueError):
        apply_edit(graph, dict(op='create', kind='data', iri=str(N.bad), name='数据', range=str(XSD)+'madeUp'))
    for source, predicate, target in [(N.a, RDF.type, N.Mobile), (N.value, RDFS.domain, N.Cell),
                                      (N.value, RDFS.range, N.code), (S.hasCarrier, RDFS.range, N.Mobile)]:
        assert edit(graph, 'link', source, predicate, target)['added'] == 1


def test_data_values_check_instance_domain_and_literal_range_and_allow_legacy_removal():
    graph = sample()
    graph.add((N.measure, RDF.type, OWL.DatatypeProperty))
    graph.add((N.measure, RDFS.domain, S.Workstation))
    graph.add((N.measure, RDFS.range, XSD.integer))
    def value(source, datatype, lang='', op='add_value'):
        return apply_edit(graph, dict(op=op, source=str(source), predicate=str(N.measure), value='7',
                                      datatype=str(datatype), language=lang))
    for person, datatype, lang, reason in [(N.b, XSD.integer, '', '定义域'), (N.a, XSD.string, '', '值域'),
                                           (N.a, '', '', '值域'), (N.a, '', 'zh', '值域')]:
        before = set(graph)
        with pytest.raises(ValueError, match=reason):
            value(person, datatype, lang)
        assert set(graph) == before
    assert value(N.child, XSD.integer)['added'] == 1
    graph.add((N.b, N.measure, Literal('7', lang='zh')))
    value(N.b, '', 'zh', 'remove_value')
    graph.set((N.measure, RDFS.range, RDF.langString))
    assert value(N.a, '', 'zh')['added'] == 1
    graph.set((N.measure, RDFS.range, XSD.string))
    assert value(N.a, '')['added'] == 1
    graph.set((N.measure, RDFS.range, XSD.decimal))
    graph.add((N.measure, RDFS.range, XSD.integer))
    assert value(N.a, XSD.integer)['added'] == 1


def test_edit_http_replays_direction_checks_before_export(tmp_path):
    from tests.build_runtime_support import BASE_URL, http_app
    from tests.robotics_task_support import fixture
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        path = f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}/graph-edit-copy"
        def request(ops):
            return client.post(path, json=dict(base_sha256=version['payload_sha256'], format='owl', operations=ops),
                               base_url=BASE_URL)
        ops = [dict(op='create', name='类甲', kind='class', iri=str(N.A)),
               dict(op='create', name='类乙', kind='class', iri=str(N.B), parent=str(N.A)),
               dict(op='create', name='值', kind='data', iri=str(N.p), domain=str(N.B), range=str(XSD.integer)),
               dict(op='create', name='实例', kind='individual', iri=str(N.i), parent=str(N.A))]
        reverse = dict(op='link', source=str(N.A), predicate=str(RDFS.subClassOf), target=str(N.B))
        value = dict(op='add_value', source=str(N.i), predicate=str(N.p), datatype=str(XSD.integer),
                     language='', value='7')
        for invalid in (reverse, value):
            assert request(ops + [invalid]).status_code == 422
        membership = dict(op='link', source=str(N.i), predicate=str(RDF.type), target=str(N.B))
        good = request([*ops, membership, value])
        assert good.status_code == 200
        graph = Graph().parse(data=good.json['content'], format='xml')
        assert (N.i, N.p, Literal(7)) in graph


def test_edit_view_exposes_existing_datatypes_and_only_declared_class_choices():
    from ontology_assets.graph_edit_copy import render_copy
    graph = sample()
    graph.add((N.code, RDF.type, RDFS.Datatype))
    graph.add((N.value, RDF.type, OWL.DatatypeProperty))
    graph.add((N.value, RDFS.range, N.code))
    graph.add((N.value, RDFS.domain, N.undeclared))
    version = {'ontology_id': 'example', 'version_id': 'v', 'payload_sha256': 'a'*64}
    view = render_copy(graph, version, [], 'view')['view']
    assert str(N.code) in view['declared_datatypes']
    assert next(p for p in view['properties'] if p['id'] == str(N.value))['value_types'] == []
    assert next(n for n in view['nodes'] if n['id'] == str(N.undeclared))['declared_class'] is False
    with pytest.raises(ValueError):
        edit(graph, 'link', N.a, RDF.type, N.undeclared)

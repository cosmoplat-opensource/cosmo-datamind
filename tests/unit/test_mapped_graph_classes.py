"""Application class projection and original RDF identities — DR-189."""
import hashlib

from rdflib import Graph, OWL, RDF, RDFS, URIRef

from domains.robotics.graph import S
from ontology_assets.interactive_ontology_graph_view import project_version
from ontology_assets.graph_edit_copy import parse_source, render_copy

DESIGN = 'https://example.org/design#Cell'
OTHER = 'https://example.org/extension#Cell'
APP = str(S.Workstation)
NS = 'https://example.org/test#'


def sample():
    source = f'''@prefix owl: <{OWL}> . @prefix rdfs: <{RDFS}> . @prefix t: <{NS}> .
        <{DESIGN}> a owl:Class ; rdfs:label "工位" ; rdfs:subClassOf t:Place .
        <{APP}> a owl:Class ; rdfs:label "工位" ; rdfs:subClassOf t:Place .
        <{OTHER}> a owl:Class ; rdfs:label "工位" . t:Place a owl:Class .
        t:A a <{DESIGN}> . t:B a <{APP}> . t:C a <{APP}>, <{DESIGN}> .
        t:has a owl:ObjectProperty ; rdfs:domain <{DESIGN}> ; rdfs:range <{APP}> .
        t:kind a owl:ObjectProperty ; rdfs:range [ a owl:Class ; owl:unionOf (<{DESIGN}> <{OTHER}>) ] .'''
    raw = Graph().parse(data=source, format='turtle').serialize(format='xml', encoding='utf-8')
    version = dict(ontology_id='ontology', version_id='version', payload_kind='robotics_rdf',
                   payload_sha256=hashlib.sha256(raw).hexdigest())
    roles = dict(version='1', confirmed=True, bindings=[dict(kind='classes', role='Workstation', iri=DESIGN)])
    return raw, version, roles


def test_only_confirmed_class_pairs_share_nodes_members_and_edges():
    raw, version, roles = sample()
    for mapping in (None, {**roles, 'confirmed': False}, {**roles, 'bindings': [
            dict(kind='object_properties', role='Workstation', iri=DESIGN),
            dict(kind='classes', role='NoSuchClass', iri=DESIGN)]}):
        nodes = project_version(version, raw, role_map=mapping)['nodes']
        assert {APP, DESIGN, OTHER} <= {n['id'] for n in nodes}
    view = project_version(version, raw, role_map=roles)
    nodes = {n['id']: n for n in view['nodes']}
    assert DESIGN not in nodes and OTHER in nodes
    assert nodes[APP]['individuals'] == [NS + n for n in 'ABC']
    assert [n['id'] for n in nodes[APP]['design_classes']] == [DESIGN]
    assert all(i['types'] == [APP] for i in view['individuals'])
    prop = next(p for p in view['properties'] if p['id'] == NS+'has')
    assert prop['domains'] == prop['ranges'] == [APP]
    edges = [e for e in view['edges'] if e['kind'] == 'subclass']
    assert len(edges) == 1 and edges[0]['source'] == APP
    union = next(n['expression'] for n in nodes.values() if 'expression' in n)
    assert set(union['operands']) == {APP, OTHER}
    assert all(DESIGN not in (e['source'], e['target']) for e in view['edges'])


def test_edit_copy_retains_precise_assertions_and_exports_both_class_identities():
    raw, version, roles = sample()
    ops = [dict(op='create', kind='individual', name='新实例', iri=NS+'new', parent=APP)]
    def render(fmt):
        return render_copy(parse_source(version, raw), version, ops, fmt, role_map=roles)
    view = render('view')['view']
    node = next(n for n in view['nodes'] if n['id'] == APP)
    assert len(node['individuals']) == 4
    edge = next(e for e in view['edges'] if e['kind'] == 'subclass')
    assert {pair[0] for pair in edge['assertion_pairs']} == {APP, DESIGN}
    person = next(n for n in view['individuals'] if n['id'] == NS+'C')
    assert set(person['original_types']) == {APP, DESIGN}
    ops.append(dict(op='unlink', source=NS+'C', predicate=str(RDF.type), target=DESIGN))
    graph = Graph().parse(data=render('owl')['content'], format='xml')
    for iri in (APP, DESIGN, OTHER):
        assert (URIRef(iri), RDF.type, OWL.Class) in graph
    assert (URIRef(NS+'new'), RDF.type, URIRef(APP)) in graph
    assert (URIRef(NS+'C'), RDF.type, URIRef(DESIGN)) not in graph
    assert (URIRef(NS+'C'), RDF.type, URIRef(APP)) in graph



def test_overlapping_class_roles_keep_ambiguous_nodes_separate():
    raw, _, roles = sample()
    graph = Graph().parse(data=raw, format='xml')
    graph.add((S.Carrier, RDF.type, OWL.Class))
    roles['bindings'].append(dict(kind='classes', role='Carrier', iri=APP))
    version = dict(ontology_id='ontology', version_id='version', payload_kind='robotics_rdf')
    payload = graph.serialize(format='xml', encoding='utf-8')
    version['payload_sha256'] = hashlib.sha256(payload).hexdigest()
    view = project_version(version, payload, role_map=roles)
    assert {DESIGN, APP, str(S.Carrier)} <= {n['id'] for n in view['nodes']}
    assert any('映射' in warning for warning in view['warnings'])

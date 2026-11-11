"""RDF graph semantics, not serializer whitespace — DR-070 / T04 RED."""
from copy import deepcopy

import pytest
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
from rdflib.collection import Collection
from rdflib.compare import isomorphic

from tests.robotics_modeling_support import api, schema, bundle, confirmation, ur_schema, NS, no_network

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def graph(raw):
    assert isinstance(raw, bytes)
    return Graph().parse(data=raw, format='xml')


@pytest.mark.parametrize('endpoint', [RDFS.domain, RDFS.range])
def test_union_domains_survive_rdf_roundtrip(endpoint):
    value = schema()
    key = 'domain_ids' if endpoint == RDFS.domain else 'range_ids'
    value['object_properties'][0][key] = ['Atomic', 'Composite']
    before = deepcopy(value)
    exporter = api('export')
    first = graph(exporter.tbox_owl(value, NS))
    assert value == before
    ns = Namespace(NS)
    domains = list(first.objects(ns.hasInput, endpoint))
    assert len(domains) == 1
    head = first.value(domains[0], OWL.unionOf)
    assert head is not None
    assert set(Collection(first, head)) == {ns.Atomic, ns.Composite}
    assert isomorphic(first, graph(exporter.tbox_owl(value, NS)))
    assert (ns.Atomic, RDFS.subClassOf, ns.Skill) in first


def test_class_association_is_not_domain_or_instance_value():
    value = schema()
    result = graph(api('export').tbox_owl(value, NS))
    ns = Namespace(NS)
    assert (ns.stepIndex, RDF.type, OWL.DatatypeProperty) in result
    assert (ns.stepIndex, RDFS.range, XSD.integer) in result
    assert not list(result.objects(ns.stepIndex, RDFS.domain))
    assert not list(result.triples((None, ns.stepIndex, None)))
    assert not list(result.subjects(RDF.type, OWL.Restriction))
    assert not list(result.subjects(RDF.type, OWL.NamedIndividual))


def test_typed_assertions_and_provenance_match_snapshot():
    evidence, tbox = bundle(), ur_schema()
    result = api('abox').extract(confirmation(tbox, evidence), evidence)['result']
    output = graph(api('export').abox_owl(result, tbox, evidence['evidence']))
    ns, instances = Namespace(tbox['namespace']), Namespace(tbox['namespace'] + 'individual/')
    assert set(output.subjects(RDF.type, OWL.NamedIndividual)) == {instances[r['id']] for r in result['individuals']}
    refs = {r['id'] for r in evidence['evidence']}
    for row in result['individuals']:
        assert set(row['source_refs']) <= refs
        for name in row['class_ids']:
            assert (instances[row['id']], RDF.type, ns[name]) in output
    for row in result['object_assertions']:
        assert (instances[row['subject']], ns[row['property']], instances[row['object']]) in output
    datatypes = {r['id']: XSD[r['datatype'].removeprefix('xsd:')] for r in tbox['data_properties']}
    for row in result['data_assertions']:
        assert set(row['source_refs']) <= refs
        assert (instances[row['subject']], ns[row['property']], Literal(row['value'], datatype=datatypes[row['property']])) in output
    for group, kind in [('classes', OWL.Class), ('object_properties', OWL.ObjectProperty),
                         ('data_properties', OWL.DatatypeProperty)]:
        assert {s for s in output.subjects(RDF.type, kind) if isinstance(s, URIRef)} == {ns[r['id']] for r in tbox[group]}
    assert not list(output.triples((None, OWL.imports, None)))
    assert not any(str(p).endswith('artifactContract') for _, p, _ in output)


def test_design_export_does_not_claim_legacy_application_compatibility():
    evidence, tbox = bundle(), ur_schema()
    result = api('abox').extract(confirmation(tbox, evidence), evidence)['result']
    raw = api('export').abox_owl(result, tbox, evidence['evidence'])
    assert len(graph(raw)) > 0
    from domains.robotics.consumer import load_ontology_bytes
    with pytest.raises(ValueError):
        load_ontology_bytes(raw, mode='real')


def test_export_cannot_self_authorize_unknown_provenance():
    evidence, tbox = bundle(), ur_schema()
    result = api('abox').extract(confirmation(tbox, evidence), evidence)['result']
    result['individuals'][0]['source_refs'] = ['invented_evidence']
    with pytest.raises(ValueError):
        api('export').abox_owl(result, tbox, evidence['evidence'])


@pytest.mark.parametrize('namespace', ['relative#', 'javascript:alert(1)', 'https://user:secret@example.org/#'])
def test_export_rejects_invalid_namespace(namespace):
    exporter = api('export')
    with pytest.raises(ValueError):
        exporter.tbox_owl(schema(), namespace)

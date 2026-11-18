"""Frozen projection behavior oracles — DR-159 / IR-031 T02 RED."""
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json

import pytest
from rdflib import Graph

from domains.robotics.modeling.common import digest
from tests.robotics_design_support import Harness
from tests.build_pipeline_support import blob_bytes, db_rows

NS = 'https://studio.test/robot#'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
RDFS = 'http://www.w3.org/2000/01/rdf-schema#'


def api():
    # Absence of this new product capability is an assertion, not an import/collection error.
    assert importlib.util.find_spec('ontology_assets.studio_view') is not None, (
        'DR-159 requires a read-only Studio projection for saved designs and RDF versions')
    return importlib.import_module('ontology_assets.studio_view')


def session(stage='classes', revision=1):
    classes = [{'id': key, 'label': key, 'definition': key + ' definition',
                'parent_ids': ['Resource'] if key == 'Arm' else []}
               for key in ('Resource', 'Arm', 'Gripper', 'Empty')]
    result = {'reply': 'saved proposal', 'classes': classes, 'competency_questions': [], 'clarifications': []}
    confirmation = None
    schema = None
    if stage != 'classes':
        confirmation = {'classes': deepcopy(classes), 'competency_questions': [], 'clarifications': []}
        result.update(object_properties=[{'id': 'uses', 'label': 'uses', 'definition': 'uses resource',
            'domain_ids': ['Arm', 'Gripper'], 'range_ids': ['Resource']}],
            data_properties=[{'id': 'code', 'label': 'code', 'definition': 'code text',
            'domain_ids': ['Arm'], 'datatype': 'xsd:string'}])
    if stage == 'abox':
        tbox = {'namespace': NS, **{k: deepcopy(result[k]) for k in
                ('classes', 'object_properties', 'data_properties')}}
        schema = {'tbox': tbox, 'sha256': digest(tbox)}
    return {'session_id': 'design_A', 'namespace': NS, 'revision': revision, 'stage': stage,
            'status': 'awaiting_confirmation', 'result': result, 'snapshot_sha256': digest(result),
            'class_confirmation': confirmation, 'schema_confirmation': schema, 'output_asset_ref': None}


TTL = '''@prefix s: <https://studio.test/robot#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
s:Resource a owl:Class .
s:Arm a owl:Class ; rdfs:subClassOf s:Resource .
s:Gripper a owl:Class . s:Empty a owl:Class .
s:uses a owl:ObjectProperty ; rdfs:domain [ a owl:Class ; owl:unionOf (s:Arm s:Gripper) ] ; rdfs:range s:Resource .
s:separate a owl:ObjectProperty ; rdfs:domain s:Arm, s:Gripper ; rdfs:range s:Resource .
s:code a owl:DatatypeProperty ; rdfs:domain s:Arm ; rdfs:range xsd:string .
s:n a owl:DatatypeProperty ; rdfs:domain s:Arm ; rdfs:range xsd:integer .
s:a a owl:NamedIndividual, s:Arm, s:Gripper ; s:code "" ; s:n 0 ; s:uses s:b .
s:b a s:Arm .
'''


def fixed(raw=None):
    raw = raw if raw is not None else Graph().parse(data=TTL, format='turtle').serialize(format='xml', encoding='utf-8')
    return {'ontology_id': 'ont_A', 'version_id': 'v_A', 'payload_kind': 'robotics_rdf',
            'payload_sha256': hashlib.sha256(raw).hexdigest(), 'publication_state': 'candidate'}, raw


def test_design_stages_project_only_valid_revision_content():
    projector = api()
    for stage in ('classes', 'properties', 'abox'):
        row = session(stage)
        before = deepcopy(row)
        view = projector.project_design(row)
        assert view['schema'] == 'datamind-studio/1'
        assert view['context']['key'] == 'design_A' and view['context']['revision'] == 1
        assert view['context']['stage'] == stage
        assert view['metadata']['read_only'] is True
        assert {n['id'] for n in view['nodes'] if n['type'] == 'class'} == {NS + k for k in ('Resource', 'Arm', 'Gripper', 'Empty')}
        assert bool(view['properties']) is (stage != 'classes')
        assert view['individuals'] == [] and view['relations'] == []
        assert row == before


def test_classes_stage_never_renders_stale_properties_or_vocabulary_instances():
    row = session()
    row['result']['object_properties'] = session('properties')['result']['object_properties']
    row['snapshot_sha256'] = digest(row['result'])
    view = api().project_design(row)
    assert view['properties'] == [] and view['individuals'] == []
    assert all(e['kind'] == 'subclass' for e in view['edges'])


@pytest.mark.parametrize('status', ['queued', 'running', 'failed', 'interrupted'])
def test_waiting_and_failure_keep_saved_design_without_simulated_progress(status):
    row = session(); row['status'] = status
    view = api().project_design(row)
    assert view['context']['status'] == status
    assert len([n for n in view['nodes'] if n['type'] == 'class']) == 4
    assert len(view['edges']) == 1


def test_no_saved_result_is_empty_not_a_vocabulary_seed():
    row = session(); row.update(result=None, snapshot_sha256=None, revision=0)
    view = api().project_design(row)
    assert view['nodes'] == [] and view['edges'] == [] and view['individuals'] == []


@pytest.mark.parametrize('damage', ['digest', 'namespace', 'confirmation', 'schema'])
def test_invalid_design_snapshot_is_rejected(damage):
    row = session('abox' if damage == 'schema' else 'properties')
    if damage == 'digest': row['snapshot_sha256'] = '0' * 64
    elif damage == 'namespace': row['namespace'] = 'relative'
    elif damage == 'confirmation': row['class_confirmation']['classes'] = []
    else: row['schema_confirmation']['sha256'] = '0' * 64
    with pytest.raises(ValueError): api().project_design(row)


def test_explicit_membership_counts_deduplicate_without_inference():
    version, raw = fixed()
    view = api().project_version(version, raw)
    classes = {n['id']: n for n in view['nodes'] if n['type'] == 'class'}
    assert classes[NS + 'Arm']['individuals'] == [NS + 'a', NS + 'b']
    assert classes[NS + 'Gripper']['individuals'] == [NS + 'a']
    assert classes[NS + 'Resource']['individuals'] == []
    assert classes[NS + 'Empty']['individuals'] == []
    assert len(view['individuals']) == 2
    assert next(n for n in view['individuals'] if n['id'] == NS + 'a')['types'] == [NS + 'Arm', NS + 'Gripper']


def test_fixed_legacy_and_design_payloads_preserve_rdf_semantics():
    version, raw = fixed()
    view = api().project_version(version, raw)
    props = {p['id']: p for p in view['properties']}
    expressions = {n['id']: n for n in view['nodes'] if n['type'] == 'expression'}
    union = expressions[props[NS + 'uses']['domains'][0]]['expression']
    assert union['operator'] == 'unionOf' and set(union['operands']) == {NS + 'Arm', NS + 'Gripper'}
    assert set(props[NS + 'separate']['domains']) == {NS + 'Arm', NS + 'Gripper'}
    item = next(i for i in view['individuals'] if i['id'] == NS + 'a')
    assert any(d['predicate'] == NS + 'code' and d['value'] == '' for d in item['data'])
    assert any(d['predicate'] == NS + 'n' and str(d['value']) == '0' and d['datatype'].endswith('integer') for d in item['data'])
    assert any(e['source'] == NS + 'a' and e['target'] == NS + 'b' and e['predicate'] == NS + 'uses' for e in view['relations'])
    assert view['context']['key'] == json.dumps(['ont_A', 'v_A'], separators=(',', ':'))
    assert view['context']['revision'] == version['payload_sha256']


def test_blank_expression_and_edge_identity_are_stable_after_rdf_reparse():
    version, raw = fixed()
    projector = api()
    first = projector.project_version(version, raw)
    second = projector.project_version(version, raw)
    assert first == second
    graph = Graph().parse(data=raw, format='xml')
    reverse = Graph()
    for triple in reversed(list(graph)): reverse.add(triple)
    v2, raw2 = fixed(reverse.serialize(format='xml', encoding='utf-8'))
    other = projector.project_version(v2, raw2)
    assert first['nodes'] == other['nodes'] and first['edges'] == other['edges']
    assert len({e['id'] for e in first['edges']}) == len(first['edges'])


@pytest.mark.parametrize('damage', ['hash', 'kind', 'malformed', 'doctype'])
def test_fixed_version_integrity_is_checked_before_display(damage):
    version, raw = fixed()
    if damage == 'hash': version['payload_sha256'] = '0' * 64
    elif damage == 'kind': version['payload_kind'] = 'data_ir'
    elif damage == 'malformed': version, raw = fixed(b'<broken>')
    else: version, raw = fixed(b'<!DOCTYPE rdf [<!ENTITY test "bad">]><rdf/>')
    with pytest.raises(ValueError): api().project_version(version, raw)


def test_real_saved_design_and_fixed_candidate_projection_are_read_only(tmp_path):
    projector = api()
    harness = Harness(tmp_path / 'assets')
    state = harness.candidate()
    from ontology_assets import AssetStore
    assets = AssetStore(harness.root)
    version = assets.get_version(**state['output_asset_ref'])
    raw = assets.read_payload(**state['output_asset_ref'])
    before = db_rows(harness.root), blob_bytes(harness.root), len(harness.runtime.calls)
    design = projector.project_design(state)
    view = projector.project_version(version, raw)
    assert design['context']['stage'] == 'abox' and design['individuals'] == []
    assert view['individuals'] and view['context']['revision'] == version['payload_sha256']
    assert before == (db_rows(harness.root), blob_bytes(harness.root), len(harness.runtime.calls))


def test_reference_rdf_fixture_has_independent_explicit_membership_oracle():
    from rdflib import OWL, RDF, URIRef
    graph = Graph().parse(data=TTL, format='turtle')
    assert set(graph.subjects(RDF.type, URIRef(NS + 'Arm'))) == {URIRef(NS + 'a'), URIRef(NS + 'b')}
    assert set(graph.subjects(RDF.type, URIRef(NS + 'Resource'))) == set()
    assert len(list(graph.subjects(OWL.unionOf, None))) == 1
    assert len(set(graph.objects(URIRef(NS + 'separate'), URIRef(RDFS + 'domain')))) == 2


def test_reference_design_fixture_digests_and_confirmations_are_consistent():
    for stage in ('classes', 'properties', 'abox'):
        row = session(stage)
        assert row['snapshot_sha256'] == digest(row['result'])
        if stage != 'classes': assert row['class_confirmation']['classes'] == row['result']['classes']
        if stage == 'abox':
            assert row['schema_confirmation']['sha256'] == digest(row['schema_confirmation']['tbox'])


def test_legacy_robotics_payload_preserves_declared_classes_and_instance_identity():
    from tests.unit.test_robotics_task_completion import ready_graph
    from domains.robotics.graph import S
    version, raw = fixed(ready_graph().serialize(format='xml', encoding='utf-8'))
    result = api().project_version(version, raw)
    classes = {n['id'] for n in result['nodes'] if n['type'] == 'class'}
    instances = {n['id'] for n in result['individuals']}
    assert str(S.PartType) in classes and str(S.Part_A_001) in instances
    assert not classes.intersection(instances)


def test_equal_local_names_keep_full_iri_identity():
    raw = Graph().parse(data=TTL + '\n<https://other.test/#Arm> a owl:Class .\n'
                        '<https://other.test/#a> a <https://other.test/#Arm> .', format='turtle').serialize(
                            format='xml', encoding='utf-8')
    version, raw = fixed(raw)
    view = api().project_version(version, raw)
    nodes = {n['id']: n for n in view['nodes']}
    assert nodes[NS + 'Arm']['individuals'] == [NS + 'a', NS + 'b']
    assert nodes['https://other.test/#Arm']['individuals'] == ['https://other.test/#a']


def test_empty_rdf_is_valid_empty_view_without_dummy_nodes():
    version, raw = fixed(Graph().serialize(format='xml', encoding='utf-8'))
    result = api().project_version(version, raw)
    assert result['nodes'] == [] and result['individuals'] == [] and result['edges'] == []


def test_oversized_projection_refuses_instead_of_silently_truncating():
    projector = api()
    from rdflib import OWL, RDF, URIRef
    graph = Graph()
    for index in range(20001): graph.add((URIRef(NS + str(index)), RDF.type, OWL.Class))
    version, raw = fixed(graph.serialize(format='xml', encoding='utf-8'))
    with pytest.raises(ValueError, match='规模|limit|large'):
        projector.project_version(version, raw)


def test_cartesian_property_expansion_is_rejected_before_allocating_edges(monkeypatch):
    from rdflib import OWL, RDF, RDFS, URIRef
    projector = api()
    graph = Graph()
    prop = URIRef(NS + 'wide')
    graph.add((prop, RDF.type, OWL.ObjectProperty))
    for i in range(320):
        graph.add((prop, RDFS.domain, URIRef(NS + 'd' + str(i))))
        graph.add((prop, RDFS.range, URIRef(NS + 'r' + str(i))))
    calls = []
    original = projector._edge

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(projector, '_edge', counted)
    version, raw = fixed(graph.serialize(format='xml', encoding='utf-8'))
    with pytest.raises(ValueError, match='规模|上限'):
        projector.project_version(version, raw)
    assert calls == [], 'reject the Cartesian product before constructing its edges'

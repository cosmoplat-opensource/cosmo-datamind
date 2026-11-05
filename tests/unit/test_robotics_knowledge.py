"""T02 RED: RDF authority, typed business edits and protected structure — DR-059 / DR-060 / DR-061."""
from copy import deepcopy

import pytest
from rdflib import BNode, Literal, RDF, RDFS, URIRef, XSD
from rdflib.collection import Collection
from rdflib.compare import isomorphic

from tests.robotics_governance_support import field, graph, module, relation
from tests.robotics_support import S


def test_edit_preserves_unknown_semantics_literals_lists_and_original_graph():
    api = module('domains.robotics.authoring')
    original = graph()
    blank = BNode('opaque')
    original.add((S.Foreign, S.unrecognized, blank))
    original.add((blank, S.measure, Literal('1.200', datatype=XSD.decimal, normalize=False)))
    original.add((blank, RDFS.label, Literal('unknown annotation', lang='en')))
    Collection(original, BNode('list'), [S.Foreign, Literal('列表', lang='zh')])
    before = set(original)
    changed = api.apply_operations(original, [field()])
    assert set(original) == before and changed is not original
    removed = before - set(changed)
    assert removed == set(original.triples((S.PartType_A, RDFS.label, None)))
    assert (S.PartType_A, RDFS.label, Literal('人工零件类型', lang='zh')) in changed
    assert set(original.triples((blank, None, None))) <= set(changed)
    assert list(changed.items(BNode('list'))) == [S.Foreign, Literal('列表', lang='zh')]


def test_same_display_name_does_not_merge_full_iris_and_projection_keeps_identity():
    api = module('domains.robotics.authoring')
    original = graph()
    identities = ['urn:factory:a#part', 'urn:factory:b#part']
    operations = [{'op': 'create_entity', 'iri': iri, 'entity_type': 'PartType', 'label': '同名零件',
                   'basis': '测试目录声明'} for iri in identities]
    changed = api.apply_operations(original, operations)
    for iri in identities:
        assert (URIRef(iri), RDF.type, S.PartType) in changed
    projection = api.inspect_knowledge(changed)
    assert {e['iri'] for e in projection['entities']} >= set(identities)
    assert any(i['code'] == 'ambiguous_label' for i in projection['issues'])


@pytest.mark.parametrize('iri,relation_name,target', [
    (S.Part_A_001, 'hasPartType', S.PartType_A),
    (S.Station_B, 'hasCarrier', S.Carrier_B_01),
    (S.Carrier_B_01, 'hasSlot', S.Slot_B_01),
    (S.PartType_A, 'hasPerceptionProfile', S.Perception_Current),
    (S.Slot_B_01, 'hasPlacementProfile', S.Placement_Current),
])
def test_business_mapping_is_real_rdf_and_never_confirmation(iri, relation_name, target):
    api = module('domains.robotics.authoring')
    original = graph()
    status = set(original.triples((None, S.confirmationStatus, None)))
    changed = api.apply_operations(original, [relation(iri, relation_name, [target])])
    assert (iri, S[relation_name], target) in changed
    assert set(changed.triples((None, S.confirmationStatus, None))) == status
    assert not list(changed.subjects(RDF.type, S.ReadinessEvidence))
    assert api.inspect_knowledge(changed)['execution_readiness'] == 'not_verified'


def test_parameter_updates_feed_existing_grounding_and_retain_basis():
    api = module('domains.robotics.authoring')
    changed = api.apply_operations(graph(), [field(S.Perception_Current, 'threshold', '0.72'),
                                            field(S.Perception_Current, 'modelName', 'part_v2')])
    values, sources = module('domains.robotics.grounding').profile_parameters(changed, S.Perception_Current, 'Perception')
    assert values['threshold'] == '0.72' and values['model_name'] == 'part_v2'
    assert sources and not list(changed.triples((None, S.profileValues, None)))
    report = api.inspect_knowledge(changed)
    assert any(p['iri'] == str(S.Perception_Current) and p['field'] == 'threshold'
               and p['basis'] == field()['basis'] for p in report['provenance'])


def test_unknown_values_units_and_frames_are_saved_as_gaps_without_defaults():
    api = module('domains.robotics.authoring')
    changed = api.apply_operations(graph(), [field(S.PlacePose_Current, 'rawValue', None),
                                            field(S.PlacePose_Current, 'lengthUnit', None),
                                            field(S.PlacePose_Current, 'angleUnit', None),
                                            relation(S.PlacePose_Current, 'expressedInFrame', [])])
    assert not list(changed.objects(S.PlacePose_Current, S.rawValue))
    report = api.inspect_knowledge(changed)
    gaps = {i['field'] for i in report['issues'] if i.get('iri') == str(S.PlacePose_Current)}
    assert {'rawValue', 'lengthUnit', 'angleUnit', 'expressedInFrame'} <= gaps
    assert not list(changed.objects(S.PlacePose_Current, S.lengthUnit))
    assert not list(changed.objects(S.PlacePose_Current, S.expressedInFrame))


@pytest.mark.parametrize('operation', [
    field(S.Perception_Current, 'threshold', 'NaN'),
    field(S.Perception_Current, 'threshold', '1.1'),
    field(S.Perception_Current, 'exposureTime', '-1'),
    field(S.CaptureJoints_Current, 'rawValue', '[1,2]'),
    field(S.PlacePose_Current, 'rawValue', 'not a pose'),
    relation(S.Part_A_001, 'hasPartType', [S.Carrier_B_01]),
    relation(S.PartType_A, 'hasPerceptionProfile', [S.DoesNotExist]),
    relation(S.Part_A_001, 'hasPartType', [S.PartType_A, S.PartType_A]),
    {'op': 'create_entity', 'iri': str(S.PartType_A), 'entity_type': 'PartType', 'label': 'duplicate', 'basis': 'test'},
    {'op': 'create_entity', 'iri': 'local_name_only', 'entity_type': 'PartType', 'label': 'bad ID', 'basis': 'test'},
    {'op': 'delete_entity', 'iri': str(S.Carrier_B_01), 'basis': 'referenced'},
    {**field(), 'basis': ''},
])
def test_invalid_values_references_and_unexplained_edits_are_atomic(operation):
    api = module('domains.robotics.authoring')
    original = graph()
    before = deepcopy(original)
    with pytest.raises(ValueError):
        api.apply_operations(original, [field(), operation])
    assert isomorphic(original, before)


@pytest.mark.parametrize('operation', [
    field(S.PartType_A, 'confirmationStatus', 'confirmed'),
    field(S.BehaviorTree_Source, 'sha256', '0' * 64),
    field(S.NodeModels_Source, 'sha256', '0' * 64),
    field(URIRef('https://example.org/ur-skill'), 'datasetKind', 'demo'),
    field(URIRef('https://example.org/ur-skill'), 'evidencePolicy', None),
    field(S.main_Sequence_1, 'stepIndex', '9'),
    field(S.grasp_Subtree_Impl, 'label', 'editable implementation?'),
    field(S.Perception_Current, 'profileValues', '{}'),
    {'op': 'delete_entity', 'iri': str(S.Grasp_Skill), 'basis': 'test'},
    {'op': 'replace_graph', 'rdf': '<rdf/>', 'basis': 'test'},
    {'op': 'create_entity', 'iri': str(S.NewSkill), 'entity_type': 'AtomicSkill', 'label': 'unsafe', 'basis': 'test'},
])
def test_protected_structure_protocol_and_confirmation_are_not_editable(operation):
    api = module('domains.robotics.authoring')
    original = graph()
    before = set(original)
    with pytest.raises(ValueError):
        api.apply_operations(original, [operation])
    assert set(original) == before


def test_explicit_detach_then_delete_preserves_other_entities():
    api = module('domains.robotics.authoring')
    original = graph()
    # Use a fresh, permitted business object so no protected Task relation must be edited.
    changed = api.apply_operations(original, [{'op': 'create_entity', 'iri': str(S.SpareSlot),
        'entity_type': 'PlacementSlot', 'label': '备用槽位', 'basis': 'test'},
        relation(S.Carrier_B_01, 'hasSlot', [S.Slot_B_01, S.SpareSlot])])
    deleted = api.apply_operations(changed, [relation(S.Carrier_B_01, 'hasSlot', [S.Slot_B_01]),
        {'op': 'delete_entity', 'iri': str(S.SpareSlot), 'basis': 'explicit detach'}])
    assert not list(deleted.triples((S.SpareSlot, None, None)))
    assert not list(deleted.triples((None, None, S.SpareSlot)))
    assert (S.Carrier_B_01, S.hasSlot, S.Slot_B_01) in deleted

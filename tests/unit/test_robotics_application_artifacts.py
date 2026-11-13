"""T02 RED: identity preservation, real RDF consumers and complete v2 bundle — DR-072."""
from copy import deepcopy
import hashlib
import json

import pytest
from rdflib import Literal, Namespace, OWL, RDF, RDFS, URIRef
from rdflib.compare import isomorphic

from domains.robotics import compiler, semantics, task_knowledge
from domains.robotics.consumer import load_ontology_bytes
from tests.robotics_modeling_support import no_network
from tests.robotics_support import S, source_root, trees, config
from tests.robotics_adaptation_support import (
    api, sample, adapt, exported, graph, json_document, blocked, old_test_hashes, role_map,
)

__all__ = ['no_network', 'sample']
pytestmark = pytest.mark.usefixtures('no_network')


def test_adaptation_preserves_inputs_and_retains_real_execution_semantics(sample):
    before, sources = deepcopy(sample), old_test_hashes()
    output = adapt(sample)
    assert output['status'] == 'adapted' and not output['issues']
    g = graph(output['ontology'])
    assert trees(semantics.xml_from_graph(g)) == trees(source_root())
    assert task_knowledge.inspect_structure(g, 'real') is g
    assert len(set(g.subjects(RDF.type, S.SkillInvocation))) == 25
    assert len(set(g.subjects(RDF.type, S.ParameterBinding))) == 129
    assert sample == before and old_test_hashes() == sources
    assert output['execution_readiness'] == 'not_verified'


def test_no_old_illustrative_business_or_device_instances_are_invented(sample):
    g = graph(adapt(sample)['ontology'])
    for name in ('Workpiece', 'Workstation', 'Carrier', 'PlacementSlot', 'Task', 'RobotArm', 'Gripper', 'Camera',
                 'Observation', 'ExecutionRecord', 'ReadinessEvidence'):
        assert not list(g.subjects(RDF.type, S[name])), name
    for name in ('Part_A_001', 'Station_B', 'Carrier_B_01', 'Slot_B_01', 'Example_Task_A_to_B', 'UR_LeftArm'):
        assert not list(g.triples((S[name], None, None)))
    assert not list(g.triples((None, OWL.sameAs, None)))
    assert not list(g.triples((None, OWL.imports, None)))


def test_original_individuals_have_explicit_semantic_identity_mapping(sample):
    output = adapt(sample); g = graph(output['ontology']); mapping = output['identity_map']
    ns = sample['tbox']['namespace'] + 'individual/'
    rows = [r for r in mapping if r['role'] == 'SkillInvocation']
    calls = [r for r in sample['abox']['individuals'] if 'SkillInvocation' in r['class_ids']]
    assert {r['source_iri'] for r in rows} == {ns + r['id'] for r in calls}
    assert len({r['target_iri'] for r in rows}) == 25
    assert all((URIRef(r['target_iri']), RDF.type, S.SkillInvocation) in g for r in rows)
    assert all(r['source_refs'] for r in rows)


def test_user_extension_knowledge_is_not_discarded_or_turned_into_an_execution_role(sample):
    extension = Namespace('urn:synthetic:extension:')
    original = graph(sample['ontology'])
    original.add((extension.operator_note, RDFS.label, Literal('合成扩展知识，仅用于软件测试')))
    raw = original.serialize(format='xml', encoding='utf-8')
    sample['ontology'] = raw; sample['origin']['payload_sha256'] = hashlib.sha256(raw).hexdigest()
    output = adapt(sample); g = graph(output['ontology'])
    assert (extension.operator_note, RDFS.label, Literal('合成扩展知识，仅用于软件测试')) in g
    assert not any(r['source_iri'] == str(extension.operator_note) for r in output['identity_map'])


def test_public_configuration_compiles_through_existing_validator_only():
    output = adapt(); g = graph(output['ontology']); baseline = semantics.xml_from_graph(g)
    paths = semantics.configuration_paths(g)
    selected = config(); selected['Perception']['threshold'] = '0.91'
    candidate = compiler.instantiate(baseline, selected, models_root=semantics.node_models_from_graph(g),
                                    config_paths=paths, baseline=baseline)
    for skill, fields in paths.items():
        for name, (tree_id, index, port) in fields.items():
            node = candidate.find("BehaviorTree[@ID='" + tree_id + "']/Sequence")[index]
            assert node.get(port) == selected[skill][name]
    assert trees(candidate) != trees(baseline)
    assert trees(semantics.xml_from_graph(g)) == trees(baseline)
    with pytest.raises(ValueError):
        compiler.instantiate(baseline, {**selected, 'grasp': {'obj_in_robot': 'invented'}},
            models_root=semantics.node_models_from_graph(g), config_paths=paths, baseline=baseline)


def test_internal_configuration_and_fixed_flow_match_source_except_public_values():
    g = graph(adapt()['ontology']); baseline = semantics.xml_from_graph(g)
    selected = config(); selected['Perception']['object_name'] = 'test-object'
    candidate = compiler.instantiate(baseline, selected, models_root=semantics.node_models_from_graph(g),
                                    config_paths=semantics.configuration_paths(g), baseline=baseline)
    public = {(tree, index, port) for fields in semantics.configuration_paths(g).values()
              for tree, index, port in fields.values()}
    for left_tree, right_tree in zip(baseline.findall('BehaviorTree'), candidate.findall('BehaviorTree')):
        assert left_tree.attrib == right_tree.attrib
        for index, (left, right) in enumerate(zip(left_tree.find('Sequence'), right_tree.find('Sequence'))):
            assert left.tag == right.tag and left.attrib.keys() == right.attrib.keys()
            assert all(left.get(key) == right.get(key) for key in left.attrib
                       if (left_tree.get('ID'), index, key) not in public)


@pytest.mark.parametrize('case', ['origin_hash', 'confirmation', 'role_snapshot', 'source_evidence'])
def test_corrupt_design_basis_is_blocked_without_producing_partial_artifacts(sample, case):
    mapping = role_map(sample['tbox'])
    if case == 'origin_hash': sample['origin']['payload_sha256'] = '0' * 64
    elif case == 'confirmation': sample['confirmation']['sha256'] = '0' * 64
    elif case == 'role_snapshot': mapping['tbox_sha256'] = '0' * 64
    else: sample['evidence']['documents'][0]['sha256'] = '0' * 64
    before = deepcopy(sample)
    output = api().adapt(design=sample, role_map=mapping, mode='real')
    assert output['status'] == 'blocked' and output['issues']
    assert output.get('ontology') is None and output.get('document') is None
    assert sample == before


def test_missing_class_roles_produce_schema_repair_not_a_fake_executable_graph(sample):
    mapping = role_map(sample['tbox'])
    mapping['bindings'] = [r for r in mapping['bindings'] if r['role'] != 'ParameterBinding']
    output = api().adapt(design=sample, role_map=mapping, mode='real')
    blocked(output, 'schema.missing_role')
    assert output.get('ontology') is None


def test_total_json_preserves_current_graph_original_design_and_version_lineage(sample):
    product = adapt(sample); before = deepcopy(product)
    files = exported(product); document = json_document(files)
    assert document['contract'] == {'id': 'robotics-application-design', 'version': 2}
    assert document['output_asset_ref'] == {'ontology_id': 'ont-synthetic', 'version_id': 'ver-app-2'}
    assert document['parent_version_id'] == 'ver-design-1'
    assert document['payload_sha256'] == hashlib.sha256(files['owl']).hexdigest()
    assert document['current']['tbox'] and document['current']['abox']
    assert document['origin']['asset_ref'] == {k: sample['origin'][k] for k in ('ontology_id', 'version_id')}
    assert document['origin']['design_snapshot']['tbox'] == sample['tbox']
    assert document['origin']['design_snapshot']['abox'] == sample['abox']
    assert document['identity_map'] == product['identity_map']
    assert document['role_map']['tbox_sha256'] == role_map(sample['tbox'])['tbox_sha256']
    assert document['coverage'] and document['validation'] and document['components']['adapter_version']
    assert document['execution_readiness'] == 'not_verified'
    assert product == before


def test_total_json_alone_restores_rdf_semantics_without_file_or_network_access(tmp_path, monkeypatch):
    files = exported(adapt())
    # Parsing the total JSON must not read any sidecar/archived XML. RDF serialization isn't a filesystem read.
    import pathlib
    trusted = pathlib.Path(__file__).resolve().parents[2] / 'domains/robotics/resources'
    read_bytes, read_text = pathlib.Path.read_bytes, pathlib.Path.read_text
    def guarded_bytes(path, *args, **kwargs):
        assert path.resolve().is_relative_to(trusted), 'Total JSON restore accessed a filesystem sidecar'
        return read_bytes(path, *args, **kwargs)
    def guarded_text(path, *args, **kwargs):
        assert path.resolve().is_relative_to(trusted), 'Total JSON restore accessed a filesystem sidecar'
        return read_text(path, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, 'read_bytes', guarded_bytes)
    monkeypatch.setattr(pathlib.Path, 'read_text', guarded_text)
    restored = api().restore_bundle(files['json'])
    assert isomorphic(graph(restored), graph(files['owl']))


@pytest.mark.parametrize('case', ['unknown_version', 'payload_hash', 'missing_current', 'changed_current'])
def test_unknown_or_inconsistent_total_json_cannot_restore_as_valid(case):
    files = exported(adapt()); document = json_document(files)
    if case == 'unknown_version': document['contract']['version'] = 999
    elif case == 'payload_hash': document['payload_sha256'] = '0' * 64
    elif case == 'missing_current': document.pop('current')
    else: document['current']['abox'] = {'individuals': [], 'object_assertions': [], 'data_assertions': []}
    with pytest.raises(ValueError) as caught:
        api().restore_bundle(json.dumps(document, ensure_ascii=False).encode())
    assert getattr(caught.value, 'code', None) == 'application_artifact_invalid'


def test_pure_adapt_and_export_do_not_publish_or_overwrite_original_v1_bytes(sample, tmp_path):
    original = tmp_path / 'design-v1.owl'; original.write_bytes(sample['ontology'])
    before = original.read_bytes(), deepcopy(sample)
    files = exported(adapt(sample))
    assert set(tmp_path.iterdir()) == {original}
    assert original.read_bytes() == before[0] and sample == before[1]
    assert not any(str(tmp_path) in content.decode('utf-8') for content in files.values())


def test_original_valid_generic_design_is_not_accepted_by_legacy_consumer(sample):
    # Real existing baseline runs during RED; no missing target API needed here.
    with pytest.raises(ValueError):
        load_ontology_bytes(sample['ontology'], mode='real')
    assert graph(sample['ontology'])


def test_adaptation_does_not_fall_back_to_legacy_builder_or_example_population(sample, monkeypatch):
    from domains.robotics import importer
    from domains.robotics.profiles import ur_pick_place
    def forbidden(*args, **kwargs):
        pytest.fail('Application adaptation fell back to the old builder or illustrative business population')
    monkeypatch.setattr(importer, 'build_graph', forbidden)
    monkeypatch.setattr(ur_pick_place, 'add_domain', forbidden)
    assert adapt(sample)['status'] == 'adapted'


def test_existing_legacy_compiler_is_an_independent_six_parameter_oracle():
    from tests.robotics_support import build
    g = build(); baseline = semantics.xml_from_graph(g)
    paths = semantics.configuration_paths(g)
    assert sum(len(v) for v in paths.values()) == 6
    result = compiler.instantiate(baseline, config(), models_root=semantics.node_models_from_graph(g),
                                 config_paths=paths, baseline=baseline)
    assert trees(result) == trees(source_root())

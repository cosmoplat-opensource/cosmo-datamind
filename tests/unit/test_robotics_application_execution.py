"""T02 RED: source-bound execution semantics, scoped values and public-only editing — DR-072."""
from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from tests.robotics_modeling_support import no_network
from tests.robotics_support import SOURCE, config
from tests.robotics_adaptation_support import (
    api, extract, extraction_input, calls, blocked, issue_sources, PUBLIC,
)

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def main(tree):
    return tree.find("BehaviorTree[@ID='main']/Sequence")


def test_execution_retains_document_order_calls_names_and_subtree_targets():
    data = extraction_input(); before = deepcopy(data)
    output = api().extract_execution(**data)
    xml = ET.parse(SOURCE).getroot()
    assert output['status'] == 'extracted' and output['issues'] == []
    assert [t['tree_id'] for t in output['execution']['trees']] == [t.get('ID') for t in xml.findall('BehaviorTree')]
    assert output['execution']['root_attributes'] == xml.attrib
    assert [t['tree_order'] for t in output['execution']['trees']] == list(range(1, 5))
    for tree in xml.findall('BehaviorTree'):
        actual = calls(output, tree.get('ID')); expected = list(tree.find('Sequence'))
        assert len(actual) == len(expected)
        assert [r['step_index'] for r in actual] == list(range(1, len(actual) + 1))
        assert [r['xml_tag'] for r in actual] == [n.tag for n in expected]
        assert [r['xml_name'] for r in actual] == [n.get('name') for n in expected]
        assert [r['skill_id'] for r in actual] == [n.get('ID') if n.tag == 'SubTree' else n.tag for n in expected]
        assert all(r['source_refs'] for r in actual)
    assert data == before


def test_repeated_motion_calls_have_distinct_identity_and_bindings():
    rows = [r for r in calls(extract(), 'grasp') if r['xml_tag'] == 'MoveL']
    assert len(rows) == 3 and len({r['id'] for r in rows}) == 3
    assert [r['step_index'] for r in rows] == [3, 4, 6]
    assert len({b['id'] for r in rows for b in r['bindings']}) == sum(len(r['bindings']) for r in rows)


def test_binding_kinds_preserve_parent_interface_local_reads_and_output_writes():
    output = extract()
    capture = calls(output, 'Perception')[0]
    assert next(b for b in capture['bindings'] if b['parameter'] == 'target')['kind'] == 'parent_parameter'
    assert next(b for b in capture['bindings'] if b['parameter'] == 'pose')['kind'] == 'local_output'
    estimate = calls(output, 'Perception')[-1]
    assert next(b for b in estimate['bindings'] if b['parameter'] == 'intrinsic')['kind'] == 'local_input'
    assert next(b for b in estimate['bindings'] if b['parameter'] == 'timeout_s')['kind'] == 'literal'
    for tree in output['execution']['trees']:
        for row in tree['calls']:
            for b in row['bindings']:
                assert b['scope'] == tree['tree_id'] and b['source_refs']
                assert b['kind'] in {'parent_parameter', 'local_input', 'local_output', 'literal'}


def test_same_variable_spelling_in_different_scopes_does_not_merge_references():
    def same_key(tree, models):
        tree.find("BehaviorTree[@ID='Perception']/Sequence/MoveJ").set('pose', '{cc}')
    output = extract(same_key)
    references = output['execution']['data_references']
    keyed = {(r['scope'], r['key']): r['id'] for r in references}
    assert len(keyed) == len(references)
    # The modified Perception output and existing grasp output deliberately share a spelling.
    assert keyed['Perception', 'cc'] != keyed['grasp', 'cc']


def test_six_public_values_trace_to_unique_literals_and_keep_raw_units():
    output = extract(); rows = output['execution']['public_configs']
    expected = config()
    assert {(r['skill'], r['parameter']) for r in rows} == {(s, p) for s, names in PUBLIC.items() for p in names}
    assert len({r['binding_id'] for r in rows}) == 6
    for row in rows:
        assert row['value'] == expected[row['skill']][row['parameter']]
        assert row['source_refs'] and row['binding_id'] and row['invocation_id']
        assert row['confirmation_status'] == 'source_extracted'
        assert row['unit_status'] == 'unconfirmed'
    assert ('grasp', 'obj_in_robot') not in {(r['skill'], r['parameter']) for r in rows}


def test_setblackboard_public_position_uses_value_not_output_key():
    output = extract()
    row = next(r for r in output['execution']['public_configs'] if r['parameter'] == 'position')
    setter = next(r for r in calls(output, 'main') if r['id'] == row['invocation_id'])
    assert setter['xml_tag'] == 'SetBlackboard'
    value = next(b for b in setter['bindings'] if b['parameter'] == 'value')
    key = next(b for b in setter['bindings'] if b['parameter'] == 'output_key')
    assert row['binding_id'] == value['id'] and value['kind'] == 'literal' and key['kind'] == 'local_output'
    assert row['value'] != key['value']


def test_ports_preserve_types_directions_and_declared_default_presence():
    output = extract(); ports = output['execution']['ports']
    keys = [(p['owner'], p['parameter']) for p in ports]
    assert len(keys) == len(set(keys))
    move = next(p for p in ports if p['owner'] == 'MoveJ' and p['parameter'] == 'target')
    assert move['direction'] == 'input' and move['cpp_type'] and move['source_refs']
    assert all(type(p['default_present']) is bool for p in ports)
    assert all('declared_default' in p for p in ports if p['default_present'])


@pytest.mark.parametrize('case,code', [('fallback','execution.unsupported_template'),
    ('nested','execution.unsupported_template'), ('missing_tree','execution.missing_metadata'),
    ('duplicate_tree','execution.missing_metadata'), ('bad_main','execution.missing_metadata'),
    ('recursive','execution.unsupported_template'), ('reorder','execution.missing_order')])
def test_unsupported_structure_is_source_located_and_never_guessed(case, code):
    def mutate(tree, models):
        seq = main(tree)
        if case == 'fallback': seq.tag = 'Fallback'
        elif case == 'nested': ET.SubElement(seq[0], 'Sequence')
        elif case == 'missing_tree': tree.find('BehaviorTree').attrib.pop('ID')
        elif case == 'duplicate_tree': tree.append(deepcopy(tree.find('BehaviorTree')))
        elif case == 'bad_main': tree.set('main_tree_to_execute', 'nonexistent')
        elif case == 'recursive': seq[4].set('ID', 'main')
        else: seq[2], seq[3] = seq[3], seq[2]
    data = extraction_input(mutate)
    issues = blocked(api().extract_execution(**data), code)
    issue_sources(issues, data['evidence_bundle'])


@pytest.mark.parametrize('case', ['unassigned', 'dynamic', 'two_writers', 'write_after_use', 'self_reference'])
def test_public_variable_requires_unique_static_producer_before_use(case):
    def mutate(tree, models):
        seq = main(tree); setter = seq[1]; key = setter.get('output_key')
        if case == 'unassigned': setter.set('output_key', 'other_capture_key')
        elif case == 'dynamic': setter.set('value', '{runtime_result}')
        elif case == 'two_writers': seq.insert(2, deepcopy(setter))
        elif case == 'write_after_use': seq.remove(setter); seq.append(setter)
        else: setter.set('value', '{' + key + '}')
    data = extraction_input(mutate)
    issue_sources(blocked(api().extract_execution(**data), 'execution.binding_ambiguous'), data['evidence_bundle'])


@pytest.mark.parametrize('case', ['wrong_type', 'duplicate_port', 'missing_type', 'direction_conflict'])
def test_invalid_ports_block_with_entity_and_source_evidence(case):
    def mutate(tree, models):
        model = models.find("TreeNodesModel/Action[@ID='MoveJ']")
        port = model.find("input_port[@name='target']")
        if case == 'wrong_type': port.set('type', 'bool')
        elif case == 'duplicate_port': model.append(deepcopy(port))
        elif case == 'missing_type':
            for root in (tree, models):
                for p in root.findall("TreeNodesModel/Action[@ID='MoveJ']/input_port[@name='target']"):
                    p.attrib.pop('type', None)
        else: port.tag = 'output_port'
    data = extraction_input(mutate)
    issue_sources(blocked(api().extract_execution(**data), 'execution.type_conflict'), data['evidence_bundle'])


@pytest.mark.parametrize('case', ['confirmation_hash', 'source_digest', 'vocabulary_digest', 'tampered_fact'])
def test_stale_confirmation_or_source_facts_cannot_authorize_reextraction(case):
    data = extraction_input()
    if case == 'confirmation_hash': data['confirmation']['sha256'] = '0' * 64
    elif case == 'source_digest': data['confirmation']['source_sha256']['behavior_tree'] = '0' * 64
    elif case == 'vocabulary_digest': data['confirmation']['vocabulary_sha256'] = ''
    else:
        fact = next(e for e in data['evidence_bundle']['evidence'] if e.get('facts', {}).get('attributes'))
        fact['facts']['attributes']['injected'] = 'unconfirmed'
    output = api().extract_execution(**data)
    blocked(output, 'execution.source_integrity')
    assert output.get('execution') is None

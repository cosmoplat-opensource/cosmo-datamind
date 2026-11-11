"""Deterministic extraction, bindings and honest omissions — DR-070 / T04 RED."""
from copy import deepcopy
import inspect

import pytest

from tests.robotics_modeling_support import api, bundle, confirmation, ur_schema, no_network

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def extract(evidence=None, tbox=None):
    evidence = bundle() if evidence is None else evidence
    tbox = ur_schema() if tbox is None else tbox
    return api('abox').extract(confirmation(tbox, evidence), evidence)


def small_sources():
    return {'behavior_tree': b'<root><BehaviorTree ID="Main"><Sequence>'
            b'<Read input="1" output="{v}"/><Read input="{v}" output="{w}"/>'
            b'</Sequence></BehaviorTree></root>',
            'node_models': b'<root><TreeNodesModel><Action ID="Read">'
            b'<input_port name="input" type="int"/><output_port name="output" type="int"/>'
            b'</Action></TreeNodesModel></root>'}


def test_abox_is_deterministic_without_model(monkeypatch):
    evidence, tbox = bundle(), ur_schema()
    sealed = confirmation(tbox, evidence)
    before = deepcopy((sealed, evidence))
    # Tripwire also covers accidental calls to the T03 transport adapter.
    from domain_runs.robotics_build_routing import BuildRoute
    def forbidden(*args, **kwargs):
        pytest.fail('ABox must not call a model')
    monkeypatch.setattr(BuildRoute, 'request', forbidden)
    module = api('abox')
    assert not {'route', 'model', 'client', 'prompt'} & set(inspect.signature(module.extract).parameters)
    first = module.extract(sealed, evidence)
    assert first == module.extract(sealed, evidence)
    assert (sealed, evidence) == before
    assert first['report']['method'] == 'xml_rules'
    coverage = first['report']['source_coverage']
    assert coverage['calls_total'] == coverage['calls_emitted'] == 25
    assert coverage['bindings_total'] == coverage['bindings_emitted'] == 129
    assert len({r['id'] for r in first['result']['individuals'] if 'SkillInvocation' in r['class_ids']}) == 25
    assert len({r['id'] for r in first['result']['individuals'] if 'ParameterBinding' in r['class_ids']}) == 129
    allowed = {row['id'] for row in evidence['evidence']}
    for group in ('individuals', 'object_assertions', 'data_assertions'):
        assert all(row['source_refs'] and set(row['source_refs']) <= allowed for row in first['result'][group])


def test_repeated_calls_and_bindings_remain_distinct():
    product = extract(bundle(small_sources()))
    result, coverage = product['result'], product['report']['source_coverage']
    calls = {r['id'] for r in result['individuals'] if 'SkillInvocation' in r['class_ids']}
    assert len(calls) == coverage['calls_emitted'] == 2
    assert len({r['id'] for r in coverage['binding_sites']}) == 4
    skill_links = [r for r in result['object_assertions'] if r['subject'] in calls and r['property'] == 'invokesSkill']
    assert len(skill_links) == 2 and len({r['object'] for r in skill_links}) == 1
    indexes = {r['value'] for r in result['data_assertions'] if r['subject'] in calls and r['property'] == 'stepIndex'}
    assert indexes == {1, 2}
    sites = {(s['step_index'], s['parameter']): s['id'] for s in coverage['binding_sites']}
    output = next(r['object'] for r in result['object_assertions']
                  if r['subject'] == sites[1, 'output'] and r['property'] == 'targetData')
    input_value = next(r['object'] for r in result['object_assertions']
                       if r['subject'] == sites[2, 'input'] and r['property'] == 'sourceData')
    assert output == input_value
    assert any(r['subject'] == sites[1, 'input'] and r['property'] == 'literalValue' and r['value'] == '1'
               for r in result['data_assertions'])


@pytest.mark.parametrize('case', ['control', 'unknown_node', 'duplicate_port', 'undeclared_port', 'bad_output', 'cycle'])
def test_unsupported_structure_is_located_and_never_yields_candidate(case):
    abox = api('abox')
    values = small_sources()
    if case == 'control': values['behavior_tree'] = values['behavior_tree'].replace(b'Sequence', b'Fallback')
    elif case == 'unknown_node': values['behavior_tree'] = values['behavior_tree'].replace(b'<Read ', b'<Unknown ')
    elif case == 'duplicate_port': values['node_models'] = values['node_models'].replace(
        b'</Action>', b'<input_port name="input"/></Action>')
    elif case == 'undeclared_port': values['behavior_tree'] = values['behavior_tree'].replace(b'input="1"', b'unknown="1"')
    elif case == 'bad_output': values['behavior_tree'] = values['behavior_tree'].replace(b'output="{v}"', b'output="literal"')
    elif case == 'cycle': values['behavior_tree'] = (
        b'<root><BehaviorTree ID="Main"><Sequence><SubTree ID="Main"/></Sequence></BehaviorTree></root>')
    evidence = bundle(values)
    before = deepcopy(evidence)
    with pytest.raises(ValueError) as failure:
        abox.extract(confirmation(ur_schema(), evidence), evidence)
    issues = failure.value.issues
    assert issues and any(i.get('source_role') in values for i in issues)
    assert any(i.get('location') or i.get('source_ref') for i in issues)
    assert evidence == before


@pytest.mark.parametrize('term,group', [('hasBinding', 'object_properties'), ('bindingDirection', 'data_properties')])
def test_unsupported_structure_and_missing_terms_are_reported(term, group):
    tbox = ur_schema()
    tbox[group] = [row for row in tbox[group] if row['id'] != term]
    product = extract(tbox=tbox)
    report = product['report']
    assert any(item['term_id'] == term for item in report['missing_terms'])
    assert report['skipped_assertions'] and product['result']['clarifications']
    assert all(r['property'] != term for key in ('object_assertions', 'data_assertions')
               for r in product['result'][key])


def test_optional_unmapped_property_is_reported_without_invented_fact():
    tbox = ur_schema()
    row = deepcopy(tbox['data_properties'][0])
    row.update(id='siteVerified', label='合成未映射属性', datatype='xsd:boolean',
               domain_ids=[], class_ids=['SkillInvocation'])
    tbox['data_properties'].append(row)
    product = extract(tbox=tbox)
    assert 'siteVerified' in product['report']['selected_unmapped_terms']['data_properties']
    coverage = next(r for r in product['report']['property_coverage'] if r['id'] == 'siteVerified')
    assert coverage['assertions'] == 0 and coverage['reason']
    assert not any(r['property'] == 'siteVerified' for r in product['result']['data_assertions'])


@pytest.mark.parametrize('case', ['missing_confirmation', 'schema_changed', 'source_changed'])
def test_extraction_requires_unchanged_confirmed_schema_and_sources(case):
    abox, evidence = api('abox'), bundle()
    sealed = confirmation(ur_schema(), evidence)
    if case == 'missing_confirmation': sealed = None
    elif case == 'schema_changed': sealed['tbox']['classes'][0]['definition'] += 'tampered'
    else: sealed['source_sha256']['behavior_tree'] = '0' * 64
    with pytest.raises(ValueError):
        abox.extract(sealed, evidence)


@pytest.mark.parametrize('case', ['missing_individual', 'wrong_domain', 'wrong_range', 'bad_source',
    'bool_as_integer', 'string_as_integer', 'invented_property'])
def test_abox_assertions_are_validated_against_schema_and_source(case):
    abox, evidence, tbox = api('abox'), bundle(), ur_schema()
    result = extract(evidence, tbox)['result']
    if case == 'missing_individual': result['object_assertions'][0]['object'] = 'not_an_individual'
    elif case in ('wrong_domain', 'wrong_range'):
        row = next(r for r in result['object_assertions'] if r['property'] == 'invokesSkill')
        row['subject' if case == 'wrong_domain' else 'object'] = next(
            r['id'] for r in result['individuals'] if 'ParameterBinding' in r['class_ids'])
    elif case == 'bad_source': result['individuals'][0]['source_refs'] = ['missing_evidence']
    elif case in ('bool_as_integer', 'string_as_integer'):
        row = next(r for r in result['data_assertions'] if r['property'] == 'stepIndex')
        row['value'] = True if case == 'bool_as_integer' else '1'
    else: result['data_assertions'][0]['property'] = 'invented'
    with pytest.raises(ValueError):
        abox.validate_result(result, tbox, evidence['evidence'])

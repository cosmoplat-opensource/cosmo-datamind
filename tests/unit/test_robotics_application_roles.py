"""T02 RED: explicit semantic roles and stage-specific requirements — DR-072."""
from copy import deepcopy

import pytest

from tests.robotics_modeling_support import digest, no_network, ur_schema
from tests.robotics_adaptation_support import api, role_map, blocked, EXECUTION_CLASSES, EXECUTION_RELATIONS

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def resolve(tbox=None, mapping=None, required_for='execution'):
    tbox = ur_schema() if tbox is None else tbox
    return api().resolve_roles(tbox=tbox, role_map=role_map(tbox) if mapping is None else mapping,
                               required_for=required_for)


def test_explicit_full_iri_map_is_readonly_and_preserves_term_kinds():
    schema = ur_schema(); mapping = role_map(schema); before = deepcopy((schema, mapping))
    output = resolve(schema, mapping)
    assert output['status'] == 'passed' and not output['issues']
    assert output['roles']['classes']['AtomicSkill'] == schema['namespace'] + 'AtomicSkill'
    assert output['roles']['object_properties']['hasStep'] == schema['namespace'] + 'hasStep'
    assert (schema, mapping) == before


@pytest.mark.parametrize('kind,term', [('classes', 'SkillInvocation'), ('classes', 'ParameterBinding'),
    ('classes', 'SkillImplementation'), ('classes', 'PortContract'), ('classes', 'DataReference'),
    ('object_properties', 'hasStep'), ('object_properties', 'hasBinding'),
    ('object_properties', 'sourceParameter'), ('object_properties', 'mapsToPort')])
def test_missing_explicit_role_is_schema_issue_not_missing_business_instance(kind, term):
    schema = ur_schema(); mapping = role_map(schema)
    mapping['bindings'] = [r for r in mapping['bindings'] if not (r['kind'] == kind and r['role'] == term)]
    issues = blocked(resolve(schema, mapping), 'schema.missing_role')
    assert any(i['role'] == term and i['kind'] == kind and i['layer'] == 'schema' for i in issues)


@pytest.mark.parametrize('case', ['unconfirmed', 'stale_digest', 'wrong_namespace', 'wrong_kind',
    'duplicate_role', 'one_iri_two_incompatible_roles'])
def test_untrusted_or_ambiguous_role_mapping_cannot_authorize_adaptation(case):
    schema = ur_schema(); mapping = role_map(schema)
    binding = next(r for r in mapping['bindings'] if r['role'] == 'SkillInvocation')
    if case == 'unconfirmed': mapping['confirmed'] = False
    elif case == 'stale_digest': mapping['tbox_sha256'] = '0' * 64
    elif case == 'wrong_namespace': binding['iri'] = 'https://unconfirmed.invalid/#SkillInvocation'
    elif case == 'wrong_kind': binding['kind'] = 'object_properties'
    elif case == 'duplicate_role': mapping['bindings'].append(deepcopy(binding))
    else: binding['iri'] = schema['namespace'] + 'AtomicSkill'
    blocked(resolve(schema, mapping), 'schema.role_conflict')


@pytest.mark.parametrize('case', ['reversed_relation', 'wrong_range', 'wrong_parent', 'wrong_datatype'])
def test_names_do_not_override_incompatible_domain_range_or_inheritance(case):
    schema = ur_schema()
    if case in ('reversed_relation', 'wrong_range'):
        row = next(r for r in schema['object_properties'] if r['id'] == 'hasStep')
        if case == 'reversed_relation': row['domain_ids'], row['range_ids'] = row['range_ids'], row['domain_ids']
        else: row['range_ids'] = ['SkillParameter']
    elif case == 'wrong_parent':
        next(r for r in schema['classes'] if r['id'] == 'AtomicSkill')['parent_ids'] = ['SkillInvocation']
    else: next(r for r in schema['data_properties'] if r['id'] == 'stepIndex')['datatype'] = 'xsd:string'
    blocked(resolve(schema), 'schema.role_conflict')


def test_user_confirmed_renamed_class_requires_semantics_not_lexical_match():
    schema = ur_schema()
    for group in ('classes', 'object_properties', 'data_properties'):
        for r in schema[group]:
            for key in ('parent_ids', 'domain_ids', 'range_ids', 'class_ids'):
                if key in r: r[key] = ['CallSite' if name == 'SkillInvocation' else name for name in r[key]]
    next(r for r in schema['classes'] if r['id'] == 'SkillInvocation')['id'] = 'CallSite'
    mapping = role_map(schema)
    next(r for r in mapping['bindings'] if r['role'] == 'CallSite')['role'] = 'SkillInvocation'
    assert resolve(schema, mapping)['roles']['classes']['SkillInvocation'] == schema['namespace'] + 'CallSite'
    assert mapping['tbox_sha256'] == digest(schema)


def test_execution_stage_does_not_require_unselected_task_business_roles():
    schema = ur_schema(); mapping = role_map(schema)
    mapping['bindings'] = [r for r in mapping['bindings'] if r['role'] in EXECUTION_CLASSES | EXECUTION_RELATIONS
                           or r['kind'] == 'data_properties']
    assert resolve(schema, mapping)['status'] == 'passed'
    blocked(resolve(schema, mapping, required_for='task'), 'schema.missing_role')

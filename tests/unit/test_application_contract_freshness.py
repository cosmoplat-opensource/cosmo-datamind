"""Bind application decisions to current facts, never client readiness — DR-071 / IR-025 T02 RED."""
from copy import deepcopy

import pytest

from tests.application_contract_support import (
    ENTRYPOINT, api, basis, contract, evaluate, inputs, no_network, problem, registry, result,
)

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_identical_effective_inputs_have_current_freshness_independent_of_dict_order():
    core = api()
    data = inputs()
    report = evaluate(data=data)
    reordered = deepcopy(data)
    reordered['task']['content'] = dict(reversed(list(reordered['task']['content'].items())))
    assert basis(data) == basis(reordered)
    assert core.compare_freshness(report, basis(reordered)) == 'current'


@pytest.mark.parametrize('change', ['asset_id', 'version_id', 'payload', 'lifecycle', 'task_session',
                                    'task_revision', 'task_content', 'task_removed', 'mode',
                                    'generation_policy', 'environment', 'evidence'])
def test_each_execution_relevant_change_invalidates_old_ready_result(change):
    core = api()
    data = inputs()
    report = evaluate(data=data)
    original = deepcopy(report)
    changed = deepcopy(data)
    if change == 'asset_id':
        changed['snapshots'][0]['ontology_id'] = 'ont-other'
    elif change == 'version_id':
        changed['snapshots'][0]['version_id'] = 'ver-2'
    elif change == 'payload':
        changed['snapshots'][0]['payload'] = b'changed effective payload including edits'
    elif change == 'lifecycle':
        changed['snapshots'][0]['lifecycle_revision'] += 1
    elif change == 'task_session':
        changed['task']['session_id'] = 'task-2'
    elif change == 'task_revision':
        changed['task']['revision'] += 1
    elif change == 'task_content':
        changed['task']['content']['part'] = 'different-part'
    elif change == 'task_removed':
        changed['task'] = None
    elif change == 'mode':
        changed['context']['mode'] = 'demo'
    elif change == 'generation_policy':
        changed['context']['generation_policy'] = 'strict'
    elif change == 'environment':
        changed['environment']['revision'] = 'env-2'
    elif change == 'evidence':
        changed['environment']['evidence_revision'] += 1
    assert core.compare_freshness(report, basis(changed)) == 'stale'
    assert report == original, 'Historical judgment must not be overwritten on read'


@pytest.mark.parametrize('changed', [{'application_id': 'data.deep_qa'}, {'contract_version': '2'}])
def test_other_application_or_contract_revision_cannot_reuse_ready(changed):
    core = api()
    report = evaluate()
    assert core.compare_freshness(report, basis(**changed)) == 'stale'


def test_freshness_hashes_actual_payload_instead_of_accepting_a_supplied_digest():
    data = inputs()
    data['snapshots'][0]['payload_sha256'] = '0' * 64
    first = basis(data)
    data['snapshots'][0]['payload'] += b'changed'
    assert basis(data) != first


@pytest.mark.parametrize('change', ['second_payload', 'second_version', 'reordered', 'removed'])
def test_complete_ordered_multi_asset_vector_is_part_of_freshness(change):
    core = api()
    data = inputs()
    data['snapshots'].append({**data['snapshots'][0], 'ontology_id': 'ont-b', 'version_id': 'ver-b'})
    spec, _ = contract()
    spec['asset_cardinality']['max'] = 2  # Synthetic multi-asset app, not implemented deep QA.
    report = evaluate(spec=spec, data=data)
    if change == 'second_payload':
        data['snapshots'][1]['payload'] += b'edited'
    elif change == 'second_version':
        data['snapshots'][1]['version_id'] = 'ver-b2'
    elif change == 'reordered':
        data['snapshots'].reverse()
    else:
        data['snapshots'].pop()
    assert core.compare_freshness(report, basis(data)) == 'stale'


def test_unavailable_current_environment_is_unknown_not_current_or_implicit_recheck():
    core = api()
    spec, calls = contract()
    report = evaluate(spec=spec)
    counts = {k: len(v.calls) for k, v in calls.items()}
    assert core.compare_freshness(report, None) == 'unknown'
    assert {k: len(v.calls) for k, v in calls.items()} == counts


def test_execution_guard_rechecks_even_when_previous_report_is_ready_and_current():
    core = api()
    spec, calls = contract()
    reg = registry(spec)
    previous = core.evaluate(registry=reg, entrypoint=ENTRYPOINT, **inputs())
    before = {k: len(v.calls) for k, v in calls.items()}
    reads = []
    def resolve():
        reads.append(True)
        return inputs()
    authorized = core.require_ready(registry=reg, entrypoint=ENTRYPOINT,
                                     resolve_context=resolve, previous_report=previous)
    assert reads
    assert authorized['execution_status'] == 'ready'
    assert authorized['basis'] == basis()
    assert all(len(calls[k].calls) > count for k, count in before.items())


@pytest.mark.parametrize('prior', ['forged_ready', 'old_version', 'other_application'])
def test_client_or_stale_report_cannot_authorize_a_currently_blocked_task(prior):
    core = api()
    previous = evaluate()
    if prior == 'forged_ready':
        previous.update(execution_status='ready', issues=[], allowed_actions=['generate'])
    elif prior == 'old_version':
        previous['asset_refs'][0]['version_id'] = 'ver-old'
    else:
        previous['application_id'] = 'data.deep_qa'
    spec, calls = contract(overrides={'execution': [result('execution', 'missing', issues=[problem()])]})
    live = inputs()
    live['snapshots'][0]['version_id'] = 'ver-current'
    with pytest.raises(core.ContractError) as caught:
        core.require_ready(registry=registry(spec), entrypoint=ENTRYPOINT,
                           resolve_context=lambda: deepcopy(live), previous_report=previous)
    assert caught.value.code == 'application_not_ready'
    assert calls['execution'].calls
    assert calls['execution'].calls[0]['snapshots'][0]['version_id'] == 'ver-current'


def test_execution_guard_rejects_preparation_only_when_task_is_absent():
    core = api()
    with pytest.raises(core.ContractError) as caught:
        core.require_ready(registry=registry(), entrypoint=ENTRYPOINT,
                           resolve_context=lambda: inputs(with_task=False))
    assert caught.value.code == 'application_not_ready'


def test_execution_guard_does_not_turn_checker_failure_into_permission():
    core = api()
    spec, _ = contract(overrides={'environment': TimeoutError('private upstream failed')})
    with pytest.raises(core.ContractError) as caught:
        core.require_ready(registry=registry(spec), entrypoint=ENTRYPOINT, resolve_context=inputs)
    assert caught.value.code == 'application_not_ready'
    assert 'private upstream' not in str(caught.value)

"""Layer outcomes, diagnostics and read-only evaluation — DR-071 / IR-025 T02 RED."""
from copy import deepcopy
import hashlib
import json

import pytest

from tests.application_contract_support import (
    APP, CODES, ENTRYPOINT, LAYERS, api, check, contract, evaluate, inputs, no_network, problem, result,
)

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_all_required_checks_pass_for_this_task_without_claiming_physical_execution():
    report = evaluate()
    assert report['application_id'] == APP and report['entrypoint'] == ENTRYPOINT
    assert set(report['layers']) == set(LAYERS)
    assert report['preparation_status'] == 'available'
    assert report['execution_status'] == 'ready'
    assert report['execution_readiness'] == 'not_verified'
    assert all(check(report, layer)['status'] == 'passed' for layer in LAYERS)
    assert report['asset_refs'] == [{'ontology_id': 'ont-a', 'version_id': 'ver-1'}]
    assert report['payload_hashes'] == [hashlib.sha256(inputs()['snapshots'][0]['payload']).hexdigest()]


def test_absent_task_allows_preparation_but_never_execution():
    spec, calls = contract()
    report = evaluate(spec=spec, data=inputs(with_task=False))
    assert report['preparation_status'] == 'available'
    assert report['execution_status'] == 'not_evaluated'
    assert report['task_ref'] is None and report['task_hash'] is None
    assert check(report, 'task')['status'] == 'not_evaluated'
    assert check(report, 'task')['reason']
    assert calls['task'].calls == []
    assert len(calls['schema'].calls) == len(calls['execution'].calls) == 1


@pytest.mark.parametrize('status', ['missing', 'unsupported', 'invalid', 'error', 'not_evaluated'])
def test_every_nonpassing_required_outcome_blocks_execution(status):
    spec, _ = contract(overrides={'execution': [result('execution', status, issues=[problem()], reason='缺执行知识')]})
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'blocked'
    assert report['preparation_status'] == 'blocked'
    assert check(report, 'execution')['status'] == status


def test_optional_warning_does_not_block_an_otherwise_complete_task():
    gap = problem(layer='environment', code='environment.optional_evidence')
    gap['severity'] = 'warning'
    spec, _ = contract(overrides={'environment': [result('environment', 'missing', issues=[gap])]})
    spec['checkers'][-1]['required'] = False
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'ready'
    assert any(issue['code'] == gap['code'] for issue in report['issues'])


def test_warning_severity_cannot_downgrade_a_required_missing_check():
    gap = problem()
    gap['severity'] = 'warning'
    spec, _ = contract(overrides={'execution': [result('execution', 'missing', issues=[gap], required=False)]})
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'blocked'
    assert check(report, 'execution')['required'] is True


def test_proven_not_applicable_is_distinct_from_missing():
    spec, _ = contract(overrides={'environment': [result('environment', 'not_evaluated', applicable=False,
                                                        reason='该合成策略不需要外部连接')]})
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'ready'
    assert check(report, 'environment')['applicable'] is False
    assert check(report, 'environment')['reason']


@pytest.mark.parametrize('bad_output', [
    [], [result('execution'), result('execution')],
    [dict(result('execution'), check_code='not.registered')],
    [result('execution', 'probably_ready')],
    [result('execution', 'not_evaluated', applicable=False)],
    [result('execution', 'not_evaluated')],
    [result('execution', status=True)], None, 'passed',
])
def test_malformed_or_incomplete_checker_output_is_error_not_ready(bad_output):
    spec, calls = contract(overrides={'execution': bad_output})
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'blocked'
    assert check(report, 'execution')['status'] == 'error'
    assert report['issues']
    assert len(calls['environment'].calls) == 1, 'Independent diagnostics must not be discarded'


def test_missing_one_of_multiple_declared_checks_is_not_vacuous_success():
    spec, _ = contract()
    spec['checkers'][1]['required_codes'].append('execution.order')
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'blocked'
    outcomes = {row['check_code']: row for row in report['layers']['execution']['checks']}
    assert outcomes['execution.order']['status'] == 'error'


def test_dependency_failure_skips_dependent_checks_but_keeps_independent_evidence():
    gap = problem('schema', code='schema.missing_role')
    spec, calls = contract(overrides={'schema': [result('schema', 'missing', issues=[gap])]})
    spec['checkers'][1]['depends_on'] = [CODES['schema']]
    spec['checkers'][2]['depends_on'] = [CODES['execution']]
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'blocked'
    assert calls['execution'].calls == calls['task'].calls == []
    assert check(report, 'execution')['status'] == 'not_evaluated'
    assert check(report, 'execution')['reason']
    assert check(report, 'task')['status'] == 'not_evaluated'
    assert check(report, 'environment')['status'] == 'passed'
    assert len(calls['environment'].calls) == 1


def test_checker_exception_is_sanitized_and_other_checks_still_run():
    secret = 'api-key-do-not-log https://private.invalid/v1 /private/runtime/secret'
    spec, calls = contract(overrides={'execution': RuntimeError(secret)})
    report = evaluate(spec=spec)
    assert report['execution_status'] == 'blocked'
    assert check(report, 'execution')['status'] == 'error'
    assert any(issue['code'] == 'system.checker_failed' for issue in report['issues'])
    assert len(calls['environment'].calls) == 1
    assert secret not in json.dumps(report)
    assert 'api-key-do-not-log' not in json.dumps(report)


def test_issue_keeps_precise_entity_evidence_and_unavailable_repair_without_dead_link():
    gap = problem()
    spec, _ = contract(overrides={'execution': [result('execution', 'missing', issues=[gap])]})
    report = evaluate(spec=spec)
    issue = next(row for row in report['issues'] if row['code'] == gap['code'])
    for field in ('layer', 'severity', 'expected', 'observed', 'entity_refs', 'evidence_refs'):
        assert issue[field] == gap[field]
    action = issue['repair_actions'][0]
    assert action['action_id'] == 'robotics.reextract'
    assert action['availability'] == 'unavailable', 'A suggested label cannot create a registered handler'
    assert not action.get('href') and action['action_id'] not in report['allowed_actions']
    assert inputs()['snapshots'][0]['version_id'] == report['asset_refs'][0]['version_id']


def test_two_diagnostics_do_not_lose_one_issue_when_local_ids_collide():
    a = problem('schema', code='schema.missing_role')
    b = problem('execution')
    spec, _ = contract(overrides={'schema': [result('schema', 'missing', issues=[a])],
                                 'execution': [result('execution', 'missing', issues=[b])]})
    report = evaluate(spec=spec)
    assert {row['code'] for row in report['issues']} >= {a['code'], b['code']}
    ids = [row['issue_id'] for row in report['issues']]
    assert len(ids) == len(set(ids))
    for layer in ('schema', 'execution'):
        assert set(check(report, layer)['issue_ids']) <= set(ids)
        assert check(report, layer)['issue_ids']


def test_evaluation_does_not_mutate_snapshot_task_or_checker_owned_output():
    data = inputs()
    gap = problem()
    spec, calls = contract(overrides={'execution': [result('execution', 'missing', issues=[gap])]})
    before = deepcopy(data)
    outputs = {layer: deepcopy(c.response) for layer, c in calls.items()}
    report = evaluate(spec=spec, data=data)
    report['issues'][0]['observed'].append('client-edited')
    assert data == before
    assert {layer: c.response for layer, c in calls.items()} == outputs
    again = evaluate(spec=spec, data=data)
    assert all('client-edited' not in i['observed'] for i in again['issues'])


def test_checker_cannot_change_inputs_seen_by_independent_checks():
    data = inputs()
    spec, calls = contract()
    def mutating_checker(context):
        context['snapshots'][0]['payload'] = b'changed'
        context['task']['content']['part'] = 'changed'
        return [result('schema')]
    spec['checkers'][0]['check'] = mutating_checker
    before = deepcopy(data)
    evaluate(spec=spec, data=data)
    assert data == before
    assert calls['execution'].calls[0]['snapshots'][0]['payload'] == before['snapshots'][0]['payload']
    assert calls['execution'].calls[0]['task'] == before['task']


def test_wrong_payload_kind_produces_diagnostic_without_invoking_robotics_checker():
    data = inputs()
    data['snapshots'][0].update(domain='data', payload_kind='data_ir', payload=b'{}')
    spec, calls = contract()
    report = evaluate(spec=spec, data=data)
    assert report['execution_status'] == 'blocked'
    assert any(row['status'] == 'unsupported' for row in report['layers']['schema']['checks'])
    assert all(not c.calls for c in calls.values())


@pytest.mark.parametrize('bad_refs', ['empty', 'duplicate', 'two_versions', 'latest'])
def test_robotics_requires_exactly_one_fixed_asset_version(bad_refs):
    core = api()
    data = inputs()
    if bad_refs == 'empty':
        data['snapshots'] = []
    elif bad_refs == 'duplicate':
        data['snapshots'] *= 2
    elif bad_refs == 'two_versions':
        data['snapshots'].append({**data['snapshots'][0], 'version_id': 'ver-2'})
    else:
        data['snapshots'][0]['version_id'] = 'latest'
    with pytest.raises(core.ContractError) as caught:
        evaluate(data=data)
    assert caught.value.code == 'invalid_request'


@pytest.mark.parametrize('mode,policy', [('real', 'offline'), ('real', 'strict'), ('demo', 'offline'), ('demo', 'strict')])
def test_mode_and_generation_policy_remain_separate_dimensions(mode, policy):
    data = inputs()
    data['context'] = {'mode': mode, 'generation_policy': policy}
    spec, calls = contract()
    report = evaluate(spec=spec, data=data)
    assert report['context'] == data['context']
    assert calls['environment'].calls[0]['context'] == data['context']


@pytest.mark.parametrize('context', [{'mode': 'offline', 'generation_policy': 'real'},
                                      {'mode': 'real', 'generation_policy': 'relaxed'},
                                      {'mode': True, 'generation_policy': 'offline'}])
def test_unknown_or_swapped_mode_dimensions_are_rejected(context):
    core = api()
    data = inputs()
    data['context'] = context
    with pytest.raises(core.ContractError) as caught:
        evaluate(data=data)
    assert caught.value.code == 'invalid_request'

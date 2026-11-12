"""Backend registration and fixed application entrypoints — DR-071 / IR-025 T02 RED."""
from copy import deepcopy

import pytest

from tests.application_contract_support import APP, ENTRYPOINT, api, contract, inputs, no_network

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_registration_resolves_bound_entrypoint_without_running_checkers():
    core = api()
    spec, calls = contract()
    reg = core.Registry()
    reg.register(spec)
    resolved = reg.resolve(ENTRYPOINT)
    assert resolved['application_id'] == APP and resolved['contract_version'] == '1'
    assert resolved['availability'] == 'available'
    assert all(not checker.calls for checker in calls.values())


@pytest.mark.parametrize('collision', ['application_id', 'entrypoint'])
def test_duplicate_registration_does_not_overwrite_original(collision):
    core = api()
    spec, _ = contract()
    reg = core.Registry()
    reg.register(spec)
    other, _ = contract(application_id=APP if collision == 'application_id' else 'test.other',
                        entrypoint='test.other' if collision == 'application_id' else ENTRYPOINT)
    with pytest.raises(core.ContractError) as caught:
        reg.register(other)
    assert caught.value.code == 'registration_conflict'
    assert reg.resolve(ENTRYPOINT)['application_id'] == APP


@pytest.mark.parametrize('malformation', ['no_checkers', 'empty_required_codes', 'missing_layer',
                                          'duplicate_code', 'non_callable', 'bad_dependency'])
def test_incomplete_registration_cannot_create_an_always_ready_application(malformation):
    core = api()
    spec, _ = contract()
    if malformation == 'no_checkers':
        spec['checkers'] = []
    elif malformation == 'empty_required_codes':
        spec['checkers'][0]['required_codes'] = []
    elif malformation == 'missing_layer':
        spec['checkers'].pop()
    elif malformation == 'duplicate_code':
        spec['checkers'].append(deepcopy(spec['checkers'][0]))
    elif malformation == 'non_callable':
        spec['checkers'][0]['check'] = 'os.system'
    else:
        spec['checkers'][0]['depends_on'] = ['nonexistent.check']
    with pytest.raises(core.ContractError) as caught:
        core.Registry().register(spec)
    assert caught.value.code == 'invalid_contract'


def test_registry_snapshot_cannot_be_rebound_through_mutable_input_or_return():
    core = api()
    spec, _ = contract()
    reg = core.Registry()
    reg.register(spec)
    spec['entrypoints'].append('injected.entrypoint')
    resolved = reg.resolve(ENTRYPOINT)
    resolved['accepted_payload_kinds'].append('data_ir')
    resolved['application_id'] = 'injected.app'
    assert reg.resolve(ENTRYPOINT)['application_id'] == APP
    assert reg.resolve(ENTRYPOINT)['accepted_payload_kinds'] == ['robotics_rdf']
    with pytest.raises(core.ContractError):
        reg.resolve('injected.entrypoint')


def test_unknown_entrypoint_has_no_default_application():
    core = api()
    reg = core.Registry()
    reg.register(contract()[0])
    with pytest.raises(core.ContractError) as caught:
        core.evaluate(registry=reg, entrypoint='unknown.execute', **inputs())
    assert caught.value.code == 'unknown_entrypoint'


def test_declared_but_unavailable_adapter_cannot_return_ready():
    core = api()
    spec, calls = contract()
    spec['availability'] = 'unavailable'
    reg = core.Registry()
    reg.register(spec)
    with pytest.raises(core.ContractError) as caught:
        core.evaluate(registry=reg, entrypoint=ENTRYPOINT, **inputs())
    assert caught.value.code == 'application_unavailable'
    assert all(not c.calls for c in calls.values())


def test_same_version_can_have_different_results_for_two_applications():
    from tests.application_contract_support import result, problem
    core = api()
    one, _ = contract()
    two, _ = contract(application_id='test.inspection', entrypoint='test.inspect',
                      overrides={'execution': [result('execution', 'missing', issues=[problem()])]})
    reg = core.Registry()
    reg.register(one)
    reg.register(two)
    a = core.evaluate(registry=reg, entrypoint=ENTRYPOINT, **inputs())
    b = core.evaluate(registry=reg, entrypoint='test.inspect', **inputs())
    assert a['asset_refs'] == b['asset_refs']
    assert a['application_id'] != b['application_id']
    assert a['execution_status'] == 'ready' and b['execution_status'] == 'blocked'

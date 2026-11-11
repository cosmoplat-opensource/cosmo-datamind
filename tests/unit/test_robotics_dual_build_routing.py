"""Configuration routing and single-provider failure boundaries — DR-070 / IR-024 T02 RED."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from domain_runs.build_service import BuildRequestError
from tests.robotics_dual_build_support import (
    RuntimeRegistry, ScriptedRuntime, TEST_BASE, TEST_KEY, TEST_MODEL,
    isolated_platform, model_config, routing_api,
)

__all__ = ['isolated_platform']


def resolve(*, config=None, environ=None, registry=None):
    return routing_api().resolve_route(
        config={} if config is None else config,
        environ={} if environ is None else environ,
        registry=RuntimeRegistry() if registry is None else registry,
    )


def test_no_runtime_uses_legacy_without_network(isolated_platform):
    registry = RuntimeRegistry()
    route = resolve(registry=registry)
    assert route.public() == {'method': 'legacy_xml', 'model': None}
    assert registry.lookups == []
    with pytest.raises(BuildRequestError) as caught:
        route.request('session', '不能在离线路径调用模型', timeout=5)
    assert caught.value.code == 'model_not_available'


def test_configured_runtime_selects_model_path_without_readiness_request(isolated_platform):
    runtime = ScriptedRuntime()
    cfg = model_config()
    before = deepcopy(cfg)
    route = resolve(config=cfg, registry=RuntimeRegistry(openai=runtime))
    public = route.public()
    assert public['method'] == 'llm_tbox_abox'
    assert public['model']['provider'] == 'openai'
    assert public['model']['model'] == TEST_MODEL
    assert public['model']['configuration_id']
    assert TEST_KEY not in json.dumps(public) and TEST_BASE not in json.dumps(public)
    assert cfg == before and runtime.calls == []
    public['model']['model'] = 'mutated-by-client'
    assert route.public()['model']['model'] == TEST_MODEL


@pytest.mark.parametrize('case', [
    'base_only', 'key_only', 'model_only', 'bad_scheme', 'credential_url', 'query_url',
    'bad_timeout', 'unregistered', 'stale_registration', 'stale_endpoint', 'unsupported', 'bad_driver',
])
def test_invalid_or_stale_config_does_not_select_legacy(isolated_platform, case):
    cfg = model_config()
    runtime = ScriptedRuntime()
    registry = RuntimeRegistry(openai=runtime)
    if case in ('base_only', 'key_only', 'model_only'):
        field = case.removesuffix('_only')
        cfg['llm'] = {field: cfg['llm'][field]}
    elif case == 'bad_scheme':
        cfg['llm']['base'] = 'file:///tmp/model'
    elif case == 'credential_url':
        cfg['llm']['base'] = 'https://user:password@model.invalid/v1'
    elif case == 'query_url':
        cfg['llm']['base'] += '?secret=' + TEST_KEY
    elif case == 'bad_timeout':
        cfg['llm']['timeout'] = 'not-a-number'
    elif case == 'unregistered':
        registry = RuntimeRegistry()
    elif case == 'stale_registration':
        cfg = {}
    elif case == 'stale_endpoint':
        runtime.base = 'https://old-model.invalid/v1'
    elif case == 'unsupported':
        cfg = {'driver': 'hermes'}
        registry = RuntimeRegistry(hermes=SimpleNamespace(model=TEST_MODEL))
    else:
        cfg['driver'] = 'misspelled-driver'
    with pytest.raises(BuildRequestError) as caught:
        resolve(config=cfg, registry=registry)
    assert caught.value.code == 'configuration_error'
    assert TEST_KEY not in str(caught.value)
    assert runtime.calls == []


def test_runtime_priority_and_configuration_precedence(isolated_platform):
    saved = model_config(base='https://saved.invalid/v1', model='saved-model', key='saved-test-key')
    env = {'DATAMIND_LLM_BASE': TEST_BASE, 'DATAMIND_LLM_MODEL': TEST_MODEL,
           'DATAMIND_LLM_KEY': TEST_KEY, 'CLAW_DRIVER': 'openai'}
    primary, alternate = ScriptedRuntime(), ScriptedRuntime(name='hermes', model='alternate-model')
    registry = RuntimeRegistry(hermes=alternate, openai=primary)
    original = deepcopy((saved, env))
    public = resolve(config=saved, environ=env, registry=registry).public()
    assert public['model']['provider'] == 'openai' and public['model']['model'] == TEST_MODEL
    env['CLAW_DRIVER'] = 'hermes'
    other = resolve(config=saved, environ=env, registry=registry).public()
    assert other['model']['provider'] == 'hermes' and other['model']['model'] == 'alternate-model'
    assert saved == original[0] and {**env, 'CLAW_DRIVER': 'openai'} == original[1]
    assert primary.calls == alternate.calls == []


def test_default_resolver_reads_platform_config_without_new_agent_variables(isolated_platform):
    runtime = ScriptedRuntime()
    isolated_platform(model_config(), RuntimeRegistry(openai=runtime))
    public = routing_api().resolve_route().public()
    assert public['method'] == 'llm_tbox_abox'
    assert public['model']['model'] == TEST_MODEL
    assert runtime.calls == []


def test_route_identity_is_stable_without_embedding_credentials(isolated_platform):
    def identity(base=TEST_BASE, model=TEST_MODEL, key=TEST_KEY):
        return resolve(config=model_config(base=base, model=model, key=key),
                       registry=RuntimeRegistry(openai=ScriptedRuntime(base=base, model=model, key=key))).public()
    first = identity()
    assert identity() == first
    assert identity(key='rotated-synthetic-key') == first
    assert identity(model='another-model')['model']['configuration_id'] != first['model']['configuration_id']
    assert identity(base='https://other.invalid/v1')['model']['configuration_id'] != first['model']['configuration_id']
    assert TEST_KEY not in json.dumps(first) and TEST_BASE not in json.dumps(first)


@pytest.mark.parametrize('failure', ['timeout', 'connection', 'auth', 'rate_limit', 'empty', 'exception'])
def test_model_failure_never_tries_another_runtime_or_legacy(isolated_platform, failure):
    reply = {
        'timeout': TimeoutError('secret ' + TEST_KEY),
        'connection': OSError('secret ' + TEST_KEY),
        'auth': (False, 'HTTP 401 ' + TEST_KEY),
        'rate_limit': (False, 'HTTP 429 ' + TEST_KEY),
        'empty': (True, '   '),
        'exception': RuntimeError('internal ' + TEST_KEY),
    }[failure]
    primary = ScriptedRuntime(reply)
    alternate = ScriptedRuntime((True, '{"unwanted":true}'), name='hermes')
    route = resolve(config=model_config(), registry=RuntimeRegistry(openai=primary, hermes=alternate))
    before = route.public()
    with pytest.raises(BuildRequestError) as caught:
        route.request('design-1', 'synthetic design requirement', timeout=7)
    assert caught.value.code == 'model_request_failed'
    assert TEST_KEY not in str(caught.value)
    assert len(primary.calls) == 1 and alternate.calls == []
    assert primary.calls[0]['session_id'] == 'design-1' and primary.calls[0]['timeout'] == 7
    assert route.public() == before


def test_successful_model_request_returns_raw_text_without_writing_or_format_repair(isolated_platform):
    raw = '{"reply":"synthetic proposal; semantic validation belongs to T04"}'
    runtime = ScriptedRuntime((True, raw))
    route = resolve(config=model_config(), registry=RuntimeRegistry(openai=runtime))
    assert route.request('design-1', 'purpose', timeout=9) == raw
    assert len(runtime.calls) == 1


@pytest.mark.parametrize('change', ['removed', 'model', 'endpoint', 'method'])
def test_pinned_route_cannot_change_method_or_model(isolated_platform, change):
    first = resolve(config=model_config(), registry=RuntimeRegistry(openai=ScriptedRuntime())).public()
    previous = deepcopy(first)
    if change == 'removed':
        current = resolve().public()
    elif change == 'model':
        current = resolve(config=model_config(model='replacement'),
                          registry=RuntimeRegistry(openai=ScriptedRuntime(model='replacement'))).public()
    elif change == 'endpoint':
        current = resolve(config=model_config(base='https://replacement.invalid/v1'),
                          registry=RuntimeRegistry(openai=ScriptedRuntime(base='https://replacement.invalid/v1'))).public()
    else:
        first, current = resolve().public(), first
        previous = deepcopy(first)
    with pytest.raises(BuildRequestError) as caught:
        routing_api().require_same_route(first, current)
    assert caught.value.code == 'configuration_changed'
    assert first == previous


def test_restored_same_route_can_retry_without_mutating_snapshot(isolated_platform):
    route = resolve(config=model_config(), registry=RuntimeRegistry(openai=ScriptedRuntime())).public()
    saved = deepcopy(route)
    routing_api().require_same_route(saved, route)
    assert saved == route

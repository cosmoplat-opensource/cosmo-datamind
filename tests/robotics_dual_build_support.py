"""Synthetic runtime boundary and isolated platform state — DR-070 / IR-024 T02.

Only model configuration/registry/transport are replaced. XML, HTTP guards,
asset storage and the legacy RDF pipeline remain production implementations.
"""
from copy import deepcopy
import importlib
import socket

import pytest

TEST_KEY = 'ir024-synthetic-secret-never-real'
TEST_BASE = 'https://model.invalid/v1'
TEST_MODEL = 'ir024-test-model'


def routing_api():
    # Missing production behavior fails a collected case; never importorskip/xfail.
    return importlib.import_module('domain_runs.robotics_build_routing')


def model_config(**changes):
    return {'driver': 'openai', 'llm': {
        'base': TEST_BASE, 'model': TEST_MODEL, 'key': TEST_KEY, **changes}}


class ScriptedRuntime:
    """A fake external runtime, not a fake TBox/ABox implementation."""

    def __init__(self, *responses, name='openai', base=TEST_BASE, model=TEST_MODEL, key=TEST_KEY):
        self.name, self.base, self.model, self.key = name, base, model, key
        self.responses = list(responses)
        self.calls = []

    def available(self):
        return True

    def run_turn(self, session_id, message, timeout=300, model=None):
        self.calls.append({'session_id': session_id, 'message': message, 'timeout': timeout, 'model': model})
        assert self.responses, 'Unexpected model request (including readiness probes or retries)'
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return deepcopy(reply)


class RuntimeRegistry:
    def __init__(self, **runtimes):
        self.runtimes = runtimes
        self.lookups = []

    def available(self):
        return list(self.runtimes)

    def get_runtime(self, name):
        self.lookups.append(name)
        return self.runtimes[name]


@pytest.fixture
def isolated_platform(monkeypatch):
    # server initializes the platform registry, but conftest has already isolated
    # its workdir, engine and credentials. Do not inspect the user's live config.
    import server  # noqa: F401
    import agent_runtime
    import srv_engine

    for key in tuple(__import__('os').environ):
        if key.startswith(('DATAMIND_LLM_', 'TBOX_LLM_', 'ABOX_LLM_')) or key == 'CLAW_DRIVER':
            monkeypatch.delenv(key, raising=False)

    def deny_network(*args, **kwargs):
        pytest.fail('IR-024 T02 must not open network connections')

    monkeypatch.setattr(socket.socket, 'connect', deny_network)
    monkeypatch.setattr(socket, 'create_connection', deny_network)

    def install(config=None, registry=None):
        selected = RuntimeRegistry() if registry is None else registry
        saved = deepcopy({} if config is None else config)
        monkeypatch.setattr(srv_engine, '_load_engine_cfg', lambda: deepcopy(saved))
        monkeypatch.setattr(agent_runtime, 'available', selected.available)
        monkeypatch.setattr(agent_runtime, 'get_runtime', selected.get_runtime)
        return selected

    install()
    return install

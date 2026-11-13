"""Scoped TBox thinking policy without changing other applications — DR-070."""
import io
import json
from copy import deepcopy

import pytest

from domain_runs.build_service import BuildRequestError
from domain_runs.robotics_build_routing import BuildRoute
from openai_runtime import OpenAICompatRuntime
from tests.robotics_design_support import Harness, proposals
from tests.robotics_dual_build_support import TEST_BASE, TEST_KEY, model_config

POLICY = 'DATAMIND_ROBOTICS_DESIGN_THINKING'
GLOBAL = {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high', 'seed': 7}


class Response(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def transport(monkeypatch, model='deepseek-flash', replies=None):
    monkeypatch.setenv('DATAMIND_LLM_BASE', TEST_BASE)
    monkeypatch.setenv('DATAMIND_LLM_KEY', TEST_KEY)
    monkeypatch.setenv('DATAMIND_LLM_MODEL', model)
    monkeypatch.setenv('DATAMIND_LLM_MAX_TOKENS', '16384')
    monkeypatch.setenv('DATAMIND_LLM_EXTRA_BODY', json.dumps(GLOBAL))
    monkeypatch.delenv(POLICY, raising=False)
    pending = list(replies) if replies is not None else ['valid body'] * 8
    calls = []

    def request(req, timeout):
        calls.append({'body': json.loads(req.data), 'timeout': timeout})
        assert pending, 'Unexpected additional model request'
        return Response(json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': pending.pop(0)}}]}).encode())

    monkeypatch.setattr('openai_runtime.urllib.request.urlopen', request)
    return OpenAICompatRuntime(), calls


def route(runtime):
    return BuildRoute(runtime, provider='openai', model=runtime.model,
        identity={'provider': 'openai', 'model': runtime.model})


@pytest.mark.parametrize('mode', [None, 'disabled', 'low', 'inherit'])
def test_tbox_policy_overrides_only_thinking_fields(monkeypatch, mode):
    rt, calls = transport(monkeypatch)
    if mode is not None:
        monkeypatch.setenv(POLICY, mode)
    assert route(rt).request('design-test', 'design requirements', timeout=9) == 'valid body'
    body = calls[0]['body']
    if mode == 'inherit':
        assert body['thinking'] == GLOBAL['thinking']
        assert body['reasoning_effort'] == 'high'
    elif mode == 'low':
        assert body['thinking'] == {'type': 'enabled'}
        assert body['reasoning_effort'] == 'low'
    else:
        assert body['thinking'] == {'type': 'disabled'}
        assert 'reasoning_effort' not in body
    assert body['seed'] == 7 and body['max_tokens'] == 16384
    assert body['model'] == rt.model and calls[0]['timeout'] == 9
    assert body['messages'] == [{'role': 'user', 'content': 'design requirements'}]


@pytest.mark.parametrize('model', ['deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-pro'])
def test_supported_deepseek_models_disable_thinking_for_tbox(monkeypatch, model):
    rt, calls = transport(monkeypatch, model=model)
    route(rt).request('design-test', 'requirements', timeout=3)
    assert calls[0]['body']['thinking'] == {'type': 'disabled'}


@pytest.mark.parametrize('model', ['gpt-4o-mini', 'qwen3', 'deepseek-reasoner'])
def test_unverified_model_protocols_are_not_given_deepseek_overrides(monkeypatch, model):
    rt, calls = transport(monkeypatch, model=model)
    route(rt).request('design-test', 'requirements', timeout=3)
    for key, value in GLOBAL.items():
        assert calls[0]['body'][key] == value


def test_tbox_call_does_not_mutate_general_or_streaming_calls(monkeypatch):
    rt, calls = transport(monkeypatch)
    assert rt.run_turn('task-before', 'task', timeout=3)[0]
    before = deepcopy(calls[0]['body'])
    route(rt).request('design-test', 'requirements', timeout=3)
    assert calls[1]['body']['thinking'] == {'type': 'disabled'}
    assert rt.run_turn('task-after', 'task', timeout=3)[0]
    assert calls[2]['body'] == before
    assert rt.run_turn_stream('task-stream', 'task', timeout=3)[0]
    assert calls[3]['body'] == {**before, 'stream': True}
    assert json.loads(__import__('os').environ['DATAMIND_LLM_EXTRA_BODY']) == GLOBAL


def test_invalid_policy_fails_before_http_without_fallback(monkeypatch):
    rt, calls = transport(monkeypatch)
    monkeypatch.setenv(POLICY, 'typo-not-a-valid-policy')
    with pytest.raises(BuildRequestError) as error:
        route(rt).request('design-test', 'requirements', timeout=3)
    assert error.value.code == 'configuration_error'
    assert calls == []


def test_real_design_pipeline_scopes_policy_to_both_tbox_stages(monkeypatch, tmp_path):
    replies = [json.dumps(p, ensure_ascii=False) for p in proposals()]
    rt, calls = transport(monkeypatch, replies=replies)
    h = Harness(tmp_path)
    h.runtime = rt
    h.config = model_config(model=rt.model)
    original = h.submit(description='整理技能及调用参数')
    assert h.run()['status'] == 'completed'
    classes = h.get(original)
    confirmed = h.service.confirm_classes(classes['session_id'], request_id='confirm-classes',
        expected_revision=classes['revision'], snapshot_sha256=classes['snapshot_sha256'])
    h.message(confirmed)
    assert h.run()['status'] == 'completed'
    properties = h.get(original)
    assert properties['stage'] == 'properties'
    assert properties['result']['object_properties']
    assert properties['class_confirmation'] == confirmed['class_confirmation']
    assert properties['result']['classes'] == confirmed['result']['classes']
    assert len(calls) == 2
    assert all(c['body']['thinking'] == {'type': 'disabled'} for c in calls)
    assert '"stage":"classes"' in calls[0]['body']['messages'][0]['content']
    assert '"stage":"properties"' in calls[1]['body']['messages'][0]['content']

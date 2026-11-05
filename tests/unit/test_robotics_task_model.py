"""IR-020 T02: real parser contract with a scripted model transport — DR-062 / DR-066.

Only the external model is replaced; these tests do not claim live model quality.
Imports are inside tests so absent production modules yield failures, not collection errors.
"""
from copy import deepcopy
import importlib
import json

import pytest


def api():
    return importlib.import_module('domains.robotics.task_model')


def task(**changes):
    return {'operation': 'pick_place', 'part': '零件A', 'station': '工位B',
            'carrier': None, 'slot': None, 'unsupported_requests': [], **changes}


def envelope(value=None, *, content=None, finish='stop', refusal=None):
    return {'choices': [{'finish_reason': finish, 'message': {
        'role': 'assistant', 'content': json.dumps(value if value is not None else task(), ensure_ascii=False)
        if content is None else content, 'refusal': refusal}}]}


class ScriptedTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **request):
        self.calls.append(deepcopy(request))
        assert self.responses, 'Unexpected extra model request'
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def client(transport, **changes):
    options = {'base_url': 'https://model.invalid/v1/', 'model': 'test-model',
               'api_key': 'test-secret-only', 'timeout': 17, 'transport': transport, **changes}
    return api().ModelClient(**options)


def test_parser_sends_role_messages_json_mode_and_does_not_mutate_inputs():
    messages = [{'role': 'user', 'content': '把零件A放到工位B'},
                {'role': 'assistant', 'content': '请选择放置位'},
                {'role': 'user', 'content': '选择槽位1'}]
    catalog = [{'kind': 'PlacementSlot', 'id': 'urn:factory:slot1', 'label': '槽位1', 'aliases': []}]
    before = deepcopy((messages, catalog))
    expected = task(slot='槽位1')
    transport = ScriptedTransport(envelope(expected))
    result = client(transport).parse(messages, catalog)
    assert result == expected
    assert (messages, catalog) == before
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request['url'] == 'https://model.invalid/v1/chat/completions'
    assert request['headers']['Authorization'] == 'Bearer test-secret-only'
    assert request['timeout'] == 17
    payload = request['payload']
    assert payload['model'] == 'test-model'
    assert payload['stream'] is False
    assert payload['response_format'] == {'type': 'json_object'}
    assert payload['messages'][0]['role'] == 'system'
    assert payload['messages'][1:] == messages
    assert '槽位1' in payload['messages'][0]['content']
    assert 'test-secret-only' not in json.dumps(payload)


@pytest.mark.parametrize('changes', [
    {'operation': None, 'part': None, 'station': None},
    {'operation': 'unsupported', 'unsupported_requests': ['批量处理多个零件']},
    {'unsupported_requests': ['修改速度', '现在执行机器人']},
])
def test_valid_task_preserves_missing_fields_and_unsupported_requests(changes):
    expected = task(**changes)
    before = deepcopy(expected)
    assert api().validate_task(expected) == before
    transport = ScriptedTransport(envelope(expected))
    assert client(transport).parse([{'role': 'user', 'content': '描述任务'}], []) == before
    assert expected == before


@pytest.mark.parametrize('case', [
    'missing_field', 'extra_xml', 'not_object', 'unknown_operation', 'empty_name',
    'numeric_name', 'long_name', 'restrictions_string', 'empty_restriction', 'too_many_restrictions',
])
def test_six_field_contract_rejects_invalid_tasks(case):
    value = task()
    if case == 'missing_field':
        value.pop('slot')
    elif case == 'extra_xml':
        value['xml'] = '<root/>'
    elif case == 'not_object':
        value = [value]
    elif case == 'unknown_operation':
        value['operation'] = 'weld'
    elif case == 'empty_name':
        value['part'] = '  '
    elif case == 'numeric_name':
        value['slot'] = 123
    elif case == 'long_name':
        value['station'] = 'x' * 201
    elif case == 'restrictions_string':
        value['unsupported_requests'] = '改速度'
    elif case == 'empty_restriction':
        value['unsupported_requests'] = ['']
    else:
        value['unsupported_requests'] = ['修改'] * 21
    module = api()
    with pytest.raises(module.ModelError) as caught:
        module.validate_task(value)
    assert caught.value.code == 'model_format'


def test_invalid_json_gets_one_format_repair_without_losing_conversation():
    messages = [{'role': 'user', 'content': '把零件A放到工位B'}]
    transport = ScriptedTransport(envelope(content='not json'), envelope(task(slot=None)))
    assert client(transport).parse(messages, []) == task(slot=None)
    assert len(transport.calls) == 2
    first = transport.calls[0]['payload']['messages']
    retry = transport.calls[1]['payload']['messages']
    assert retry[:len(first)] == first
    assert retry[-2] == {'role': 'assistant', 'content': 'not json'}
    assert retry[-1]['role'] == 'user'
    assert 'JSON' in retry[-1]['content']
    assert messages == [{'role': 'user', 'content': '把零件A放到工位B'}]


def test_two_bad_outputs_stop_without_default_task_or_xml():
    transport = ScriptedTransport(envelope(content='<root/>'), envelope({'operation': 'pick_place'}))
    module = api()
    with pytest.raises(module.ModelError) as caught:
        client(transport).parse([{'role': 'user', 'content': '生成任务'}], [])
    assert caught.value.code == 'model_format'
    assert len(transport.calls) == 2


@pytest.mark.parametrize('response, code', [
    (envelope(refusal='拒绝'), 'model_refusal'),
    (envelope(finish='content_filter'), 'model_refusal'),
    (envelope(finish='length'), 'model_truncated'),
    (envelope(finish='tool_calls'), 'model_response'),
    ({'choices': []}, 'model_response'),
    ({'choices': [{'finish_reason': 'stop', 'message': {'content': None}}]}, 'model_response'),
])
def test_bad_envelope_is_not_repaired_as_task_json(response, code):
    transport = ScriptedTransport(response)
    module = api()
    with pytest.raises(module.ModelError) as caught:
        client(transport).parse([{'role': 'user', 'content': '生成任务'}], [])
    assert caught.value.code == code
    assert len(transport.calls) == 1


@pytest.mark.parametrize('failure, code', [
    (TimeoutError('test-secret-only timeout'), 'model_timeout'),
    (OSError('test-secret-only connection failed'), 'model_connection'),
])
def test_transport_failure_is_explicit_and_does_not_expose_credentials(failure, code):
    transport = ScriptedTransport(failure)
    module = api()
    with pytest.raises(module.ModelError) as caught:
        client(transport).parse([{'role': 'user', 'content': '生成任务'}], [])
    assert caught.value.code == code
    assert 'test-secret-only' not in str(caught.value)
    assert len(transport.calls) == 1


@pytest.mark.parametrize('missing', ['base_url', 'model', 'api_key'])
def test_missing_configuration_does_not_call_model_or_invent_task(missing):
    transport = ScriptedTransport()
    parser = client(transport, **{missing: ''})
    status = parser.status()
    assert status['configured'] is False and status['missing']
    assert 'test-secret-only' not in json.dumps(status)
    with pytest.raises(api().ModelError) as caught:
        parser.parse([{'role': 'user', 'content': '把A放到B'}], [])
    assert caught.value.code == 'model_not_configured'
    assert transport.calls == []


@pytest.mark.parametrize('override', ['', 'extract-special'])
def test_datamind_config_reuse_environment_precedence_and_optional_task_model(override):
    config = {'llm': {'base': 'https://saved.invalid/v1', 'key': 'saved-secret', 'model': 'saved-model'},
              'task_models': {'bt_extract': override}}
    environ = {'DATAMIND_LLM_BASE': 'https://env.invalid/v1', 'DATAMIND_LLM_KEY': 'env-secret',
               'DATAMIND_LLM_MODEL': 'env-model', 'DATAMIND_LLM_TIMEOUT': '19'}
    before = deepcopy((config, environ))
    transport = ScriptedTransport(envelope())
    parser = api().ModelClient.from_datamind(config=config, environ=environ, transport=transport)
    assert parser.status()['configured'] is True
    assert parser.parse([{'role': 'user', 'content': '任务'}], []) == task()
    request = transport.calls[0]
    assert request['url'] == 'https://env.invalid/v1/chat/completions'
    assert request['headers']['Authorization'] == 'Bearer env-secret'
    assert request['payload']['model'] == (override or 'env-model')
    assert request['timeout'] == 19
    assert (config, environ) == before
    assert 'env-secret' not in json.dumps(parser.status())


def test_saved_datamind_config_works_without_new_bt_environment_variables():
    config = {'llm': {'base': 'https://saved.invalid/v1', 'key': 'saved-secret', 'model': 'saved-model'}}
    transport = ScriptedTransport(envelope())
    parser = api().ModelClient.from_datamind(config=config, environ={}, transport=transport)
    assert parser.status()['configured'] is True
    parser.parse([{'role': 'user', 'content': '任务'}], [])
    request = transport.calls[0]
    assert request['url'] == 'https://saved.invalid/v1/chat/completions'
    assert request['payload']['model'] == 'saved-model'
    assert request['headers']['Authorization'] == 'Bearer saved-secret'

"""T04 controls: exercise default urllib transport against loopback HTTP — DR-062 / DR-066."""
import json
import threading

import pytest

from domains.robotics.task_model import ModelClient, ModelError
from tests.robotics_governance_support import TASK
from tests.robotics_task_support import TEXT, model_server


def test_default_transport_sends_actual_json_request_and_bearer_header():
    with model_server({}) as (url, calls):
        client = ModelClient(url, 'test-http-model', 'local-test-key')
        assert client.parse([{'role': 'user', 'content': TEXT}], []) == TASK
        assert len(calls) == 1 and calls[0]['path'] == '/v1/chat/completions'
        assert calls[0]['headers']['Authorization'] == 'Bearer local-test-key'
        assert calls[0]['payload']['response_format'] == {'type': 'json_object'}
        assert calls[0]['payload']['model'] == 'test-http-model'


@pytest.mark.parametrize('reply,code', [
    ({'status': 401, 'body': b'local-test-key'}, 'model_auth'),
    ({'status': 429}, 'model_rate_limit'), ({'body': b'not JSON'}, 'model_response'),
    ({'body': b'x' * 1_000_001}, 'model_response')])
def test_default_transport_bounds_and_classifies_real_http_failures(reply, code):
    with model_server(reply) as (url, calls):
        with pytest.raises(ModelError) as caught:
            ModelClient(url, 'test-model', 'local-test-key').parse([{'role': 'user', 'content': TEXT}], [])
        assert caught.value.code == code and 'local-test-key' not in str(caught.value)
        assert len(calls) == 1


def test_redirect_never_forwards_credentials_to_other_endpoint():
    with model_server({}) as (other, downstream):
        with model_server({'status': 307, 'headers': {'Location': other + '/chat/completions'}}) as (url, calls):
            with pytest.raises(ModelError) as caught:
                ModelClient(url, 'test-model', 'local-test-key').parse([{'role': 'user', 'content': TEXT}], [])
            assert caught.value.code == 'model_http'
            assert len(calls) == 1 and downstream == []


def test_default_transport_timeout_and_closed_connection_are_explicit():
    release = threading.Event()
    with model_server({'release': release}) as (url, calls):
        with pytest.raises(ModelError) as caught:
            ModelClient(url, 'test-model', 'local-test-key', timeout=.05).parse(
                [{'role': 'user', 'content': TEXT}], [])
        assert caught.value.code == 'model_timeout' and len(calls) == 1
        release.set()
    with pytest.raises(ModelError) as caught:
        ModelClient(url, 'test-model', 'local-test-key', timeout=1).parse([{'role': 'user', 'content': TEXT}], [])
    assert caught.value.code == 'model_connection'
    assert 'local-test-key' not in json.dumps({'error': str(caught.value)})

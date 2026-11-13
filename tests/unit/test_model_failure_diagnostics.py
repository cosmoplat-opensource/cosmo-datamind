"""Safe, per-call LLM failure evidence and persistent design errors — DR-029 / DR-070."""
import io
import json
import urllib.error
from email.message import Message

import pytest

from openai_runtime import OpenAICompatRuntime
from domain_runs.robotics_build_routing import BuildRoute
from domain_runs.build_service import BuildRequestError
from tests.robotics_design_support import Harness
from tests.build_pipeline_support import db_rows, blob_bytes

SECRET = 'synthetic-private-key-MUST-NOT-APPEAR'


class Response(io.BytesIO):
    status = 200
    def __enter__(self):return self
    def __exit__(self,*args):return False


def runtime(monkeypatch, body=None, exc=None):
    monkeypatch.setenv('DATAMIND_LLM_BASE','https://private-endpoint.invalid/v1')
    monkeypatch.setenv('DATAMIND_LLM_KEY',SECRET)
    monkeypatch.setenv('DATAMIND_LLM_MODEL','synthetic-model')
    monkeypatch.setenv('DATAMIND_LLM_MAX_TOKENS','16384')
    def request(*args,**kwargs):
        if exc is not None:raise exc
        return Response(json.dumps(body).encode())
    monkeypatch.setattr('openai_runtime.urllib.request.urlopen',request)
    return OpenAICompatRuntime()


def test_empty_reasoning_response_retains_budget_evidence_without_reasoning_text(monkeypatch):
    rt=runtime(monkeypatch,{'choices':[{'finish_reason':'length','message':{'content':'','reasoning_content':SECRET}}],
        'usage':{'prompt_tokens':10000,'completion_tokens':16384,'completion_tokens_details':{'reasoning_tokens':16384}}})
    ok,text,diag=rt.run_turn_detailed('s','private user prompt',timeout=3)
    assert ok is False and SECRET not in text
    assert diag['code']=='model_empty_content' and diag['http_status']==200 and diag['finish_reason']=='length'
    assert diag['reasoning_tokens']==diag['completion_tokens']==diag['max_tokens']==16384
    assert diag['content_chars']==0 and diag['reasoning_chars']==len(SECRET)
    assert SECRET not in json.dumps(diag) and 'private user prompt' not in json.dumps(diag)


@pytest.mark.parametrize('exception,code,status',[
    (TimeoutError(SECRET),'model_timeout',None),
    (urllib.error.URLError(OSError(SECRET)),'model_connection_failed',None),
    (urllib.error.HTTPError('https://private-endpoint.invalid',429,SECRET,Message(),io.BytesIO(SECRET.encode())), 'model_http_error',429),
])
def test_transport_failures_preserve_safe_category_not_provider_text(monkeypatch,exception,code,status):
    rt=runtime(monkeypatch,exc=exception)
    ok,text,diag=rt.run_turn_detailed('s','private prompt',timeout=3)
    assert not ok and diag['code']==code and diag.get('http_status')==status
    assert SECRET not in text+json.dumps(diag) and 'private-endpoint' not in text+json.dumps(diag)


@pytest.mark.parametrize('body',[{}, {'choices':[{'message':{'content':['bad',SECRET]},'finish_reason':SECRET}]}])
def test_malformed_response_is_classified_and_private_fields_dropped(monkeypatch,body):
    rt=runtime(monkeypatch,body)
    ok,text,diag=rt.run_turn_detailed('s','prompt',timeout=3)
    assert not ok and diag['code']=='model_invalid_response'
    assert SECRET not in text+json.dumps(diag)


def test_legacy_two_tuple_call_is_preserved(monkeypatch):
    rt=runtime(monkeypatch,{'choices':[{'finish_reason':'stop','message':{'content':'valid body'}}]})
    assert rt.run_turn('s','prompt',timeout=3)==(True,'valid body')


def test_route_and_actual_design_failure_keep_safe_details_and_original_revision(monkeypatch,tmp_path):
    rt=runtime(monkeypatch,{'choices':[{'finish_reason':'length','message':{'content':'','reasoning_content':SECRET}}],
        'usage':{'completion_tokens':16384,'completion_tokens_details':{'reasoning_tokens':16384}}})
    route=BuildRoute(rt,provider='openai',model='synthetic-model',identity={'model':'synthetic-model'})
    with pytest.raises(BuildRequestError) as caught:route.request('s','prompt',timeout=3)
    assert caught.value.code=='model_request_failed' and caught.value.issues[0]['finish_reason']=='length'
    assert '空正文' in str(caught.value) and SECRET not in str(caught.value)
    h=Harness(tmp_path);state=h.classes()
    # Keep the original model identity; only real HTTP transport is scripted.
    h.runtime.run_turn=rt.run_turn
    h.runtime.run_turn_detailed=rt.run_turn_detailed
    h.message(state);run=h.run();current=h.get(state)
    assert run['status']=='failed' and current['revision']==state['revision']
    assert current['class_confirmation']==state['class_confirmation'] and current['result']==state['result']
    assert current['error']['code']=='model_request_failed'
    assert current['error']['issues'][0]['reasoning_tokens']==16384
    assert '空正文' in current['error']['message']
    assert SECRET not in json.dumps(db_rows(tmp_path))+str(blob_bytes(tmp_path))

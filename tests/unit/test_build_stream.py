# -*- coding: utf-8 -*-
"""本体构建「过程输出」：流式有界执行器与 OpenAI 兼容驱动的流式解析。"""
import io
import json
import time

import server
import openai_runtime


def test_bounded_stream_relays_logs_then_done():
    def work(log):
        log("a"); log("b")
        return {"objects": [1]}
    events = list(server._bounded_stream(work, secs=5, tick=0.05))
    logs = [e[1] for e in events if e[0] == "log"]
    assert logs == ["a", "b"]
    assert events[-1][0] == "done"
    _, value, err, timed_out = events[-1]
    assert value == {"objects": [1]} and err is None and timed_out is False


def test_bounded_stream_ticks_while_waiting():
    def work(log):
        time.sleep(0.35)
        return 1
    events = list(server._bounded_stream(work, secs=5, tick=0.1))
    assert any(e[0] == "tick" for e in events), "等待期间应有心跳"
    assert events[-1] == ("done", 1, None, False)


def test_bounded_stream_reports_exception_not_timeout():
    def work(log):
        raise ValueError("boom")
    events = list(server._bounded_stream(work, secs=5, tick=0.05))
    _, value, err, timed_out = events[-1]
    assert value is None and isinstance(err, ValueError) and timed_out is False


def test_bounded_stream_timeout_gives_up():
    def work(log):
        time.sleep(2)
        return 1
    events = list(server._bounded_stream(work, secs=0.3, tick=0.05))
    assert events[-1][0] == "done" and events[-1][3] is True


class _FakeResp(io.BytesIO):
    def __init__(self, lines, ctype):
        super().__init__(b"".join(lines))
        self.headers = {"Content-Type": ctype}
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _rt(monkeypatch, resp):
    monkeypatch.setenv("DATAMIND_LLM_BASE", "http://x")
    monkeypatch.setenv("DATAMIND_LLM_KEY", "k")
    rt = openai_runtime.OpenAICompatRuntime()
    monkeypatch.setattr(openai_runtime.urllib.request, "urlopen", lambda req, timeout=0: resp)
    return rt


def test_run_turn_stream_concatenates_deltas_and_calls_on_delta(monkeypatch):
    lines = [b'data: ' + json.dumps({"choices": [{"delta": {"content": c}}]}).encode() + b"\n"
             for c in ('{"objects"', ':[', ']}')]
    lines.append(b"data: [DONE]\n")
    rt = _rt(monkeypatch, _FakeResp(lines, "text/event-stream"))
    seen = []
    ok, text = rt.run_turn_stream("s", "m", timeout=5, on_delta=seen.append)
    assert ok and text == '{"objects":[]}'
    assert "".join(seen) == text


def test_run_turn_stream_falls_back_to_plain_json(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()
    rt = _rt(monkeypatch, _FakeResp([body], "application/json"))
    ok, text = rt.run_turn_stream("s", "m", timeout=5)
    assert ok and text == "hi"


def test_run_turn_stream_empty_content_is_failure(monkeypatch):
    lines = [b'data: ' + json.dumps({"choices": [{"delta": {"reasoning_content": "thinking"}}]}).encode() + b"\n",
             b"data: [DONE]\n"]
    rt = _rt(monkeypatch, _FakeResp(lines, "text/event-stream"))
    ok, text = rt.run_turn_stream("s", "m", timeout=5)
    assert ok is False and "空 content" in text


def test_run_turn_stream_relays_reasoning_separately(monkeypatch):
    lines = [b'data: ' + json.dumps({"choices": [{"delta": {"reasoning_content": "think"}}]}).encode() + b"\n",
             b'data: ' + json.dumps({"choices": [{"delta": {"content": "ok"}}]}).encode() + b"\n",
             b"data: [DONE]\n"]
    rt = _rt(monkeypatch, _FakeResp(lines, "text/event-stream"))
    seen, thought = [], []
    ok, text = rt.run_turn_stream("s", "m", timeout=5, on_delta=seen.append, on_reasoning=thought.append)
    assert ok and text == "ok" and seen == ["ok"] and thought == ["think"]


def test_extra_body_env_merges_into_request(monkeypatch):
    captured = {}
    def fake_urlopen(req, timeout=0):
        captured["body"] = json.loads(req.data)
        return _FakeResp([json.dumps({"choices": [{"message": {"content": "x"}}]}).encode()],
                         "application/json")
    monkeypatch.setenv("DATAMIND_LLM_BASE", "http://x"); monkeypatch.setenv("DATAMIND_LLM_KEY", "k")
    monkeypatch.setenv("DATAMIND_LLM_EXTRA_BODY", '{"thinking":{"type":"disabled"}}')
    monkeypatch.setattr(openai_runtime.urllib.request, "urlopen", fake_urlopen)
    ok, _ = openai_runtime.OpenAICompatRuntime().run_turn("s", "m", timeout=5)
    assert ok and captured["body"]["thinking"] == {"type": "disabled"}
    monkeypatch.setenv("DATAMIND_LLM_EXTRA_BODY", "not json")
    assert openai_runtime._extra_body() == {}

# -*- coding: utf-8 -*-
"""端点凭据的边界(DR-096):已保存的 Key 只发往已保存的端点;界面保存的端点与模型随配置更新;
端点清空后驱动不再登记为可用。"""
import json
import sys

import bp_engine
import openai_runtime
import server
import srv_engine

SAVED_BASE = "https://llm.example.test/v1"
SAVED_KEY = "sk-saved-0123456789"


class _Resp:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _capture(monkeypatch, body):
    sent = []

    def fake_urlopen(req, timeout=None):
        sent.append({"url": req.full_url, "auth": req.get_header("Authorization"), "timeout": timeout})
        return _Resp(body)
    monkeypatch.setattr(bp_engine.urllib.request, "urlopen", fake_urlopen)
    return sent


def _saved(monkeypatch):
    monkeypatch.setenv("DATAMIND_LLM_BASE", SAVED_BASE)
    monkeypatch.setenv("DATAMIND_LLM_KEY", SAVED_KEY)
    monkeypatch.setenv("DATAMIND_LLM_MODEL", "m-saved")


def test_models_does_not_send_saved_key_to_another_endpoint(monkeypatch):
    _saved(monkeypatch)
    sent = _capture(monkeypatch, {"data": [{"id": "m1"}]})
    r = server.app.test_client().post("/api/engine/llm/models", json={"base": "https://other.example.test/v1"})
    assert r.status_code == 400 and "Key" in r.get_json()["error"]
    assert sent == []


def test_test_endpoint_does_not_send_saved_key_to_another_endpoint(monkeypatch):
    _saved(monkeypatch)
    sent = _capture(monkeypatch, {"choices": [{"message": {"content": "在线"}}]})
    r = server.app.test_client().post("/api/engine/llm/test",
                                      json={"base": "https://other.example.test/v1", "model": "m"})
    assert r.status_code == 400 and sent == []


def test_saved_key_is_reused_for_the_saved_endpoint(monkeypatch):
    _saved(monkeypatch)
    sent = _capture(monkeypatch, {"data": [{"id": "m1"}]})
    for body in ({}, {"base": SAVED_BASE + "/"}):
        r = server.app.test_client().post("/api/engine/llm/models", json=body)
        assert r.get_json()["models"] == ["m1"]
    assert [s["auth"] for s in sent] == ["Bearer " + SAVED_KEY] * 2
    assert all(s["url"] == SAVED_BASE + "/models" for s in sent)


def test_explicit_key_goes_to_the_endpoint_being_tested(monkeypatch):
    _saved(monkeypatch)
    sent = _capture(monkeypatch, {"choices": [{"message": {"content": "在线"}}]})
    r = server.app.test_client().post("/api/engine/llm/test", json={
        "base": "https://other.example.test/v1", "key": "sk-other-abcdefgh", "model": "m", "timeout": "abc"})
    assert r.get_json()["ok"] is True
    assert sent[0]["auth"] == "Bearer sk-other-abcdefgh" and sent[0]["timeout"] == 60


def test_test_timeout_is_bounded(monkeypatch):
    _saved(monkeypatch)
    sent = _capture(monkeypatch, {"choices": [{"message": {"content": "在线"}}]})
    server.app.test_client().post("/api/engine/llm/test", json={"timeout": 99999})
    assert sent[0]["timeout"] == 300


def _clean_llm_env(monkeypatch):
    for var in srv_engine.LLM_ENV.values():
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(srv_engine, "_ENV_LOCKED_AT_BOOT", set())


def test_reapplying_config_switches_endpoint_and_model_with_the_key(monkeypatch):
    _clean_llm_env(monkeypatch)
    srv_engine._apply_engine_cfg({"llm": {"base": "https://a.example.test/v1", "key": "key-a-12345678", "model": "m1"}})
    srv_engine._apply_engine_cfg({"llm": {"base": "https://b.example.test/v1", "key": "key-b-12345678", "model": "m2"}})
    import os
    assert os.environ["DATAMIND_LLM_BASE"] == "https://b.example.test/v1"
    assert os.environ["DATAMIND_LLM_MODEL"] == "m2"
    assert os.environ["DATAMIND_LLM_KEY"] == "key-b-12345678"


def test_endpoint_injected_at_boot_is_not_overridden_by_saved_config(monkeypatch):
    _clean_llm_env(monkeypatch)
    monkeypatch.setenv("DATAMIND_LLM_BASE", "https://ops.example.test/v1")
    monkeypatch.setattr(srv_engine, "_ENV_LOCKED_AT_BOOT", {"DATAMIND_LLM_BASE"})
    srv_engine._apply_engine_cfg({"llm": {"base": "https://ui.example.test/v1", "key": "key-ui-12345678"}})
    import os
    assert os.environ["DATAMIND_LLM_BASE"] == "https://ops.example.test/v1"


def test_clearing_the_endpoint_unregisters_the_driver(monkeypatch):
    _clean_llm_env(monkeypatch)
    ar = sys.modules["agent_runtime"]
    monkeypatch.setitem(ar._REGISTRY, "openai", ar._REGISTRY.get("openai"))
    monkeypatch.setenv("DATAMIND_LLM_BASE", "https://a.example.test/v1")
    monkeypatch.setenv("DATAMIND_LLM_KEY", "key-a-12345678")
    assert openai_runtime.register(ar) is True and "openai" in ar.available()
    monkeypatch.delenv("DATAMIND_LLM_BASE")
    assert openai_runtime.register(ar) is False
    assert "openai" not in ar.available()

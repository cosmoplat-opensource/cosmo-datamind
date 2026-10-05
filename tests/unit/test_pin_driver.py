"""固定引擎(DR-091):设置 DATAMIND_PIN_DRIVER 后只用该引擎,失败时同一引擎重试,不退到其他模型。"""
from __future__ import annotations

import srv_engine


def test_pinned_driver_is_the_only_one_and_retried(monkeypatch):
    monkeypatch.setenv("DATAMIND_PIN_DRIVER", "openai")
    monkeypatch.setenv("DATAMIND_PIN_RETRIES", "3")
    assert srv_engine._drv_order() == ("openai", "openai", "openai")


def test_without_pin_the_preferred_driver_comes_first_with_fallbacks(monkeypatch):
    monkeypatch.delenv("DATAMIND_PIN_DRIVER", raising=False)
    monkeypatch.setenv("CLAW_DRIVER", "openai")
    order = srv_engine._drv_order()
    assert order[0] == "openai" and len(set(order)) > 1


def test_unknown_pin_falls_back_to_normal_order(monkeypatch):
    monkeypatch.setenv("DATAMIND_PIN_DRIVER", "nope")
    monkeypatch.setenv("CLAW_DRIVER", "openai")
    assert srv_engine._drv_order()[0] == "openai"

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享内核:关系证据的领域值对象(DDD),由模型文件生成(MDD)。

关系状态、状态迁移、有效行规则原先以字符串散落在裁决、复核、验收、问数各处,改一处漏一处。
现在唯一来源是 ``specs/models/relation_lifecycle.json``:本模块导入时读取它,其余模块从这里取值。
改规则先改模型;tests/meta/test_models.py 校验模型自洽,行为测试校验代码符合模型。

只用标准库,不依赖 Web 层(见 specs/models/context_map.json 的 shared_kernel)。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "specs", "models", "relation_lifecycle.json")

with open(MODEL_PATH, encoding="utf-8") as _fh:
    MODEL = json.load(_fh)

STATES: tuple = tuple(MODEL["states"])
INITIAL: str = MODEL["initial"]
VALID_ROWS_RULES: tuple = tuple(MODEL["valid_rows_rules"])
QA_ENFORCED_RULES: frozenset = frozenset(k for k, v in MODEL["valid_rows_rules"].items() if v.get("enforced_in_qa"))
CROSS_CHECK_VERDICTS: tuple = tuple(MODEL["cross_check_verdicts"])


@dataclass(frozen=True)
class Transition:
    """值对象:一次状态迁移由谁、凭什么发生。"""
    source: str
    target: str
    actor: str
    via: str


TRANSITIONS: tuple = tuple(Transition(t["from"], t["to"], t["actor"], t["via"]) for t in MODEL["transitions"])
AUTOMATED_ACTORS = frozenset(t.actor for t in TRANSITIONS if t.actor != "human")


def allowed(source: str, target: str, actor: str) -> bool:
    """这次迁移是否被模型允许(同状态不算迁移)。"""
    if source == target:
        return True
    return any(t.source == source and t.target == target and t.actor == actor for t in TRANSITIONS)


def is_valid_rows(evidence: dict | None) -> bool:
    """证据里写明了模型认可的有效行规则且成立:父键在有效行上唯一。"""
    vr = (evidence or {}).get("valid_rows")
    return isinstance(vr, dict) and vr.get("state") == "support" and vr.get("rule") in VALID_ROWS_RULES


def qa_requirement(rule: str) -> str:
    return MODEL["valid_rows_rules"].get(rule, {}).get("qa_requirement", "")

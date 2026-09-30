#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享内核:关系证据的领域值对象(DDD),由模型文件生成(MDD)。

有效行规则原先在验收(build_quality)与问数(server)两处各写一份规则名。现在两处都从
``specs/models/relation_lifecycle.json`` 取:本模块导入时读取它。复核技能脚本须能独立发布、不能导入本模块,
只能自带常量,由 tests/meta/test_models.py 核对它与模型一致。

只用标准库,不依赖 Web 层。
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
QA_CONDITIONS: dict = {k: tuple(v.get("qa_conditions", ())) for k, v in MODEL["valid_rows_rules"].items()}
# 问数时强制检查的规则(latest 只提示,不强制)
QA_ENFORCED_RULES: frozenset = frozenset(k for k, c in QA_CONDITIONS.items() if set(c) & {"tenant", "group", "live"})
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

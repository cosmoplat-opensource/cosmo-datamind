#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""有效行规则的集中定义(DR-066)。

验收(build_quality)与问数(server)都从 ``specs/models/valid_rows_rules.json`` 读取规则名与
问数条件,本模块在导入时加载该文件。复核技能脚本须能独立发布、不能导入本模块,因此自带规则名,
由 tests/meta/test_models.py 核对两处一致。只用标准库。
"""
from __future__ import annotations

import json
import os

MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "specs", "models", "valid_rows_rules.json")

with open(MODEL_PATH, encoding="utf-8") as _fh:
    MODEL = json.load(_fh)

VALID_ROWS_RULES: tuple = tuple(MODEL["valid_rows_rules"])
QA_CONDITIONS: dict = {k: tuple(v.get("qa_conditions", ())) for k, v in MODEL["valid_rows_rules"].items()}
# 问数时强制检查的规则(latest 只提示,不强制)
QA_ENFORCED_RULES: frozenset = frozenset(k for k, c in QA_CONDITIONS.items() if set(c) & {"tenant", "group", "live"})


def is_valid_rows(evidence: dict | None) -> bool:
    """证据里写明了认可的有效行规则且成立:父键在有效行上唯一。"""
    vr = (evidence or {}).get("valid_rows")
    return isinstance(vr, dict) and vr.get("state") == "support" and vr.get("rule") in VALID_ROWS_RULES

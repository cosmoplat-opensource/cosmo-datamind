# -*- coding: utf-8 -*-
"""OSI 风格导出(DR-055):确定性、状态随行、不依赖 YAML 库。"""
import copy

import osi_export as OE
from tests.unit.test_concept_profile import IR as _BASE

IR = copy.deepcopy(_BASE)
IR["links"][0]["evidence"] = {"child_key": "cust_id", "parent_key": "cust_id", "overlap": 100.0, "parent_unique": True}


def test_yaml_is_deterministic_and_carries_status_and_multiplicity():
    a, b = OE.to_yaml(IR, "g"), OE.to_yaml(IR, "g")
    assert a == b
    assert 'status: "verified"' in a and 'multiplicity: "many_to_one"' in a
    assert 'verbalizes: "销售订单 归属 客户"' in a
    assert 'agg: "sum"' in a and 'sql: "SELECT SUM(' in a
    assert "未经官方 validator 校验" in a


def test_emitter_quotes_and_escapes():
    text = OE.emit({"a": 'x"y', "b": [1, "z\nw", {"c": None}], "d": {}})
    assert 'a: "x\\"y"' in text and '- "z\\nw"' in text and "- c: null" in text and "d: {}" in text

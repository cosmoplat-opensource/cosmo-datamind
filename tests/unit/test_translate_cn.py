# -*- coding: utf-8 -*-
"""中文元数据维护脚本的路径与持久化回归。"""
import json
import pytest
import translate_cn


def test_checked_ir_rejects_parent_reference_even_when_target_exists(tmp_path):
    target = tmp_path / "ir.json"
    target.write_text('{"objects": []}', encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()

    with pytest.raises(ValueError, match="上级目录"):
        translate_cn._checked_ir(str(child / ".." / "ir.json"))


def test_main_translates_and_publishes_complete_json(tmp_path, monkeypatch):
    target = tmp_path / "ir.json"
    target.write_text(json.dumps({
        "objects": [{"name": "fact_sales_order", "cn": "", "attrs": [
            {"col": "order_date", "cn": ""},
        ]}],
    }), encoding="utf-8")
    monkeypatch.setattr(translate_cn, "IR", str(target))

    translate_cn.main()

    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["objects"][0]["cn"]
    assert saved["objects"][0]["attrs"][0]["cn"]
    assert [path.name for path in tmp_path.iterdir()] == ["ir.json"]

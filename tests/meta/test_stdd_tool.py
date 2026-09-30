# -*- coding: utf-8 -*-
"""STDD 工作流脚本:先写规格与场景(红),status --strict 指出缺口(DR-066)。"""
import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _tool(tmp_path, monkeypatch):
    for sub in ("specs/features", "specs/models"):
        shutil.copytree(ROOT / sub, tmp_path / sub)
    shutil.copy(ROOT / "specs" / "acceptance.json", tmp_path / "specs" / "acceptance.json")
    spec = importlib.util.spec_from_file_location("stdd_tool", ROOT / "scripts" / "stdd.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "SPEC", tmp_path / "specs" / "acceptance.json")
    monkeypatch.setattr(mod, "FEATURES", tmp_path / "specs" / "features")
    monkeypatch.setattr(mod, "CONTEXTS", tmp_path / "specs" / "models" / "context_map.json")
    return mod


def test_current_spec_has_no_gaps(tmp_path, monkeypatch):
    assert _tool(tmp_path, monkeypatch).status(strict=True) == 0


def test_new_requirement_starts_red_until_tests_exist(tmp_path, monkeypatch, capsys):
    tool = _tool(tmp_path, monkeypatch)
    assert tool.new("NEW-01", "行子集对象可问数", "governance", "问数", "DR-066") == 0
    text = (tmp_path / "specs" / "features" / "问数.feature").read_text(encoding="utf-8")
    assert "@REQ-NEW-01" in text and "场景: 行子集对象可问数(待实现)" in text
    assert tool.status(strict=True) == 1                     # 没有测试:关卡红
    assert "NEW-01:没有测试" in capsys.readouterr().out


def test_new_rejects_unknown_context(tmp_path, monkeypatch):
    import pytest
    with pytest.raises(SystemExit):
        _tool(tmp_path, monkeypatch).new("NEW-02", "x", "nowhere", "问数", "")

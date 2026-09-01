# -*- coding: utf-8 -*-
"""固定版本的本地标准资产必须真实存在、可解析、可追溯。"""
import json
from pathlib import Path

import standard_assets


ROOT = Path(standard_assets.STANDARD_ROOT)


def test_manifest_declares_only_confined_offline_assets():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert set(manifest["assets"]) == {"bfo_iof", "isa95", "ufo"}
    assert all(".." not in pattern
               for item in manifest["assets"].values() for pattern in item["files"])


def test_all_standard_assets_are_installed_and_parseable():
    status = standard_assets.catalog_status()
    assert all(item["installed"] and item["ready"] for item in status.values())
    assert status["bfo_iof"]["file_count"] == 3
    assert status["bfo_iof"]["triples"] >= 5000
    assert status["isa95"]["file_count"] == 33
    assert status["isa95"]["term_count"] >= 1800
    assert status["ufo"]["triples"] >= 700
    assert status["ufo"]["version"] == "1.0.0"


def test_asset_fingerprints_pin_downloaded_content():
    status = standard_assets.catalog_status()
    assert status["bfo_iof"]["fingerprint"] == "c38c8397df8b23e315ef658cc89a57586b896434646de6c840987beeb34756fa"
    assert status["isa95"]["fingerprint"] == "4593bb4bcc56ad5ce86a75bc8ac90667d9607e799458ff4b49887a8fdf956e69"
    assert status["ufo"]["fingerprint"] == "62bfff8a6443577234d8eba988f679bf70bc8395f0f77d2284388ae1891a0f04"


def test_prompt_context_uses_local_index_without_dumping_whole_standard():
    context = standard_assets.prompt_context("isa95")
    assert "本地机器可读资产已加载" in context
    assert "B2MML V0701" in context
    assert "内容指纹" in context
    assert len(context) < 1600


def test_isa95_distribution_keeps_mesa_license_and_excludes_paid_standard_text():
    license_text = (ROOT / "isa95" / "LICENSE").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "courtesy of MESA International" in license_text
    assert "没有复制受版权保护的 ISA/IEC 标准正文" in readme


def test_missing_or_traversing_manifest_assets_are_reported_not_faked(tmp_path, monkeypatch):
    manifest = {"schema_version": 1, "assets": {
        "missing": {"format": "xsd", "files": ["missing/*.xsd"]},
        "escape": {"format": "xsd", "files": ["../outside.xsd"]},
    }}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(standard_assets, "STANDARD_ROOT", str(tmp_path))
    monkeypatch.setattr(standard_assets, "MANIFEST_PATH", str(path))
    standard_assets.clear_cache()
    status = standard_assets.catalog_status()
    assert status["missing"]["ready"] is False and status["missing"]["errors"]
    assert status["escape"]["ready"] is False and "路径" in status["escape"]["errors"][0]


def test_asset_cache_invalidates_when_local_file_changes(tmp_path, monkeypatch):
    xsd = tmp_path / "one.xsd"
    xsd.write_text('<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
                   '<xsd:element name="Equipment"/></xsd:schema>', encoding="utf-8")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 1, "assets": {
        "fake": {"name": "fake", "format": "xsd", "files": ["one.xsd"]},
    }}), encoding="utf-8")
    monkeypatch.setattr(standard_assets, "STANDARD_ROOT", str(tmp_path))
    monkeypatch.setattr(standard_assets, "MANIFEST_PATH", str(path))
    standard_assets.clear_cache()
    before = standard_assets.status("fake")
    xsd.write_text('<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
                   '<xsd:element name="Equipment"/><xsd:element name="Material"/>'
                   '</xsd:schema>', encoding="utf-8")
    after = standard_assets.status("fake")
    assert before["term_count"] == 1 and after["term_count"] == 2
    assert before["fingerprint"] != after["fingerprint"]

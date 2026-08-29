from __future__ import annotations

import server


def test_build_manifest_persists_reproducible_inputs_without_paths():
    ir = {"scenario": {"name": "测试"}, "objects": [{"name": "dim_customer"}]}
    manifest = server._attach_build_manifest(
        ir,
        q="从数据库和指标 Excel 构建客户本体",
        source_id="demo",
        source_name="示例主库",
        skills=["ontology-semi-auto"],
        cqs=["客户维度表支撑哪些指标？"],
        stability=False,
        method="数据驱动(LLM 离线兜底)",
        evidence={
            "tables": 108,
            "docs": 1,
            "refs": [],
            "files": [{"name": "指标.xlsx", "type": "指标表", "consumed_as_text": True}],
        },
        created_at="2026-08-29T05:30:00+0900",
    )

    assert manifest["source"] == {"id": "demo", "name": "示例主库"}
    assert manifest["skills"] == ["ontology-semi-auto"]
    assert manifest["cqs"] == ["客户维度表支撑哪些指标？"]
    assert manifest["evidence"]["tables"] == 108
    assert manifest["evidence"]["files"][0]["consumed_as_text"] is True
    assert "/Users/" not in str(manifest)
    assert ir["scenario"]["evidence"]["docs"] == 1

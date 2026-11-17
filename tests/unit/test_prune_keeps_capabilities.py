# -*- coding: utf-8 -*-
"""删除页面与接口后仍须保留的能力(DR-109):被取代的接口确实不再存在;独立能力(本体治理、版本保存与锻造、看板删除)和
有脚本、技能或验收测试在用的接口没有被误删;形状校验可单独按需取得。"""
import pytest

import server


def test_shape_validation_is_available_on_demand_from_the_coverage_endpoint():
    pytest.importorskip("pyshacl")
    client = server.app.test_client()
    plain = client.get("/api/ont/completeness/demo").get_json()
    assert "shacl" not in plain                                   # 不随每次覆盖率请求执行
    report = client.get("/api/ont/completeness/demo?shacl=1").get_json()["shacl"]
    assert report["status"] in ("conforms", "violations") and report["triples"] > 0
    assert report["status"] == "violations" or report["violations"] == 0


def _rules():
    return {str(r) for r in server.app.url_map.iter_rules()}


@pytest.mark.parametrize("path", [
    "/api/build/run", "/api/jobs", "/api/job/<jid>",                       # 由 /api/build/jobs 取代
    "/api/skills", "/api/skill/run", "/api/ont/skill/<name>",               # 技能在构建页的技能编排里
    "/api/ont/skills/write", "/api/ont/skills/install",
    "/api/outputs", "/api/outputs/file", "/doc/<name>", "/api/ont/rules", "/api/metric/dimensions",
])
def test_removed_routes_are_gone(path):
    assert path not in _rules()


@pytest.mark.parametrize("path", [
    "/api/ont/cq",            # 部署验收脚本逐项对照
    "/api/ont/rebuild",       # 上游智能体技能按此接口丢弃草案
    "/api/eval/set",          # 数据来源验收测试读取评测题集
    "/api/ont/usage/<key>",   # 问数记录的使用度由它读出
    "/api/ont/accuracy", "/api/ont/rejected-patterns",
    "/api/agents", "/api/routes", "/api/sysinfo",
    "/api/viz/delete",        # 已保存看板的唯一删除途径,现场验收脚本用它清理
    "/api/ont/save", "/api/ont/forge", "/api/ont/forged", "/api/ont/forged/<fid>", "/api/ont/forged/delete",
    "/api/ont/chain", "/api/ont/drift/<key>", "/api/ont/health/<key>", "/api/ont/compat/<key>",
    "/api/ont/modules/<key>", "/api/ont/rulebook/<key>", "/api/ont/rulebook/<key>/delete", "/api/ont/decide/<key>",
    "/api/build/jobs", "/api/build/inquire",
])
def test_routes_with_real_dependents_are_kept(path):
    assert path in _rules()

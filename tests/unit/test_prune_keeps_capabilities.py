# -*- coding: utf-8 -*-
"""删除页面与接口后仍须保留的能力(DR-109):导出形状校验不随「锻造」接口一起消失;
被去掉的接口确实不再存在;有脚本、技能或验收测试在用的接口没有被误删。"""
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
    "/api/build/run", "/api/ont/save", "/api/ont/forge", "/api/ont/forged", "/api/ont/forged/delete",
    "/api/ont/skills/write", "/api/ont/skills/install", "/api/ont/skill/<name>", "/api/skills", "/api/skill/run",
    "/api/jobs", "/api/job/<jid>", "/api/outputs", "/api/outputs/file", "/doc/<name>", "/api/viz/delete",
    "/api/ont/rules", "/api/ont/chain", "/api/ont/drift/<key>", "/api/ont/rulebook/<key>",
    "/api/ont/rulebook/<key>/delete", "/api/ont/decide/<key>", "/api/ont/health/<key>", "/api/ont/compat/<key>",
    "/api/ont/modules/<key>", "/api/metric/dimensions",
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
    "/api/build/jobs", "/api/build/inquire",
])
def test_routes_with_real_dependents_are_kept(path):
    assert path in _rules()

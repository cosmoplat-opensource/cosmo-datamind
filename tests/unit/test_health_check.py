# -*- coding: utf-8 -*-
"""health_check 单测(DR-030)—— 图结构异常检出的隔离验证。

补审计指出的空白:这些确定性模块此前只经 HTTP 间接触达,边界从未被隔离测过。
"""
import health_check


def test_healthy_graph_scores_full(ir_healthy):
    r = health_check.check(ir_healthy)
    assert r["healthy"] is True
    assert r["error_count"] == 0
    assert r["score"] == 100.0


def test_isolated_node_is_signal_not_error(ir_with_isolated):
    r = health_check.check(ir_with_isolated)
    # 孤岛是信号(info),不扣分、不进阻断问题
    assert r["isolated_count"] == 1
    assert r["healthy"] is True
    assert any(s["type"] == "isolated" and s["object"] == "audit_log" for s in r["signals"])


def test_dangling_endpoint_is_hard_error():
    ir = {"objects": [{"id": "a"}],
          "relations": [{"source_concept": "a", "target_concept": "ghost", "status": "verified"}]}
    r = health_check.check(ir)
    assert r["healthy"] is False
    assert any(e["type"] == "dangling" for e in r["errors"])


def test_self_loop_is_hard_error():
    ir = {"objects": [{"id": "a", "cn": "甲"}],
          "relations": [{"source_concept": "a", "target_concept": "a", "verb": "x"}]}
    r = health_check.check(ir)
    assert any(e["type"] == "self_loop" for e in r["errors"])


def test_marked_self_ref_is_not_self_loop_error():
    # DR-036:有意的层级自引用(self_ref=True)不应被判为 self_loop 阻断问题
    ir = {"objects": [{"id": "emp", "cn": "员工"}],
          "relations": [{"source_concept": "emp", "target_concept": "emp",
                         "verb": "上级", "status": "verified", "self_ref": True}]}
    r = health_check.check(ir)
    assert not any(e["type"] == "self_loop" for e in r["errors"])
    assert r["healthy"] is True


def test_status_conflict_verified_vs_rejected():
    ir = {"objects": [{"id": "a"}, {"id": "b"}],
          "relations": [
              {"source_concept": "a", "target_concept": "b", "status": "verified"},
              {"source_concept": "b", "target_concept": "a", "status": "rejected"},
          ]}
    r = health_check.check(ir)
    assert any(e["type"] == "status_conflict" for e in r["errors"])


def test_empty_graph_does_not_crash_and_scores_full():
    r = health_check.check({"objects": [], "relations": []})
    assert r["healthy"] is True
    assert r["score"] == 100.0


def test_gaps_from_only_promotes_hard_errors(ir_with_isolated):
    r = health_check.check(ir_with_isolated)
    gaps = health_check.gaps_from(r)
    # 未连接对象是提示信号，不应进入待补清单，以免掩盖阻断问题。
    assert gaps == []

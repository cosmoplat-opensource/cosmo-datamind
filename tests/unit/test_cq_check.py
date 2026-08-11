# -*- coding: utf-8 -*-
"""cq_check 单测(DR-024)—— 能力问题结构可达性:answerable/partial/unanswerable。"""
import cq_check


def test_answerable_when_objects_anchored_and_path_via_verified(ir_healthy):
    res = cq_check.check_one("员工属于哪个部门", ir_healthy, ["emp", "dept"])
    assert res["verdict"] == "answerable"


def test_unanswerable_when_object_not_in_ontology(ir_healthy):
    # 供应商不在本体中 → 从严判不可答,并指出未锚定
    res = cq_check.check_one("供应商的交货准时率", ir_healthy, ["supplier"])
    assert res["verdict"] == "unanswerable"


def test_partial_or_unanswerable_when_path_only_candidate():
    ir = {"objects": [{"id": "a", "cn": "甲", "table": "a"}, {"id": "b", "cn": "乙", "table": "b"}],
          "relations": [{"source_concept": "a", "target_concept": "b", "status": "candidate"}]}
    res = cq_check.check_one("甲和乙的关系", ir, ["a", "b"])
    # candidate 边不算可靠路径 → 不应判 answerable
    assert res["verdict"] in ("partial", "unanswerable")


def test_check_all_aggregates(ir_healthy):
    cqs = [{"question": "员工属于哪个部门", "expect": ["emp", "dept"]}]
    res = cq_check.check_all(cqs, ir_healthy)
    assert isinstance(res, dict)

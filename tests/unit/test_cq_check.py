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


def test_semantically_rejected_verified_relation_is_not_a_strong_cq_path():
    ir = {"objects": [{"id": "a", "cn": "甲"}, {"id": "b", "cn": "乙"}],
          "relations": [{"source_concept": "a", "target_concept": "b",
                         "status": "verified", "evidence_status": "verified",
                         "semantic": "fail", "semantic_status": "disputed"}]}
    res = cq_check.check_one("甲和乙的关系", ir, ["a", "b"])
    assert res["verdict"] == "partial"
    assert "语义存疑" in res["reason"]


def test_human_asserted_relation_remains_strong_after_review():
    ir = {"objects": [{"id": "a", "cn": "甲"}, {"id": "b", "cn": "乙"}],
          "relations": [{"source_concept": "a", "target_concept": "b",
                         "status": "asserted", "semantic_status": "approved"}]}
    assert cq_check.check_one("甲和乙的关系", ir, ["a", "b"])["verdict"] == "answerable"


def test_check_all_aggregates(ir_healthy):
    cqs = [{"question": "员工属于哪个部门", "expect": ["emp", "dept"]}]
    res = cq_check.check_all(cqs, ir_healthy)
    assert isinstance(res, dict)


def test_one_anchor_without_expected_scope_is_not_false_answerable():
    """多概念问句只命中“供应商”时，不能把其余概念静默忽略后判通过。"""
    ir = {"objects": [{"id": "supplier", "cn": "供应商", "aliases": ["供方"]}], "relations": []}
    res = cq_check.check_one("哪些供应商的采购订单延期且质量评分低？", ir)
    assert [a["key"] for a in res["anchors"]] == ["supplier"]
    assert res["verdict"] == "partial"
    assert "期望对象" in res["fix"]


def test_longest_name_wins_for_nested_chinese_terms():
    ir = {
        "objects": [
            {"id": "supplier", "cn": "供应商"},
            {"id": "supplier_category", "cn": "供应商分类"},
        ],
        "relations": [],
    }
    assert [a["key"] for a in cq_check.anchor_objects("供应商分类有哪些？", ir)] == [
        "supplier_category",
    ]
    assert [a["key"] for a in cq_check.anchor_objects("供应商与供应商分类", ir)] == [
        "supplier", "supplier_category",
    ]


def test_explicit_single_expected_object_can_be_answerable():
    ir = {"objects": [{"id": "supplier", "cn": "供应商"}], "relations": []}
    res = cq_check.check_one("供应商有哪些？", ir, ["supplier"])
    assert res["verdict"] == "answerable"


def test_explicit_expected_objects_become_declared_anchors():
    """自然语言只命中一个别名时，结构化 expect 应补齐另一个声明锚点。"""
    ir = {
        "objects": [
            {"name": "dim_supplier", "cn": "供应商"},
            {"name": "fact_purchase_order", "cn": "采购单"},
        ],
        "relations": [{
            "source_concept": "dim_supplier",
            "target_concept": "fact_purchase_order",
            "status": "verified",
        }],
    }
    res = cq_check.check_one(
        "供应商能否关联到订单？",
        ir,
        ["dim_supplier", "fact_purchase_order"],
    )
    assert res["verdict"] == "answerable"
    assert [a["key"] for a in res["anchors"]] == [
        "dim_supplier", "fact_purchase_order",
    ]
    assert res["anchors"][1]["declared"] is True


def test_ambiguous_expected_alias_is_rejected():
    ir = {
        "objects": [
            {"id": "supplier_a", "aliases": ["供方"]},
            {"id": "supplier_b", "aliases": ["供方"]},
        ],
        "relations": [],
    }
    res = cq_check.check_one("供方有哪些？", ir, ["供方"])
    assert res["verdict"] == "unanswerable"
    assert "指代不唯一" in res["reason"]

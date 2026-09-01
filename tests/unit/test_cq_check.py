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


def test_aliases_tables_repeated_names_and_short_names_are_handled():
    ir = {
        "objects": [{
            "cn": "甲方", "aliases": ["供方", "", 7],
            "tables": ["supplier_table", "", 8], "id": "a",
        }, {"id": "x", "cn": "甲"}],
        "relations": [],
    }
    hits = cq_check.anchor_objects("供方 supplier_table 供方 甲", ir)
    assert [hit["key"] for hit in hits] == ["a"]


def test_graph_helpers_cover_trivial_missing_malformed_and_cycle_paths():
    ir = {
        "objects": [],
        "links": [
            {"source": "a", "target": "b", "status": "verified"},
            {"source": "b", "target": "c", "status": "asserted"},
            {"source": "c", "target": "a", "status": "candidate"},
            {"source": "", "target": "z", "status": "verified"},
        ],
    }
    strong = cq_check._adj(ir, True)
    assert cq_check._path(strong, "a", "a") == ["a"]
    assert cq_check._path(strong, "missing", "a") is None
    assert cq_check._path(strong, "a", "c") == ["a", "b", "c"]
    assert cq_check._path({"a": {"b"}, "b": {"a"}, "z": set()}, "a", "z") is None
    assert cq_check.relation_is_strong({"evidence_status": "candidate"}) is False


def test_no_anchor_and_disconnected_objects_are_unanswerable():
    ir = {
        "objects": [{"id": "a", "cn": "甲方"}, {"id": "b", "cn": "乙方"}],
        "relations": [],
    }
    assert cq_check.check_one("完全未知的问题", ir)["verdict"] == "unanswerable"
    broken = cq_check.check_one("甲方和乙方", ir, ["a", "b"])
    assert broken["verdict"] == "unanswerable"
    assert "不连通" in broken["reason"]


def test_check_all_accepts_all_supported_shapes_and_gaps():
    ir = {"objects": [{"id": "a", "cn": "甲方"}], "relations": []}
    report = cq_check.check_all([
        "甲方有哪些",
        {"q": "甲方有哪些", "expect": ["a"]},
        {"question": "未知概念"},
        {"ignored": True},
        3,
    ], ir)
    assert report["total"] == 3
    assert report["counts"] == {"answerable": 1, "partial": 1, "unanswerable": 1}
    assert report["coverage"] == 33.3
    gaps = cq_check.gaps_from(report)
    assert {gap["type"] for gap in gaps} == {"cq_partial", "cq_unanswerable"}
    assert cq_check.check_all([], ir)["coverage"] == 0.0


def test_chain_verdicts_and_gap_conversion():
    ir = {
        "objects": [
            {"id": "a", "cn": "甲方"},
            {"id": "b", "cn": "乙方"},
            {"id": "c", "cn": "丙方"},
            {"id": "d", "cn": "丁方"},
        ],
        "relations": [
            {"source_concept": "a", "target_concept": "b", "status": "verified"},
            {"source_concept": "b", "target_concept": "c", "status": "candidate"},
        ],
    }
    invalid = cq_check.check_chain(["甲方"], ir)
    assert invalid["verdict"] == "invalid"
    assert cq_check.chain_gaps(invalid) == []

    intact = cq_check.check_chain(["甲方", "乙方"], ir)
    assert intact["verdict"] == "intact"
    assert cq_check.chain_gaps(intact) == []

    weak = cq_check.check_chain(["甲方", "丙方"], ir)
    assert weak["verdict"] == "weak"
    assert cq_check.chain_gaps(weak)[0]["type"] == "chain_weak"

    broken = cq_check.check_chain(["甲方", "丁方"], ir)
    assert broken["verdict"] == "broken"
    assert cq_check.chain_gaps(broken)[0]["type"] == "chain_broken"

    missing = cq_check.check_chain(["甲方", "戊方"], ir)
    assert missing["verdict"] == "unanswerable"
    assert cq_check.chain_gaps(missing)[0]["type"] == "chain_missing_node"

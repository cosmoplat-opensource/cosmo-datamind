# -*- coding: utf-8 -*-
import build_quality


def _valid_ir(status="verified"):
    return {
        "objects": [
            {"name": "employees", "cn": "员工", "definition": "承担组织工作职责的自然人角色", "counterExample": "外部访客"},
            {"name": "departments", "cn": "部门", "definition": "划分组织管理边界的信息结构", "counterExample": "临时群聊"},
        ],
        "relations": [{
            "source_concept": "employees", "target_concept": "departments", "verb": "属于", "status": status,
            "overlap": 100.0,
            "evidence": {"source": "key_overlap", "child_key": "department_id", "parent_key": "department_id",
                         "overlap": 100.0, "parent_unique": True, "child_unique": False,
                         "name_ok": True, "direction": "child_to_parent"},
        }],
    }


def test_all_gates_pass_with_cq():
    report = build_quality.evaluate(_valid_ir(), ["员工属于哪个部门"])
    assert report["result"] == "pass"
    assert report["gate"] == "pass"
    assert report["blocking_pass"] is True
    assert report["hard_pass"] is True
    assert report["cq"]["coverage"] == 100.0


def test_verified_without_replayable_evidence_fails():
    ir = _valid_ir()
    ir["relations"][0]["evidence"] = {"child_key": "department_id", "parent_key": "id", "overlap": 100.0}
    report = build_quality.evaluate(ir, ["员工属于哪个部门"])
    assert report["gate"] == "fail"
    types = {item["type"] for item in report["evidence"]["issues"]}
    assert "verified_parent_not_unique" in types
    assert "verified_name_unsupported" in types


def test_candidate_and_missing_cq_require_review_not_hard_fail():
    report = build_quality.evaluate(_valid_ir(status="candidate"))
    assert report["gate"] == "review"
    assert report["hard_pass"] is True
    types = {item["type"] for item in report["review_queue"]}
    assert {"candidate_relations", "cq_not_provided"} <= types


def test_semantic_dispute_is_review_signal():
    ir = _valid_ir()
    ir["relations"][0]["semantic"] = "fail"
    report = build_quality.evaluate(ir, ["员工属于哪个部门"])
    assert report["gate"] == "review"
    assert any(item["type"] == "semantic_disputes" for item in report["review_queue"])


def test_legacy_schema_fk_is_accepted_as_declared_evidence():
    ir = _valid_ir()
    ir["relations"][0]["evidence"] = {"sources": ["schema-fk"],
                                        "child_key": "department_id", "parent_key": "department_id"}
    ir["relations"][0]["note"] = "声明外键 department_id→departments.department_id"
    report = build_quality.evaluate(ir, ["员工属于哪个部门"])
    assert report["evidence"]["valid"] is True
    assert report["gate"] == "pass"


def test_empty_legacy_links_does_not_hide_current_relations():
    ir = _valid_ir()
    ir["links"] = []
    ir["relations"][0]["evidence"] = {}
    report = build_quality.evaluate(ir, ["员工属于哪个部门"])
    assert report["result"] == "fail"
    assert report["evidence"]["checked"] == 1

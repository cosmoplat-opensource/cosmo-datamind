# -*- coding: utf-8 -*-
import rdflib

import build_quality
import ontology_grounding as grounding
import server


def test_only_official_relation_and_compatible_categories_are_mapped():
    item = grounding.from_verb("描述", "InformationContentEntity", "Process")
    assert item == {"relation": "describes", "iri": "iof:describes",
                    "temporal": "notTemporalized", "status": "mapped", "reason": ""}


def test_category_mismatch_is_kept_as_local_relation():
    item = grounding.from_verb("描述", "Process", "MaterialEntity")
    assert item["status"] == "unmapped"
    assert item["relation"] == "" and item["iri"] == ""
    assert "类别约束不满足" in item["reason"]


def test_legacy_invented_relation_is_not_exported_as_iof_property():
    item = grounding.normalize("relatedToAtSomeTime", "atSomeTime", "关联",
                               "MaterialEntity", "MaterialEntity")
    assert item["status"] == "unmapped"
    assert "不是 BFO/IOF 官方关系" in item["reason"]


def test_generic_output_verb_does_not_claim_specified_output():
    item = grounding.from_verb("产生", "Process", "MaterialEntity")
    assert item["relation"] == "hasOutput"
    assert item["iri"] == "iof:hasOutput"


def test_planned_process_inherits_process_domain_for_input_and_participant():
    input_relation = grounding.from_verb("输入", "PlannedProcess", "MaterialEntity")
    participant_relation = grounding.normalize(
        "hasParticipantAtSomeTime", None, None, "PlannedProcess", "MaterialEntity"
    )
    assert input_relation["status"] == "mapped"
    assert participant_relation["status"] == "mapped"


def test_turtle_uses_official_namespaces_and_omits_unsafe_subproperty():
    ir = {
        "scenario": {"name": "映射测试"},
        "objects": [
            {"name": "record", "cn": "记录", "kind": "ice", "bfo": "InformationContentEntity",
             "definition": "描述业务活动的信息内容实体", "isPrimitive": True, "maturity": "Provisional"},
            {"name": "process", "cn": "过程", "kind": "event", "bfo": "Process",
             "definition": "在一段时间内发生的业务活动", "isPrimitive": True, "maturity": "Released"},
        ],
        "relations": [
            {"source_concept": "record", "target_concept": "process", "verb": "描述", "status": "asserted"},
            {"source_concept": "process", "target_concept": "record", "verb": "关联", "status": "candidate",
             "founded_relation": "relatedToAtSomeTime", "temporal": "atSomeTime"},
        ],
    }
    ttl = server._ir_to_turtle("test", ir)
    assert "<https://spec.industrialontologies.org/ontology/construct/>" in ttl
    assert "<https://spec.industrialontologies.org/ontology/annotation/>" in ttl
    assert "rdfs:subPropertyOf iof:describes" in ttl
    assert "iof:relatedToAtSomeTime" not in ttl
    assert ':groundingStatus "unmapped"' in ttl
    rdflib.Graph().parse(data=ttl, format="turtle")


def test_invalid_claimed_mapping_is_reported_for_review():
    ir = {
        "objects": [
            {"name": "a", "kind": "event", "bfo": "Process", "definition": "发生的活动", "counterExample": "设备"},
            {"name": "b", "kind": "object", "bfo": "MaterialEntity", "definition": "物质实体", "counterExample": "过程"},
        ],
        "relations": [{"source_concept": "a", "target_concept": "b", "verb": "描述",
                       "status": "candidate", "founded_relation": "describes"}],
    }
    report = build_quality.evaluate(ir, ["a 描述什么"])
    assert report["result"] == "review"
    assert report["grounding"]["valid"] is False
    assert any(item["type"] == "invalid_upper_relation_mapping" for item in report["review_queue"])

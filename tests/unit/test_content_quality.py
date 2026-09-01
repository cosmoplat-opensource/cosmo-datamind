# -*- coding: utf-8 -*-
import content_quality


def test_information_artifact_terms_take_precedence_over_process_terms():
    kind, reason = content_quality.normalize_kind(
        "event", name="fact_quality_inspection_record", cn="质量检验记录")
    assert kind == "ice"
    assert reason == "information_artifact_term"


def test_explicit_event_without_record_term_is_event():
    kind, reason = content_quality.normalize_kind(
        "object", name="equipment_failure_event", cn="设备故障事件")
    assert kind == "event"
    assert reason == "event_or_process_term"


def test_example_must_be_locatable_in_evidence():
    assert content_quality.locate_example(
        "订单 SO-17", (("docs", "现场记录包含订单 SO-17，状态为已下达。"),)) == "docs"
    assert content_quality.locate_example(
        "订单 SO-18", (("docs", "现场记录包含订单 SO-17，状态为已下达。"),)) is None


def test_sanitize_ir_is_non_destructive_and_withholds_unsupported_example():
    raw = {"objects": [{"name": "repair_record", "cn": "维修记录", "kind": "event",
                         "definition": "设备修复过程中发生的活动",
                         "example": "2026-01-03 修复 A-100 设备",
                         "counterExample": "维修计划"}], "relations": []}
    clean = content_quality.sanitize_ir(raw)
    assert raw["objects"][0]["example"]  # 历史输入未被改写
    obj = clean["objects"][0]
    assert obj["kind"] == "ice"
    assert obj["bfo"] == "InformationContentEntity"
    assert obj["definition"] == ""
    assert obj["example"] == ""
    assert obj["example_provenance"]["status"] == "withheld_no_source"
    assert obj["counterexample_status"] == "illustrative_pending_review"

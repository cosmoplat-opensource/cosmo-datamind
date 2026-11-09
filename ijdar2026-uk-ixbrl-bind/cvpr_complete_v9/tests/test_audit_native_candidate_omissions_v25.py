import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_native_candidate_omissions_v25 import (
    field_status,
    period_status,
    source_evidence_task,
)


def card(reports):
    return {"name": "Revenue", "namespace": "fixture", "field_id": "fixture",
            "labels": [], "labels_by_report": {}, "declaration_variants": [
                {"reports": reports, "declaration": {"type": "money", "balance": "credit",
                                                      "period_type": "duration", "abstract": False}}]}


def test_global_catalog_presence_alone_is_not_a_retrieval_cutoff_failure():
    expected = "{fixture}Revenue"
    assert field_status(expected, "local", ["fixture"], {expected: card(["another"])}, set()) == "no_report_local_or_public_standard_declaration"
    assert field_status(expected, "local", ["other"], {expected: card(["local"])}, set()) == "namespace_not_declared_for_report"
    assert field_status(expected, "local", ["fixture"], {}, set()) == "absent_from_global_public_catalog"


def test_local_and_fixed_standard_cards_are_eligible_without_numeric_type_filter():
    expected = "{fixture}Revenue"
    for reports in (["local"], ["public_standard"]):
        assert field_status(expected, "local", ["fixture"], {expected: card(reports)}, set()) == "eligible_report_local_field_omitted_by_retrieval_cutoff"
    assert field_status(expected, "local", [], {}, {expected}) == "returned_candidate"


def test_same_end_year_is_not_a_complete_period_and_source_year_is_not_a_clue():
    expected = {"kind": "instant", "instant": "2023-12-31"}
    values = [{"kind": "duration", "start_date": "2023-01-01", "end_date": "2023-12-31"}]
    row = period_status(expected, values, {"source_year": 2023, "public_top_text": "For the year ended 31 December 2024. 2023 2024"})
    assert not row["complete_period_available"] and row["expected_end_available_with_any_period_kind"]
    assert row["expected_end_year_printed_in_generator_text"] and row["a_full_date_with_expected_month_day_visible"]
    assert not row["expected_end_explicitly_visible_in_generator_text"]
    blank = period_status(expected, [], {"source_year": 2023})
    assert not blank["expected_end_year_printed_in_generator_text"]


def test_nonannual_and_leap_day_intervals_are_not_claimed_to_be_calendar_annual():
    row = period_status({"kind": "duration", "start_date": "2023-04-01", "end_date": "2023-12-31"}, [], {})
    assert not row["expected_duration_matches_calendar_annual_hypothesis"]
    leap = period_status({"kind": "duration", "start_date": "2023-03-01", "end_date": "2024-02-29"}, [], {})
    assert not leap["expected_duration_matches_calendar_annual_hypothesis"]


def test_reduced_binding_records_cannot_be_used_as_empty_original_public_text():
    reduced = {"task_id": "t", "report": "r"}
    with pytest.raises(ValueError, match="Original full public"):
        source_evidence_task(reduced, reduced)
    original = {**reduced, "public_top_text": "2023", "public_row_text": "Revenue", "common_visual_observation": None}
    assert source_evidence_task(reduced, original) is original
    with pytest.raises(ValueError, match="another task"):
        source_evidence_task(reduced, {**original, "report": "other"})

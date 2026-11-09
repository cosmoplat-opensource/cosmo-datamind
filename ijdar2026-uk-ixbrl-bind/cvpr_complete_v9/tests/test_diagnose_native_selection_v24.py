import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from diagnose_native_selection_v24 import availability, nonperiod_state, period_error


def test_unit_and_quantity_must_be_available_in_the_same_joint_candidate():
    a, b = {"numerator": ["A"], "denominator": []}, {"numerator": ["B"], "denominator": []}
    entity = {"identifier": "fixture-only"}
    period = {"kind": "instant", "instant": "2023-12-31"}
    reference = {"field": "f", "entity": entity, "period": period, "dimensions": [], "unit": a, "quantity": "2"}
    item = {"components": {"field": [{"value": "f"}], "period": [{"value": period}],
                           "dimensions": [{"value": []}], "unit_quantity": [
                               {"value": {"unit": a, "quantity": "1"}}, {"value": {"unit": b, "quantity": "2"}}]}}
    flags = availability({"task_id": "fixture", "entity_candidates": [{"entity": entity}]}, item, reference)
    assert flags["unit"] and flags["quantity"] and not flags["unit_quantity"]
    assert not flags["complete_binding_available"]


def test_year_only_supervision_is_not_assumed_to_fix_same_year_kind_or_interval_errors():
    expected = {"kind": "duration", "start_date": "2023-01-01", "end_date": "2023-12-31"}
    assert period_error({"kind": "instant", "instant": "2023-12-31"}, expected, True) == "same_end_year_but_wrong_instant_or_duration_kind"
    assert period_error({"kind": "duration", "start_date": "2023-04-01", "end_date": "2023-12-31"}, expected, True) == "same_end_year_and_kind_but_wrong_complete_date_or_interval"
    assert period_error(None, expected, False) == "correct_complete_period_absent_from_public_candidates"


def test_numeric_lexical_variants_do_not_create_false_nonperiod_changes():
    first = {"field": "f", "entity": None, "unit": None, "dimensions": [], "quantity": "2.00"}
    second = {**first, "quantity": "2"}
    assert nonperiod_state(first) == nonperiod_state(second)

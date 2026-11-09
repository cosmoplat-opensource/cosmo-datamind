from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_binding_candidate_audit_v16 import matching_components


def example():
    unit = {"numerator": ["GBP"], "denominator": []}
    period = {"kind": "instant", "instant": "2022-12-31"}
    entity = {"scheme": "LEI", "identifier": "C"}
    task = {"task_id": "t", "source_year": 2023,
            "field_candidates": [{"namespace": "urn:f", "name": "Cash"}],
            "entity_candidates": [{"entity": entity}],
            "unit_candidates": [{"unit": unit, "multiplier": "1000000"}],
            "quantity_candidates": [{"printed_value": "5", "semantic_sign_candidates": [1, -1]}],
            "period_candidates": [{"period": period | {"complete": True}}],
            "dimension_tuple_candidates": [{"dimensions": []}]}
    teacher = {"task_id": "t", "source_year": 2023,
               "teacher_binding": {"field": "{urn:f}Cash", "entity": entity, "unit": unit,
                                   "period": period, "dimensions": [], "quantity": "5000000"}}
    return task, teacher


def test_quantity_and_unit_must_be_available_in_the_same_candidate():
    task, teacher = example()
    assert matching_components(task, teacher)["all_components_representable_without_certifying_scope"]
    task["unit_candidates"] = [{"unit": teacher["teacher_binding"]["unit"], "multiplier": "1000"},
                               {"unit": {"numerator": ["USD"], "denominator": []}, "multiplier": "1000000"}]
    result = matching_components(task, teacher)
    assert result["unit_identity"] and result["quantity_any_proposed_unit_scale"]
    assert not result["quantity_and_unit_same_candidate"]
    assert not result["all_components_representable_without_certifying_scope"]


def test_future_teacher_is_rejected_and_missing_candidate_is_not_false_null():
    task, teacher = example()
    task["field_candidates"] = []
    assert matching_components(task, teacher)["field"] is False
    teacher["source_year"] = 2024
    with pytest.raises(ValueError, match="historical"):
        matching_components(task, teacher)


def test_exported_one_day_later_instant_does_not_replace_source_period():
    task, teacher = example()
    task["period_candidates"][0]["period"]["instant"] = "2023-01-01"
    result = matching_components(task, teacher)
    assert not result["period"]
    assert not result["all_components_representable_without_certifying_scope"]


def test_unknown_teacher_is_neither_a_correct_candidate_nor_a_null_training_label():
    task, teacher = example()
    teacher["teacher_binding"] = None
    assert matching_components(task, teacher) == {"teacher_available": False}

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_witness_inventory_v23 import component_groups, exact_decimal, semantic_key


def headers():
    return {key: {"status": "ambiguous_lexical_locations", "may_supply_transfer_hypotheses": True,
                  "anchors": [{"anchor_id": key + "a"}, {"anchor_id": key + "b"}]} for key in ("h0", "h1")}


def test_numeric_grouping_is_exact_and_does_not_round_long_quantities():
    value = "123456789012345678901234567890.123450000"
    assert exact_decimal(value) == "123456789012345678901234567890.12345"
    assert exact_decimal("-0.000") == "0"
    assert semantic_key("unit_quantity", {"unit": {"numerator": ["GBP"]}, "quantity": "1000.00"}) == semantic_key("unit_quantity", {"unit": {"numerator": ["GBP"]}, "quantity": "1E3"})


def test_unit_aliases_and_all_locations_survive_semantic_deduplication():
    unit = {"numerator": ["GBP"], "denominator": []}
    task = {"task_id": "x", "quantity_candidates": [{"printed_value": "2", "semantic_sign_candidates": [1]}],
            "unit_candidates": [{"unit": unit, "multiplier": "1000", "kind": "monetary", "header": {"id": key}} for key in ("h0", "h1")]}
    records = [{"value": {"unit": unit, "quantity": "2000"},
                "metadata": {"header": {"id": "h0"}, "printed": "2", "multiplier": "1000", "semantic_sign": 1}}]
    result = component_groups(task, records, headers(), "unit_quantity")
    assert len(result) == 2  # one semantic value and the explicit unknown
    located = [w for w in result[0]["witnesses"] if w["anchor"]]
    assert {w["anchor"]["anchor_id"] for w in located} == {"h0a", "h0b", "h1a", "h1b"}
    assert any(w["ownership"] == "unknown" for w in result[0]["witnesses"])
    assert not any(w["visual_ownership_certified"] for w in located)


def test_period_value_keeps_unlocated_and_located_paths():
    period = {"kind": "instant", "instant": "2023-12-31"}
    records = [{"value": period, "metadata": {}}, {"value": period, "metadata": {"header": {"id": "h0"}}}]
    result = component_groups({"task_id": "x"}, records, headers(), "period")
    assert len(result) == 2
    assert result[0]["source_record_indices"] == [0, 1]
    assert len([w for w in result[0]["witnesses"] if w["anchor"]]) == 2
    assert any(w["anchor"] is None for w in result[0]["witnesses"])


def test_unit_restoration_cannot_insert_an_unproposed_answer_value():
    unit = {"numerator": ["GBP"], "denominator": []}
    task = {"task_id": "x", "quantity_candidates": [{"printed_value": "3", "semantic_sign_candidates": [1]}],
            "unit_candidates": [{"unit": unit, "multiplier": "1000", "kind": "monetary", "header": {"id": "h0"}}]}
    with pytest.raises(ValueError, match="new semantic value"):
        component_groups(task, [], headers(), "unit_quantity")

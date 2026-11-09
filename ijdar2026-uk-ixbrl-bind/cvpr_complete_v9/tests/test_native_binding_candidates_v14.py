from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_binding_candidates_v14 import component_record, literal_values, unit_identity, visible_period


def test_locale_ambiguity_and_negative_glyphs_are_preserved():
    assert set(literal_values("1,234")) == {"1234", "1.234"}
    assert set(literal_values("1.234")) == {"1234", "1.234"}
    assert literal_values("(1.234,50)") == ["-1234.50"]
    assert literal_values("1\u202f234,50") == ["1234.50"]


def test_dash_and_unrelated_digits_are_not_silently_zero_or_concatenated():
    assert literal_values("—") == []
    assert literal_values("-") == []
    assert literal_values("6 7") == []
    assert literal_values("(-3)") == []


def test_date_year_does_not_supply_missing_duration_boundaries():
    p = visible_period({"kind": "duration", "displayed_year": 2023, "start_date": None, "end_date": "2023-12-31"})
    assert p["start_date"] is None
    assert not p["complete"]
    with pytest.raises(ValueError, match="Inverted"):
        visible_period({"kind": "duration", "start_date": "2024-01-01", "end_date": "2023-12-31"})


def test_per_share_unit_is_not_monetary_amount():
    u = unit_identity({"kind": "currency_per_share", "currency": "GBP"})
    assert u["denominator"] == ["{http://www.xbrl.org/2003/instance}shares"]
    assert unit_identity({"kind": "monetary", "currency": "unknown"}) is None


def test_private_labels_and_scale_oracles_do_not_enter_public_candidates():
    task = {"task_id": "public", "company": "C", "source_year": 2025, "report": "R",
            "target_box": [0, 0, 1, 1], "field_candidates": [{"field_id": "public_field"}],
            "native_ocr_strings": ["123"], "common_visual_observation": None,
            "common_observation_failed": True, "canonical_value": "PRIVATE_VALUE",
            "oracle_multiplier": "PRIVATE_SCALE", "canonical_dimensions": {"unit": "PRIVATE_UNIT"}}
    record = component_record(task)
    assert "PRIVATE" not in str(record)
    assert record["unit_candidates"] == [] and record["period_candidates"] == []
    assert record["unknown_candidate_retained_for_each_component"]
    assert set(record["unknown_candidates"]) == {"field", "quantity", "unit", "period", "entity", "dimension", "scope"}
    assert all(c["unknown"] for c in record["unknown_candidates"].values())
    assert record["common_observation_failed"]

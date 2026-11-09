import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_transfer_diagnosis_v22 import (
    condition_relations,
    failure_category,
    historical_teacher,
    pair_separation,
)


def test_missing_correct_values_are_not_selection_errors_or_false_nulls():
    assert failure_category([True, False, True, True], [True] * 4, [True] * 4) == "correct_component_missing_from_full_candidates"
    assert failure_category([True] * 4, [True, False, True, True], [False] * 4) == "correct_component_pruned_from_joint_beam"
    assert failure_category([True] * 4, [True] * 4, [False] * 4) == "selection_error_with_correct_state_available"
    assert failure_category([True] * 4, [True] * 4, [True] * 4) == "semantic_tuple_exact"


def test_transfer_ranking_keeps_ties_and_excludes_missing_evidence():
    assert pair_separation([.5, .5], [True, False], [True, True]) == .5
    assert pair_separation([.9, .1, 0], [True, False, False], [True, True, False]) == 1
    assert pair_separation([0, .1], [True, False], [False, True]) is None
    assert pair_separation([.9, .8, .1], [True, True, False], [True] * 3) == 1
    with pytest.raises(ValueError):
        pair_separation([float("nan")], [True], [True])


def test_localization_control_preserves_availability_and_other_features():
    original = np.arange(32, dtype=float).reshape(2, 16)
    original_copy = original.copy()
    altered = condition_relations("localization_only", original, original[:, -8:])
    assert np.array_equal(original, original_copy)
    assert not altered[:, [8, 9, 10, 12, 13, 14]].any()
    assert np.array_equal(altered[:, [11, 15]], original[:, [11, 15]])
    assert np.array_equal(altered[:, :8], original[:, :8])


def test_teacher_access_rejects_future_and_mismatched_ids():
    with pytest.raises(ValueError):
        historical_teacher({"source_year": 2024, "task_id": "x"}, {})
    with pytest.raises(ValueError):
        historical_teacher({"source_year": 2023, "task_id": "x"}, {"x": {"source_year": 2023, "task_id": "y"}})

import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_field_learning_v11 import FieldMatcher, historical_positive


def test_missing_historical_field_is_not_a_false_null_label():
    task = {"task_id": "x", "source_year": 2023, "field_candidates": [{"name": "A", "namespace": "urn:2023"}]}
    teacher = {"task_id": "x", "source_year": 2023, "field_name": "B", "field_namespace": "urn:2023"}
    assert not historical_positive(task, teacher).any()
    teacher["field_name"] = "A"
    assert historical_positive(task, teacher).tolist() == [True, False]
    teacher["field_namespace"] = "urn:2024"
    assert not historical_positive(task, teacher).any()


def test_future_teacher_cannot_train_field_correspondence():
    task = {"task_id": "x", "source_year": 2024, "field_candidates": []}
    teacher = {"task_id": "x", "source_year": 2024, "field_name": "A", "field_namespace": "urn:x"}
    with pytest.raises(ValueError):
        historical_positive(task, teacher)


def test_equal_input_matchers_have_candidate_scores_and_no_source_lookup():
    fields, context = torch.randn(5, 9), torch.randn(12)
    supports = [(torch.randn(9), torch.randn(12)) for _ in range(3)]
    for method in ("generic_joint", "factor_field", "learned_semantic_memory", "frozen_semantic_memory"):
        model = FieldMatcher(method, 9, 12)
        assert model(fields, context, supports).shape == (5,)
    with pytest.raises(ValueError):
        FieldMatcher("unknown", 9, 12)

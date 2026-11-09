from pathlib import Path
import sys

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_field_replay_v15 import replay_fit


def test_replay_rejects_later_year_or_own_company_global_examples():
    past = {"past": True, "company": "C"}
    future = {"past": False, "company": "D"}
    with pytest.raises(ValueError, match="Later-year"):
        replay_fit(None, [0], [1], [past, future], "C", 19)
    with pytest.raises(ValueError, match="Company boundary"):
        replay_fit(None, [0], [1], [past, past], "C", 19)


def test_unusable_feedback_does_not_train_a_false_unknown_label():
    packs = [{"past": True, "company": "C", "positive": torch.tensor([False, False])},
             {"past": True, "company": "D", "positive": torch.tensor([True, False])}]
    result = replay_fit(None, [0], [1], packs, "C", 19)
    assert result == {"steps": 0, "local_example_exposures": 0, "historical_example_exposures": 0}


def test_replay_counts_actual_exposures_and_optimizes_valid_examples():
    class Matcher(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.tensor([0.0]))

        def forward(self, fields, context):
            return fields[:, 0]*self.weight

    packs = [{"past": True, "company": c, "positive": torch.tensor([True, False]),
              "fields": torch.tensor([[1.0], [0.0]]), "context": torch.tensor([0.0]),
              "prior": torch.tensor([0.0, 0.0])} for c in ("C", "D")]
    model = Matcher()
    result = replay_fit(model, [0], [1], packs, "C", 19, steps=3)
    assert result == {"steps": 3, "local_example_exposures": 3, "historical_example_exposures": 3}
    assert model.weight.item() > 0

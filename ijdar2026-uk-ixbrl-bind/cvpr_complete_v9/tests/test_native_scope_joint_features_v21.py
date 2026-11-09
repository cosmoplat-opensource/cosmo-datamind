import sys
from pathlib import Path

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_joint_learning_v18 import prototype_mask
from native_scope_joint_features_v21 import donor_vector, transfer_mask


def test_missing_transfer_remains_explicitly_unavailable():
    assert np.array_equal(donor_vector({}), np.zeros(4))
    assert np.array_equal(donor_vector({"generic": {"available_transfer_hypothesis": False}, "scope": {"available_transfer_hypothesis": False}}), np.zeros(4))


def test_mismatched_or_invalid_donor_controls_are_rejected():
    with pytest.raises(ValueError):
        donor_vector({"generic": {"available_transfer_hypothesis": True}})
    row = {"generic": {"available_transfer_hypothesis": True, "mean_joint_probability": .2, "mean_optimal_span_joint_probability": .4},
           "scope": {"available_transfer_hypothesis": True, "mean_joint_probability": .6}}
    assert np.allclose(donor_vector(row), [.2, .4, .6, 1])
    row["scope"]["mean_joint_probability"] = float("nan")
    with pytest.raises(ValueError):
        donor_vector(row)


def test_partial_prototypes_preserve_old_masks_and_hide_unobserved_transfer_channels():
    known = [False, True, False, False]
    mask = transfer_mask(known)
    assert torch.equal(mask[:460], prototype_mask(known))
    assert mask[-8:-4].all() and not mask[-4:].any()

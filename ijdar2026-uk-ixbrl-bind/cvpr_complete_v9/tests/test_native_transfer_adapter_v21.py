import sys
from itertools import product
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import continue_native_transfer_v21 as adapter
import native_joint_learning_v18 as base
from native_scope_joint_features_v21 import REL_DIM, STATE_DIM


def test_three_conditions_preserve_state_labels_priors_and_localized_geometry(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "DEST", tmp_path)
    (tmp_path / "features").mkdir()
    states = np.array(list(product(range(2), repeat=4)), dtype="int32")
    relations = np.zeros((16, REL_DIM), dtype="float32")
    relations[:, 6:8] = .3
    relations[:, -8:] = .8
    relations[:, [11, 15]] = 1
    alternate = np.full((16, 8), .2, dtype="float32")
    alternate[:, [3, 7]] = 1
    arrays = {"states_C_19": states, "unary_C_19": np.zeros((16, 4), dtype="float32"),
              "relations_C_19": relations, "scope_pixels_zeroed_C_19": alternate}
    for channel in base.CHANNELS:
        arrays["node_" + channel] = np.zeros((2, 112), dtype="float32")
    np.savez(tmp_path / "features/t.npz", **arrays)
    task = {"task_id": "t", "source_year": 2023, "company": "C"}
    binding = {"field": "F", "period": {"kind": "instant", "instant": "2022-12-31"},
               "dimensions": [], "unit": {"numerator": ["GBP"], "denominator": []}, "quantity": "1"}
    record = {c: [{"value": binding[c]}] for c in ("field", "period", "dimensions")}
    record["unit_quantity"] = [{"value": {"unit": binding["unit"], "quantity": "1"}}]
    feedback = {"t": {"task_id": "t", "source_year": 2023, "teacher_binding": binding}}
    packs = {c: adapter.load_packs(c, "C", 19, [task], [record], torch.zeros((1, 1156)), feedback)[0] for c in adapter.CONDITIONS}
    full, zero, localized = [packs[c] for c in adapter.CONDITIONS]
    for value in packs.values():
        assert value["states"].shape == (16, STATE_DIM)
        assert value["known"] == [True] * 4
        assert torch.equal(value["positive"], full["positive"])
        assert torch.equal(value["prior"], full["prior"])
        assert torch.equal(value["states"][:, :460], full["states"][:, :460])
    assert torch.allclose(full["states"][0, -8:], torch.tensor([.8, .8, .8, 1, .8, .8, .8, 1]))
    assert torch.allclose(zero["states"][0, -8:], torch.tensor([.2, .2, .2, 1, .2, .2, .2, 1]))
    assert torch.equal(localized["states"][0, -8:], torch.tensor([0., 0., 0., 1., 0., 0., 0., 1.]))


def test_expanded_joint_heads_keep_capacity_matching_and_valid_gradients(monkeypatch):
    monkeypatch.setattr(base, "STATE_DIM", STATE_DIM)
    monkeypatch.setattr(base, "REL_DIM", REL_DIM)
    torch.manual_seed(19)
    counts = []
    for method in ("dense_joint", "coupled_joint", "joint_memory"):
        model = base.JointMatcher(method, 1156)
        counts.append(sum(p.numel() for p in model.parameters()))
        x, context = torch.randn(4, STATE_DIM), torch.randn(1156)
        supports = [(torch.zeros(STATE_DIM), torch.ones(1156), torch.ones(STATE_DIM))]
        scores = model(x, context, supports)
        loss = torch.logsumexp(scores, 0) - scores[0]
        loss.backward()
        assert torch.isfinite(scores).all() and torch.isfinite(loss)
        assert all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters())
    assert max(counts) / min(counts) < 1.01

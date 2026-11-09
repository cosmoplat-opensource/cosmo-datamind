import json
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import native_scope_score_v23 as scoring
from native_scope_composition_v23 import ScopeComposer
from native_scope_learning_v23 import bank_for, fit
from test_native_scope_composition_v23 import pack


def training_packs():
    packs = {i: pack(i) for i in range(3)}
    for value in packs.values():
        value.update(year=2023, factor_pairs=24)
    return packs


def test_future_cases_cannot_enter_the_feedback_memory():
    packs = training_packs()
    packs[1]["year"] = 2024
    with pytest.raises(ValueError, match="historical"):
        bank_for([0, 1], packs)
    with pytest.raises(ValueError, match="unique"):
        bank_for([0, 0], packs)


def test_optimizer_resume_preserves_exact_budget_sequence_and_weights(tmp_path):
    torch.set_num_threads(2)
    packs = training_packs()
    torch.manual_seed(19)
    complete = ScopeComposer("generic_relational", 11)
    expected = fit(complete, [0, 1, 2], [], packs, 64, 19)
    torch.manual_seed(19)
    interrupted = ScopeComposer("generic_relational", 11)
    resume = tmp_path / "optimizer.resume.pt"

    def interrupt(step, exposure):
        raise InterruptedError("simulated external interruption after checkpoint")

    with pytest.raises(InterruptedError):
        fit(interrupted, [0, 1, 2], [], packs, 64, 19, resume, "same-design", interrupt)
    restored = ScopeComposer("generic_relational", 11)
    actual = fit(restored, [0, 1, 2], [], packs, 64, 19, resume, "same-design")
    assert actual["optimizer_steps"] == expected["optimizer_steps"] == 64
    for key in ("sampled_local_cases", "sampled_replay_cases", "sampled_witness_factor_pairs", "case_sequence_sha256"):
        assert actual[key] == expected[key]
    assert all(torch.equal(value, restored.state_dict()[name]) for name, value in complete.state_dict().items())


def test_partial_prediction_manifest_is_rejected_before_scoring_references(tmp_path, monkeypatch):
    (tmp_path / "manifest.json").write_text(json.dumps({"complete": False, "prediction_files": 1}))
    (tmp_path / "design.json").write_text("{}")
    monkeypatch.setattr(scoring, "FOLDER", tmp_path)

    def forbidden(*args):
        raise AssertionError("future references read before all predictions froze")

    monkeypatch.setattr(scoring, "scoring_references", forbidden)
    with pytest.raises(ValueError, match="1512"):
        scoring.run()

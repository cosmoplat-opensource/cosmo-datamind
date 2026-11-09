import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_year_composition_v24 import YearScopeComposer
from native_year_fit_v24 import configure_auxiliary, fit_year
from test_native_scope_learning_v23 import training_packs
from test_native_year_composition_v24 import year_anchors


def inputs():
    packs = training_packs()
    for p in packs.values():
        p["company"] = "B"
        for role, nodes in enumerate(p["nodes"][1:], 1):
            nodes[:, 112:] = 0
            nodes[:, 112 + 96 + min(role, 3)] = 1
            nodes[:, 112 + 100] = 1
    auxiliary = {"inputs": year_anchors(), "labels": torch.tensor([0., 1., 0., 1.]),
                 "active": [0, 1, 2, 3], "excluded_company": "A", "feature_manifest_sha256": "fixture-only",
                 "index": [{"company": "B", "source_year": 2023} for _ in range(4)]}
    return packs, auxiliary


def test_actual_binding_auxiliary_cases_and_dense_work_are_accounted_for_each_control():
    torch.set_num_threads(2)
    packs, auxiliary = inputs()
    exposures = []
    for method in ("generic_relational", "semantic_memory", "scope_relational"):
        torch.manual_seed(19)
        model = YearScopeComposer(method, 11)
        exposures.append(fit_year(model, [0, 1, 2], [], packs, auxiliary, 2, 19, stage="parent"))
    assert len({r["case_sequence_sha256"] for r in exposures}) == 1
    assert len({r["auxiliary_case_sequence_sha256"] for r in exposures}) == 1
    assert all(r["optimizer_steps"] == 2 and r["sampled_auxiliary_year_cases"] == 8 for r in exposures)
    assert all(r["counted_registered_dense_flops"] > 0 and r["extra_own_company_auxiliary_cases"] == 0 for r in exposures)


def test_joint_resume_preserves_both_rngs_actual_examples_and_final_weights(tmp_path):
    torch.set_num_threads(2)
    packs, auxiliary = inputs()
    torch.manual_seed(19)
    complete = YearScopeComposer("scope_relational", 11)
    expected = fit_year(complete, [0, 1, 2], [], packs, auxiliary, 64, 19, stage="parent")
    torch.manual_seed(19)
    interrupted = YearScopeComposer("scope_relational", 11)
    resume = tmp_path / "joint.resume.pt"

    def interrupt(step, exposure):
        raise InterruptedError("simulated interruption after atomic optimizer checkpoint")

    with pytest.raises(InterruptedError):
        fit_year(interrupted, [0, 1, 2], [], packs, auxiliary, 64, 19, stage="parent", resume=resume, design_sha="same-design", progress=interrupt)
    restored = YearScopeComposer("scope_relational", 11)
    actual = fit_year(restored, [0, 1, 2], [], packs, auxiliary, 64, 19, stage="parent", resume=resume, design_sha="same-design")
    for key in ("optimizer_steps", "case_sequence_sha256", "auxiliary_case_sequence_sha256",
                "sampled_auxiliary_year_cases", "counted_registered_dense_flops"):
        assert actual[key] == expected[key], key
    assert all(torch.equal(value, restored.state_dict()[name]) for name, value in complete.state_dict().items())


def test_own_company_and_future_weak_labels_are_rejected_before_optimization():
    packs, auxiliary = inputs()
    model = YearScopeComposer("scope_relational", 11)
    auxiliary["index"][0]["company"] = "A"
    with pytest.raises(ValueError, match="own-company or future"):
        configure_auxiliary(model, auxiliary)
    auxiliary["index"][0].update(company="B", source_year=2024)
    with pytest.raises(ValueError, match="own-company or future"):
        fit_year(model, [0, 1, 2], [], packs, auxiliary, 2, 19, stage="parent")


def test_unknown_local_feedback_consumes_slots_without_extra_scope_supervision():
    packs, auxiliary = inputs()
    packs[0]["company"] = "A"
    packs[0]["masks"] = [torch.zeros_like(mask) for mask in packs[0]["masks"]]
    model = YearScopeComposer("scope_relational", 11)
    result = fit_year(model, [0], [1, 2], packs, auxiliary, 64, 19, stage="local")
    assert result["optimizer_steps"] == 0 and result["sampled_auxiliary_year_cases"] == 0
    assert result["extra_own_company_auxiliary_cases"] == 0

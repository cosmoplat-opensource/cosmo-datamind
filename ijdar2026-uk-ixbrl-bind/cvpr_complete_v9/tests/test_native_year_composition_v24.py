import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_year_composition_v24 import CommonYearMemory, YearScopeComposer
from test_native_scope_composition_v23 import pack


def year_anchors(n=4):
    torch.manual_seed(20261016)
    result = torch.randn(n, 112) * .1
    result[:, 80:84] = torch.tensor([.4, .1, .5, .12])
    result[:, 84:88] = torch.tensor([.4, .3, .5, .32])
    result[:, 96:104] = 0
    result[:, 97] = 1
    result[:, 100] = 1
    return result


def test_scope_and_memory_share_common_parameters_and_same_case_memory():
    torch.manual_seed(19)
    generic = YearScopeComposer("semantic_memory", 11)
    torch.manual_seed(19)
    scoped = YearScopeComposer("scope_relational", 11)
    for key, value in generic.state_dict().items():
        if not key.startswith("geometry.period."):
            assert torch.equal(value, scoped.state_dict()[key]), key
    counts = [sum(p.numel() for p in model.parameters() if p.requires_grad) for model in (generic, scoped)]
    assert max(counts) / min(counts) < 1.01
    data, labels = year_anchors(), torch.tensor([0., 1., 0., 1.])
    for model in (generic, scoped):
        model.configure_auxiliary(data, labels, [1, 2, 3, 4])
        assert torch.equal(model.geometry.memory.bank_labels, labels)
        assert torch.nn.functional.softplus(model.geometry.raw_period_scale) > 0


def test_auxiliary_memory_excludes_the_actual_query_case():
    memory = CommonYearMemory()
    data, labels = year_anchors(2), torch.tensor([1., 0.])
    memory.configure(data, labels, [7, 8])
    memory.strength.data.fill_(1)
    assert memory(data[:1], torch.tensor([7])).item() < 0  # only the negative case may vote
    assert memory(data[1:], torch.tensor([8])).item() > 0  # only the positive case may vote
    with pytest.raises(ValueError, match="exclusion"):
        memory(data, torch.tensor([7]))


def test_joint_geometry_and_auxiliary_gradients_are_finite_for_both_controls():
    data, labels = year_anchors(), torch.tensor([0., 1., 0., 1.])
    for method in ("semantic_memory", "scope_relational"):
        model = YearScopeComposer(method, 11)
        model.configure_auxiliary(data, labels, [1, 2, 3, 4])
        model.memory_cache({})
        point = pack(1)
        for role, nodes in enumerate(point["nodes"][1:], 1):
            nodes[:, 112:] = 0
            nodes[:, 112 + 96 + min(role, 3)] = 1
            nodes[:, 112 + 100] = 1
        field, pairs, _ = model(point)
        loss = field.square().mean() + sum(p.square().mean() for p in pairs)
        loss = loss + model.auxiliary_loss(data, labels, torch.tensor([1, 2, 3, 4]))
        loss.backward()
        assert torch.isfinite(loss)
        assert all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters())
        assert all(p.grad is not None for p in model.geometry.period.parameters())
        model.clear_auxiliary_cache()


def test_unknown_anchor_is_neutral_and_auxiliary_labels_are_not_checkpoint_buffers():
    model = YearScopeComposer("scope_relational", 11)
    data = year_anchors()
    model.configure_auxiliary(data, torch.tensor([0., 1., 0., 1.]), [1, 2, 3, 4])
    data[:, 100] = 0
    assert torch.equal(model.geometry(data), torch.zeros(4))
    assert not any("bank_inputs" in key or "bank_labels" in key or "bank_case_ids" in key for key in model.state_dict())


def test_missing_common_link_bank_cannot_silently_weaken_any_control():
    for method in ("generic_relational", "semantic_memory", "scope_relational"):
        model = YearScopeComposer(method, 11)
        with pytest.raises(ValueError, match="auxiliary bank"):
            model(pack(1))
        with pytest.raises(ValueError, match="auxiliary bank"):
            model.memory_cache({})


def test_year_geometry_does_not_silently_replace_unit_or_field_geometry():
    model = YearScopeComposer("scope_relational", 11)
    data, labels = year_anchors(), torch.tensor([0., 1., 0., 1.])
    model.configure_auxiliary(data, labels, [1, 2, 3, 4])
    data[:, 97] = 0
    data[:, 98] = 1
    model.geometry.fallback_scale.data.fill_(1)
    before = model.geometry(data).detach()
    with torch.no_grad():
        for parameter in model.geometry.period.parameters():
            parameter.add_(100)
    assert torch.equal(before, model.geometry(data).detach())


def test_configured_feedback_bank_cannot_change_when_callers_mutate_their_tensors():
    memory = CommonYearMemory()
    data, labels, identities = year_anchors(), torch.tensor([0., 1., 0., 1.]), torch.tensor([1, 2, 3, 4])
    memory.configure(data, labels, identities)
    data.zero_()
    labels.fill_(9)
    identities.fill_(1)
    assert (memory.bank_inputs[:, 100] == 1).all()
    assert torch.equal(memory.bank_labels, torch.tensor([0., 1., 0., 1.]))
    assert torch.equal(memory.bank_case_ids, torch.tensor([1, 2, 3, 4]))


def test_head_only_ablation_keeps_the_identical_common_year_memory_and_parameters():
    model = YearScopeComposer("scope_relational", 11)
    data, labels = year_anchors(), torch.tensor([0., 1., 0., 1.])
    model.configure_auxiliary(data, labels, [1, 2, 3, 4])
    model.geometry.memory.strength.data.fill_(1)
    model.eval()
    before = {key: value.clone() for key, value in model.state_dict().items()}
    with torch.no_grad():
        full = model.geometry(data)
        head = model.geometry.period(data)
        model.geometry.period_head_off = True
        off = model.geometry(data)
        assert torch.allclose(full - off, torch.nn.functional.softplus(model.geometry.raw_period_scale) * head, atol=1e-6)
    assert all(torch.equal(before[k], v) for k, v in model.state_dict().items())
    model.train()
    with pytest.raises(ValueError, match="inference ablation"):
        model.geometry(data)

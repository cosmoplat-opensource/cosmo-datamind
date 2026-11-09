import sys
from pathlib import Path

import torch
from torch.nn import functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_year_scope_v24 import VisualYearScope, log_one_minus_exp


def anchors():
    torch.manual_seed(19)
    result = torch.randn(4, 112) * .1
    result[:, 80:84] = torch.tensor([.4, .1, .5, .12])
    result[:, 84:88] = torch.tensor([.4, .3, .5, .32])
    result[:, 100] = 1
    return result


def test_range_proposal_never_depends_on_target_coordinates_or_matching_flags():
    model = VisualYearScope()
    first = anchors()
    changed = first.clone()
    changed[:, 84:96] = 10
    changed[:, 100:] = -7
    assert all(torch.equal(a, b) for a, b in zip(model.propose(first), model.propose(changed), strict=True))


def test_weak_membership_orientation_is_positive_inside_and_lower_outside():
    model = VisualYearScope()
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.rectangle.bias[2:] = -1
        model.confidence.bias.fill_(3)
    observed = anchors()
    observed[:, 84:88] = observed[:, 80:84]
    inside = model(observed)
    observed[:, 84:88] += 2
    outside = model(observed)
    assert (inside > 0).all() and (outside < inside).all()


def test_both_binary_labels_produce_finite_gradients_and_missing_evidence_is_neutral():
    model = VisualYearScope()
    observed = anchors()
    labels = torch.tensor([1., 0., 1., 0.])
    loss = F.binary_cross_entropy_with_logits(model(observed), labels)
    loss.backward()
    assert torch.isfinite(loss)
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
    observed[:, 100] = 0
    assert torch.equal(model(observed), torch.zeros(4))


def test_log_complement_remains_finite_near_zero_and_far_below_zero():
    x = torch.tensor([-1e-12, -1e-7, -.1, -1., -10000.], requires_grad=True)
    actual = log_one_minus_exp(x)
    assert torch.isfinite(actual).all()
    assert torch.allclose(torch.exp(actual) + torch.exp(x), torch.ones_like(x))
    actual.sum().backward()
    assert torch.isfinite(x.grad).all()

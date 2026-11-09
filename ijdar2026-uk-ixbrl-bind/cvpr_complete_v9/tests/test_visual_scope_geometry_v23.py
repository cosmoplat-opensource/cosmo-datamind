import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_scope_geometry_v23 import (
    GenericVisualGeometry,
    VisualScopeGeometry,
    soft_interval_log_density,
)


def observations():
    anchors = torch.zeros(2, 112)
    anchors[:, 80:84] = torch.tensor([.1, .1, .2, .2])
    anchors[0, 84:88] = torch.tensor([.1, .1, .2, .2])
    anchors[1, 84:88] = torch.tensor([.8, .8, .9, .9])
    anchors[:, 97] = anchors[:, 100] = 1
    return anchors


def test_scope_proposal_is_invariant_to_the_queried_numbers_location():
    model = VisualScopeGeometry()
    with torch.no_grad():
        model.rectangle.weight.zero_()
        model.rectangle.bias.copy_(torch.tensor([0., 0., -1.5, -1.5]))
    anchors = observations()
    a = model.propose(anchors)
    changed = anchors.clone()
    changed[:, 84:96] = torch.randn(2, 12)  # target coordinates and all relative offsets
    changed[:, 100:104] = torch.randn(2, 4)  # observation/matching masks do not define the region
    b = model.propose(changed)
    assert all(torch.equal(x, y) for x, y in zip(a, b, strict=True))
    assert all(torch.equal(v[0], v[1]) for v in a)
    assert model(anchors)[0] > model(anchors)[1]


def test_unknown_location_cannot_become_a_certified_negative_edge():
    anchors = observations()
    anchors[:, 100] = 0
    for model in (VisualScopeGeometry(), GenericVisualGeometry()):
        assert torch.equal(model(anchors), torch.zeros(2))


def test_both_geometry_controls_learn_from_finite_gradients_and_matched_parameters():
    counts = []
    for model in (VisualScopeGeometry(), GenericVisualGeometry()):
        counts.append(sum(p.numel() for p in model.parameters()))
        scores = model(observations())
        loss = -torch.log_softmax(scores, 0)[0]
        loss.backward()
        assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
    assert max(counts) / min(counts) < 1.01


def test_spatial_factor_is_normalized_and_does_not_reward_infinite_extent():
    points = torch.linspace(-8, 8, 20001, dtype=torch.float64)
    centers = torch.zeros_like(points)
    density = soft_interval_log_density(points, centers, torch.full_like(points, .2)).exp()
    assert torch.allclose(torch.trapezoid(density, points), torch.tensor(1., dtype=torch.float64), atol=1e-6)
    near = torch.tensor([0.], dtype=torch.float64)
    assert soft_interval_log_density(near, near, torch.tensor([.2])) > soft_interval_log_density(near, near, torch.tensor([20.]))

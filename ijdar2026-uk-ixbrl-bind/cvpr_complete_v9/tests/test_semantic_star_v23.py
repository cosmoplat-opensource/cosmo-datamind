import itertools
import math
import sys
from pathlib import Path

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semantic_star_v23 import (
    grouped_logmeanexp,
    log_partition,
    map_semantic_binding,
    partial_semantic_nll,
    selected_witness_posterior,
    semantic_potentials,
)


def brute_scores(field, leaves):
    states = list(itertools.product(range(len(field)), *(range(v.shape[1]) for v in leaves)))
    return states, torch.stack([field[f] + sum(v[f, j] for v, j in zip(leaves, (p, d, q), strict=True)) for f, p, d, q in states])


def test_exact_partition_gradient_and_map_equal_cartesian_enumeration():
    torch.manual_seed(19)
    f = torch.randn(3, dtype=torch.float64, requires_grad=True)
    pairs = [torch.randn(3, n, dtype=torch.float64, requires_grad=True) for n in (4, 5, 2)]
    states, scores = brute_scores(f, pairs)
    direct, dynamic = torch.logsumexp(scores, 0), log_partition(f, pairs)
    assert torch.allclose(direct, dynamic, atol=1e-12)
    a = torch.autograd.grad(direct, (f, *pairs), retain_graph=True)
    b = torch.autograd.grad(dynamic, (f, *pairs))
    assert all(torch.allclose(x, y, atol=1e-12) for x, y in zip(a, b, strict=True))
    assert map_semantic_binding(f, pairs) == list(states[int(scores.argmax())])


def test_duplicate_visual_paths_do_not_change_semantic_probability():
    values = torch.tensor([[1.0, 3.0, -2.0], [.1, .3, -.2]])
    expected = grouped_logmeanexp(values, [0, 0, 1])
    duplicated = values[:, [0, 1, 0, 1, 2]]
    assert torch.allclose(expected, grouped_logmeanexp(duplicated, [0, 0, 0, 0, 1]))
    assert expected[0, 0] == pytest.approx(float(torch.logsumexp(values[0, :2], 0)) - math.log(2))


def test_semantic_map_marginalizes_occurrences_instead_of_selecting_best_one():
    f = torch.zeros(1)
    unary = [torch.tensor([4.0, -8.0, 3.5]), torch.zeros(1), torch.zeros(1)]
    pairs = [torch.zeros(1, len(v)) for v in unary]
    groups = [[0, 0, 1], [0], [0]]
    field, leaves = semantic_potentials(f, unary, pairs, groups)
    assert map_semantic_binding(field, leaves) == [0, 1, 0, 0]
    indices, posterior = selected_witness_posterior(unary[0], pairs[0], groups[0], 0, 0)
    assert indices.tolist() == [0, 1]
    assert posterior.sum().item() == pytest.approx(1)


def test_partial_feedback_preserves_missing_teacher_component_as_unobserved():
    f = torch.tensor([.2, -.3], requires_grad=True)
    leaves = [torch.tensor([[.1, .4], [.3, .5]], requires_grad=True) for _ in range(3)]
    missing = torch.zeros(2, dtype=torch.bool)
    known = torch.tensor([True, False])
    nll = partial_semantic_nll(f, leaves, [missing, known, missing, missing])
    exact = log_partition(f, leaves) - log_partition(f, leaves, [None, known, None, None])
    assert torch.allclose(nll, exact)
    assert partial_semantic_nll(f, leaves, [missing] * 4) is None
    nll.backward()
    assert torch.isfinite(f.grad).all()


def test_invalid_or_empty_group_inventories_are_rejected():
    with pytest.raises(ValueError):
        grouped_logmeanexp(torch.zeros(2), [0, 2])
    with pytest.raises(ValueError):
        grouped_logmeanexp(torch.zeros(0), [])

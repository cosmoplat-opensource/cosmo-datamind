import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_scope_composition_v23 import METHODS, ScopeComposer, collapse_witnesses
from native_witness_data_v23 import component_prior
from semantic_star_v23 import grouped_logmeanexp, partial_semantic_nll


def pack(seed=1):
    rng = torch.Generator().manual_seed(seed)
    groups = [torch.tensor([0, 0, 1]), torch.tensor([0, 1]), torch.tensor([0, 1, 1])]
    nodes = [torch.randn(3, 112, generator=rng)] + [torch.randn(len(g), 224, generator=rng) for g in groups]
    pool = torch.randn(2, 112, generator=rng)
    pool[:, 100] = 1
    for node in nodes[1:]:
        node[:, 212] = 1
    return {"context": torch.randn(11, generator=rng), "nodes": nodes, "pool": pool,
            "groups": groups, "group_counts": [torch.bincount(g) for g in groups],
            "masks": [torch.tensor([True, False, False])] + [torch.tensor([True, False])] * 3,
            "prior": [torch.zeros(len(n)) for n in nodes],
            "relations": [torch.randn(3, 2, 2, generator=rng) for _ in range(3)],
            "pair_prior": [torch.zeros(3, 2) for _ in range(3)]}


def test_vectorized_witness_marginalization_has_exact_values_and_gradients():
    x = torch.randn(4, 7, dtype=torch.float64, requires_grad=True)
    groups = torch.tensor([0, 1, 1, 2, 2, 2, 3])
    a = grouped_logmeanexp(x, groups)
    b = collapse_witnesses(x, groups, torch.bincount(groups))
    assert torch.allclose(a, b, atol=1e-12)
    assert torch.allclose(torch.autograd.grad(a.sum(), x, retain_graph=True)[0], torch.autograd.grad(b.sum(), x)[0], atol=1e-12)


def test_all_initial_models_have_the_same_prior_and_matched_active_parameters():
    item = pack()
    counts = []
    for method in METHODS:
        model = ScopeComposer(method, 11)
        counts.append(sum(p.numel() for p in model.parameters()))
        cache = model.memory_cache({1: pack(2), 2: pack(3)})
        f, leaves, _ = model(item, cache)
        assert torch.allclose(f, torch.zeros_like(f), atol=1e-6)
        assert all(torch.allclose(v, torch.zeros_like(v), atol=1e-6) for v in leaves)
    # Real experiment has a 1156-dimensional shared context. No dummy weights.
    counts = [sum(p.numel() for p in ScopeComposer(method, 1156).parameters()) for method in METHODS]
    assert max(counts) / min(counts) < 1.01


def test_role_specific_memory_skips_missing_labels_and_excludes_the_query():
    model = ScopeComposer("semantic_memory", 11)
    bank = {0: pack(0), 1: pack(1), 2: pack(2)}
    bank[1]["masks"][1] = torch.zeros(2, dtype=torch.bool)
    cache = model.memory_cache(bank)
    query = torch.tanh(model.context(bank[0]["context"]))
    selected, _ = model.retrieve(cache, bank[0]["context"], query, (0, 1), exclude=0)
    assert [cache["indices"][j] for j in selected] == [2]


def test_full_composition_and_memory_receive_finite_training_gradients():
    torch.set_num_threads(2)
    item = pack()
    for method in METHODS:
        model = ScopeComposer(method, 11)
        optimizer = torch.optim.AdamW(model.parameters(), lr=.001)
        for _ in range(3):
            optimizer.zero_grad()
            cache = model.memory_cache({1: pack(2), 2: pack(3)})
            f, leaves, _ = model(item, cache)
            loss = partial_semantic_nll(f, leaves, item["masks"])
            loss.backward()
            assert torch.isfinite(loss)
            assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
            optimizer.step()


def test_unknown_semantic_period_is_not_assumed_to_be_the_last_group():
    nodes = np.zeros((3, 224), dtype="float32")
    nodes[1:, 78] = 1
    groups = [{"value": None}, {"value": {"kind": "instant", "instant": "2023-12-31"}}]
    prior = component_prior({}, "period", nodes, groups, np.array([0, 1, 1]))
    assert np.allclose(prior.numpy(), [-.75, 0, 0])

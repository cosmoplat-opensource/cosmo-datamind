"""Exact composition shared by ontology, generic and memory controls.

Field is the central variable. Period, dimension and unit/quantity witnesses
are marginalized within semantic values before selecting a complete binding.
An occurrence remains a hypothesis: its posterior does not certify ownership.
"""

import math

import torch


def grouped_logmeanexp(values, groups):
    """Marginalize alternative witnesses without rewarding duplicate paths.

    The last axis indexes witnesses. Group IDs are contiguous semantic values.
    An identical copy of every witness in a group leaves its score unchanged.
    """
    if values.ndim < 1 or values.shape[-1] != len(groups) or not len(groups):
        raise ValueError("A nonempty aligned witness inventory is required")
    groups = torch.as_tensor(groups, dtype=torch.long, device=values.device)
    if groups.ndim != 1 or groups.min() < 0:
        raise ValueError("Nonnegative one-dimensional group IDs required")
    count = int(groups.max()) + 1
    if not torch.equal(torch.unique(groups), torch.arange(count, device=groups.device)):
        raise ValueError("Semantic group IDs must be contiguous and nonempty")
    return torch.stack([
        torch.logsumexp(values[..., groups == k], dim=-1) - math.log(int((groups == k).sum()))
        for k in range(count)
    ], dim=-1)


def semantic_potentials(field_unary, leaf_unary, pair_scores, witness_groups):
    """Sum out each leaf's latent visual occurrence conditional on field.

    All controls supply the same tree, witnesses, groups and uncertainty states.
    No top-k pruning or ontology-specific rejection occurs in this engine.
    """
    if field_unary.ndim != 1 or not len(field_unary):
        raise ValueError("Nonempty field scores required")
    if not (len(leaf_unary) == len(pair_scores) == len(witness_groups) == 3):
        raise ValueError("Exactly period, dimension and unit/quantity leaves required")
    result = []
    for unary, pairs, groups in zip(leaf_unary, pair_scores, witness_groups, strict=True):
        if unary.ndim != 1 or pairs.shape != (len(field_unary), len(unary)):
            raise ValueError("Leaf and field/leaf factor dimensions differ")
        result.append(grouped_logmeanexp(pairs + unary[None, :], groups))
    return field_unary, result


def log_partition(field_unary, semantic_pairs, known_masks=None):
    """Exact sum-product with optional known semantic-value constraints."""
    if len(semantic_pairs) != 3 or field_unary.ndim != 1:
        raise ValueError("A field-centered three-leaf graph is required")
    if known_masks is None:
        known_masks = [None] * 4
    if len(known_masks) != 4:
        raise ValueError("Four aligned partial-teacher masks required")
    field = field_unary
    if known_masks[0] is not None:
        if known_masks[0].shape != field.shape or known_masks[0].dtype != torch.bool:
            raise ValueError("Invalid field mask")
        field = field.masked_fill(~known_masks[0], -torch.inf)
    for pair, mask in zip(semantic_pairs, known_masks[1:], strict=True):
        if pair.ndim != 2 or pair.shape[0] != len(field):
            raise ValueError("Invalid semantic factor dimensions")
        if mask is not None:
            if mask.dtype != torch.bool or mask.shape != pair.shape[1:]:
                raise ValueError("Invalid leaf mask")
            pair = pair.masked_fill(~mask[None, :], -torch.inf)
        field = field + torch.logsumexp(pair, dim=1)
    return torch.logsumexp(field, dim=0)


def partial_semantic_nll(field_unary, semantic_pairs, masks):
    """Unrepresentable teacher components are unobserved, never null labels."""
    if len(masks) != 4:
        raise ValueError("Four partial component masks required")
    available = [mask if mask is not None and bool(mask.any()) else None for mask in masks]
    if not any(mask is not None for mask in available):
        return None
    return log_partition(field_unary, semantic_pairs) - log_partition(field_unary, semantic_pairs, available)


def map_semantic_binding(field_unary, semantic_pairs):
    """Exact max-product over semantic values after witness marginalization."""
    if len(semantic_pairs) != 3 or any(pair.shape[0] != len(field_unary) for pair in semantic_pairs):
        raise ValueError("Aligned semantic factors required")
    maxima = [pair.max(dim=1) for pair in semantic_pairs]
    selected_field = int((field_unary + sum(value.values for value in maxima)).argmax())
    return [selected_field] + [int(value.indices[selected_field]) for value in maxima]


def selected_witness_posterior(unary, pairs, groups, field_index, semantic_index):
    """Retain all matching occurrences; return uncertainty without certification."""
    groups = torch.as_tensor(groups, dtype=torch.long, device=unary.device)
    chosen = torch.nonzero(groups == semantic_index, as_tuple=True)[0]
    if not len(chosen) or pairs.ndim != 2 or pairs.shape[1] != len(unary):
        raise ValueError("Selected semantic value has no aligned witnesses")
    probabilities = torch.softmax(unary[chosen] + pairs[field_index, chosen], dim=0)
    return chosen, probabilities

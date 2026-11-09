"""Full binding with common weak-year memory and an oriented period head.

Every control receives the same labelled link bank and retrieval mechanism.
Only the scoped period head proposes target-independent rectangular ranges;
the generic head can condition directly on the target. Other component roles
retain the same unrestricted geometry model. Nothing certifies ownership.
"""

import math

import torch
from native_scope_composition_v23 import METHODS, ScopeComposer
from torch import nn
from torch.nn import functional as F
from visual_scope_geometry_v23 import GenericVisualGeometry
from visual_year_scope_v24 import VisualYearScope


class CommonYearMemory(nn.Module):
    """Keep every observed weak case; hybrid retrieval may use its binary label."""

    def __init__(self):
        super().__init__()
        self.encode = nn.Linear(104, 32)
        self.strength = nn.Parameter(torch.zeros(()))
        self.register_buffer("bank_inputs", torch.empty(0, 112), persistent=False)
        self.register_buffer("bank_labels", torch.empty(0), persistent=False)
        self.register_buffer("bank_case_ids", torch.empty(0, dtype=torch.long), persistent=False)
        self._cache = None

    def configure(self, anchors, labels, case_ids):
        if anchors.ndim != 2 or not len(anchors) or anchors.shape[1] != 112 or labels.shape != (len(anchors),) or len(case_ids) != len(anchors):
            raise ValueError("Complete aligned historical year-link bank required")
        if not torch.isfinite(anchors).all() or not torch.isin(labels, labels.new_tensor([0, 1])).all():
            raise ValueError("Invalid observed year-link features or labels")
        identities = torch.as_tensor(case_ids, dtype=torch.long, device=anchors.device)
        if (not (anchors[:, 97] == 1).all() or not (anchors[:, 100] == 1).all()
                or identities.ndim != 1 or len(identities.unique()) != len(anchors) or (identities < 0).any()):
            raise ValueError("Observed period glyphs and unique historical case IDs required")
        self.bank_inputs = anchors.detach().clone()
        self.bank_labels = labels.detach().clone()
        self.bank_case_ids = identities.clone()
        self._cache = None

    def prepare_cache(self):
        if len(self.bank_inputs):
            self._cache = (F.normalize(self.bank_inputs[:, :104], dim=1),
                           F.normalize(torch.tanh(self.encode(self.bank_inputs[:, :104])), dim=1))
        else:
            self._cache = None

    def clear_cache(self):
        self._cache = None

    def forward(self, anchors, exclude_case_ids=None):
        if not len(self.bank_inputs):
            return anchors.new_zeros(len(anchors))
        if self._cache is None:
            self.prepare_cache()
        raw_bank, encoded_bank = self._cache
        raw = F.normalize(anchors[:, :104], dim=1) @ raw_bank.T
        encoded = F.normalize(torch.tanh(self.encode(anchors[:, :104])), dim=1) @ encoded_bank.T
        if exclude_case_ids is not None and len(exclude_case_ids) != len(anchors):
            raise ValueError("One exclusion case ID is required for every auxiliary query")
        scores = []
        for i in range(len(anchors)):
            eligible = torch.ones(len(self.bank_inputs), dtype=torch.bool, device=anchors.device)
            if exclude_case_ids is not None:
                eligible &= self.bank_case_ids != exclude_case_ids[i]
            candidates = eligible.nonzero().flatten()
            if not len(candidates):
                scores.append(anchors.new_zeros(()))
                continue
            raw_order = candidates[torch.argsort(raw[i, candidates], descending=True, stable=True)].tolist()
            learned_order = candidates[torch.argsort(encoded[i, candidates], descending=True, stable=True)].tolist()
            selected = raw_order[:4]
            selected += [j for j in learned_order if j not in selected][:8 - len(selected)]
            weights = torch.softmax(raw[i, selected] + encoded[i, selected], dim=0)
            # Equal fixed smoothing prevents a one-class neighborhood from
            # producing an infinite logit. Both controls use this full bank.
            probability = ((weights * self.bank_labels[selected]).sum() + .05) / 1.1
            scores.append(torch.log(probability) - torch.log1p(-probability))
        return torch.stack(scores) * self.strength


class PeriodGeometry(nn.Module):
    def __init__(self, scoped, fallback):
        super().__init__()
        self.fallback = fallback
        self.memory = CommonYearMemory()
        self.period = VisualYearScope() if scoped else GenericVisualGeometry()
        self.raw_period_scale = nn.Parameter(torch.tensor(math.log(math.expm1(.1))))
        self.fallback_scale = nn.Parameter(torch.zeros(()))
        self.period_head_off = False

    def auxiliary_logits(self, anchors, exclude_case_ids=None):
        direct = self.period(anchors)
        return direct, direct + self.memory(anchors, exclude_case_ids)

    def forward(self, anchors):
        if self.period_head_off and self.training:
            raise ValueError("Period-head zeroing is a weight-tied inference ablation")
        period = (anchors[:, 97] == 1) & (anchors[:, 100] == 1)
        # Year supervision does not silently become field/unit supervision.
        result = self.fallback(anchors) * self.fallback_scale * (~period)
        if period.any():
            selected = anchors[period]
            direct = selected.new_zeros(len(selected)) if self.period_head_off else self.period(selected)
            score = direct + self.memory(selected)
            result = result.index_add(0, period.nonzero().flatten(), score * F.softplus(self.raw_period_scale))
        return result


class YearScopeComposer(ScopeComposer):
    def __init__(self, method, context_dimension):
        if method not in METHODS:
            raise ValueError("Unregistered full-binding year-link control")
        # Scope and strong memory begin with identical shared parameters and
        # fallback geometry. Their different period heads are created last.
        super().__init__("semantic_memory" if method == "scope_relational" else method, context_dimension)
        self.method = method
        self.geometry = PeriodGeometry(method == "scope_relational", self.geometry)
        del self.geometry_scale
        self.register_buffer("geometry_scale", torch.ones(()))

    def configure_auxiliary(self, anchors, labels, case_ids):
        self.geometry.memory.configure(anchors, labels, case_ids)

    def clear_auxiliary_cache(self):
        self.geometry.memory.clear_cache()

    def memory_cache(self, bank):
        self.require_auxiliary_bank()
        self.geometry.memory.prepare_cache()
        return super().memory_cache(bank)

    def require_auxiliary_bank(self):
        if not len(self.geometry.memory.bank_inputs):
            raise ValueError("The complete other-company auxiliary bank must be configured before full binding")

    def forward(self, pack, cache=None, exclude=None, geometry_off=False):
        self.require_auxiliary_bank()
        return super().forward(pack, cache, exclude, geometry_off)

    def auxiliary_logits(self, anchors, exclude_case_ids=None):
        return self.geometry.auxiliary_logits(anchors, exclude_case_ids)

    def auxiliary_loss(self, anchors, labels, exclude_case_ids):
        direct, remembered = self.auxiliary_logits(anchors, exclude_case_ids)
        # Direct supervision keeps the range/MLP head observable even if
        # retrieving known weak cases already solves the auxiliary task.
        return .5 * (F.binary_cross_entropy_with_logits(direct, labels)
                     + F.binary_cross_entropy_with_logits(remembered, labels))

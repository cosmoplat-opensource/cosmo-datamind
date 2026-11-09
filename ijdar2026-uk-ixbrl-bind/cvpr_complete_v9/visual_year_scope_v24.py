"""A header-proposed rectangle scored as weak year-link membership.

The target position evaluates a proposal but never generates its bounds.
This is a binary auxiliary training head, not certified financial ownership.
It replaces spatial density with an oriented membership logit, so correct
positive year links and incompatible negatives have explicit supervision.
"""

import math

import torch
from torch import nn
from torch.nn import functional as F


def log_one_minus_exp(log_probability):
    """Stable log(1-exp(x)) for a log probability strictly below zero."""
    if (log_probability >= 0).any():
        raise ValueError("Log probability must be strictly negative")
    small = log_probability < -math.log(2)
    result = torch.empty_like(log_probability)
    result[small] = torch.log1p(-torch.exp(log_probability[small]))
    result[~small] = torch.log(-torch.expm1(log_probability[~small]))
    return result


class VisualYearScope(nn.Module):
    def __init__(self):
        super().__init__()
        self.header = nn.Linear(92, 64)
        self.rectangle = nn.Linear(64, 4)
        self.confidence = nn.Linear(64, 1)

    def propose(self, anchors):
        if anchors.ndim != 2 or anchors.shape[1] != 112:
            raise ValueError("Common 112-dimensional anchor observations required")
        box = anchors[:, 80:84]
        header_geometry = torch.cat(((box[:, :2] + box[:, 2:]) / 2, box[:, 2:] - box[:, :2]), 1)
        inputs = torch.cat((anchors[:, :84], anchors[:, 96:100], header_geometry), 1)
        hidden = torch.tanh(self.header(inputs))
        raw = self.rectangle(hidden)
        center = (box[:, :2] + box[:, 2:]) / 2 + torch.tanh(raw[:, :2])
        half_extent = F.softplus(raw[:, 2:]) + 1e-4
        return center, half_extent, self.confidence(hidden).flatten()

    def forward(self, anchors):
        center, extent, confidence = self.propose(anchors)
        target = (anchors[:, 84:86] + anchors[:, 86:88]) / 2
        temperature = .01 + .05 * extent
        log_membership = F.logsigmoid((extent - (target - center).abs()) / temperature).sum(1)
        log_probability = F.logsigmoid(confidence) + log_membership
        # Round-off at saturation must not create an infinite positive logit.
        log_probability = torch.minimum(log_probability, log_probability.new_full((), -1e-7))
        logits = log_probability - log_one_minus_exp(log_probability)
        return logits * anchors[:, 100]

"""Learnable range hypotheses and a matched unrestricted geometry control.

Scope bounds depend on a visible header's original pixels/text/position/role.
The queried number's position is used only to evaluate the proposed range.
These weakly learned proposals do not certify actual header ownership.
Both modules consume the same common 112-dimensional anchor observations.
"""

import torch
from torch import nn
from torch.nn import functional as F


def soft_interval_log_density(coordinates, centers, half_extent):
    """Normalized smooth interval density over the real line, not ownership.

    Integral sigmoid((h - abs(x-c))/t) dx = 2*t*softplus(h/t).
    Normalizing prevents an arbitrarily large rectangle from gaining merely
    because it covers every query. Unknown evidence has neutral log factor 0.
    """
    temperature = .05 + half_extent / 4
    log_mass = torch.log(2 * temperature * F.softplus(half_extent / temperature))
    return F.logsigmoid((half_extent - (coordinates - centers).abs()) / temperature) - log_mass


class VisualScopeGeometry(nn.Module):
    def __init__(self):
        super().__init__()
        # 80 glyph features + 4 absolute header coordinates + 4 role flags +
        # 4 derived header coordinates. No per-query matching flags enter.
        self.header = nn.Linear(92, 64)
        self.rectangle = nn.Linear(64, 4)
        self.strength = nn.Linear(64, 1)

    def propose(self, anchors):
        if anchors.ndim != 2 or anchors.shape[1] != 112:
            raise ValueError("Common 112-dimensional anchor observations required")
        box = anchors[:, 80:84]
        header_geometry = torch.cat(((box[:, :2] + box[:, 2:]) / 2, box[:, 2:] - box[:, :2]), dim=1)
        inputs = torch.cat((anchors[:, :84], anchors[:, 96:100], header_geometry), dim=1)
        header = torch.tanh(self.header(inputs))
        raw = self.rectangle(header)
        center = (box[:, :2] + box[:, 2:]) / 2 + torch.tanh(raw[:, :2])
        half_extent = F.softplus(raw[:, 2:]) + 1e-4
        strength = F.softplus(self.strength(header).flatten())
        return center, half_extent, strength

    def forward(self, anchors):
        center, half_extent, strength = self.propose(anchors)
        target = anchors[:, 84:88]
        target_center = (target[:, :2] + target[:, 2:]) / 2
        # A spatial density feature, not a calibrated probability of ownership.
        # It is finite outside the interval and never rejects a semantic value.
        log_density = soft_interval_log_density(target_center, center, half_extent).sum(1)
        return log_density * strength * anchors[:, 100]


class GenericVisualGeometry(nn.Module):
    """Unrestricted target-conditioned MLP with a matched active parameter budget."""

    def __init__(self):
        super().__init__()
        self.hidden = nn.Linear(104, 59)
        self.output = nn.Linear(59, 1)

    def forward(self, anchors):
        if anchors.ndim != 2 or anchors.shape[1] != 112:
            raise ValueError("Common 112-dimensional anchor observations required")
        # The final eight coordinates are padding, not dummy trainable inputs.
        return self.output(torch.tanh(self.hidden(anchors[:, :104]))).flatten() * anchors[:, 100]

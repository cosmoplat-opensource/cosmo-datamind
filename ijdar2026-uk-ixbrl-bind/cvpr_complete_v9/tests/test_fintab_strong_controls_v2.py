import sys
from pathlib import Path

import numpy as np
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fintab_scope_strong_controls_v2 import best_horizontal_span
from fintab_scope_train_v10 import CoverageModel, HIDDEN


def test_optimal_span_keeps_internal_negative_when_total_support_wins():
    boxes = np.array([[x, .1, x+.01, .2] for x in (.1, .2, .3, .4, .5)])
    assert best_horizontal_span(boxes, np.array([-2, 3, -1, 4, -2])) == [1, 2, 3]
    assert best_horizontal_span(boxes, np.array([-1, -1, -1, -1, -1])) == []
    assert best_horizontal_span(np.empty((0, 4)), np.array([])) == []


def test_capacity_matching_uses_active_readout_parameters():
    generic = CoverageModel("generic_joint")
    generic.pair = nn.Sequential(nn.Linear(2*HIDDEN+12, 111), nn.GELU(), nn.Linear(111, 1))
    scope = CoverageModel("learned_rectangle_scope")
    counts = [sum(p.numel() for p in m.parameters()) for m in (generic, scope)]
    assert abs(counts[0] - counts[1]) / counts[1] < .001
    assert generic.range is None
    assert scope.range is not None

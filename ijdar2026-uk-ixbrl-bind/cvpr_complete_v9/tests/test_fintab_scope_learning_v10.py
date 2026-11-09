import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fintab_scope_train_v10 import CoverageModel, inside_score, predict, rectangle_projection, training_labels


def test_topology_teacher_excludes_unknown_and_partial_spans():
    cells = [
        {"row_nums": [0], "column_nums": [1], "is_column_header": True, "is_projected_row_header": False},
        {"row_nums": [1], "column_nums": [1], "is_column_header": False, "is_projected_row_header": False},
        {"row_nums": [1], "column_nums": [1, 2], "is_column_header": False, "is_projected_row_header": False},
    ]
    hdr, pos, neg = training_labels({"word_cell_ids": [0, 1, 2, None], "cells": cells})
    assert hdr.tolist() == [1, 0, 0, -1]
    assert pos.tolist() == [[0, 1]]
    assert [0, 2] not in neg.tolist()
    assert all(3 not in pair for pair in pos.tolist() + neg.tolist())


def test_learned_scope_has_valid_ranges_and_same_public_inputs():
    torch.set_num_threads(1)
    model = CoverageModel("learned_rectangle_scope")
    x = torch.randn(6, 1156)
    x[:, -4:] = torch.tensor([[.1, .1, .2, .2], [.3, .1, .4, .2],
                             [.1, .4, .2, .5], [.3, .4, .4, .5],
                             [.1, .7, .2, .8], [.3, .7, .4, .8]])
    nodes = model.nodes(x)
    boxes = model.rectangles(nodes)
    assert ((boxes >= 0) & (boxes <= 1)).all()
    assert (boxes[:, :2] < boxes[:, 2:]).all()
    result = predict(model, x)
    assert set(result) == {"header_words", "coverage_edges", "generic_rectangle_decoder_edges"}
    assert all(h < len(x) and t < len(x) for h, t in result["coverage_edges"])


def test_same_rectangle_decoder_does_not_use_labels():
    boxes = np.array([[.1, .2, .2, .3], [.3, .4, .4, .5], [.5, .6, .6, .7]])
    assert rectangle_projection(boxes, []) == []
    assert rectangle_projection(boxes, [0, 2]) == [0, 1, 2]
    scores = inside_score(torch.tensor([[.1, .1, .8, .8], [.1, .1, .8, .8]]),
                          torch.tensor([[.2, .2, .3, .3], [.9, .9, 1., 1.]]))
    assert scores[0] > 0 and scores[1] < 0

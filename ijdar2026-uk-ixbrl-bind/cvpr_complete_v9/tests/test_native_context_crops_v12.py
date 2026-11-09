import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_context_crops_v12 import context_boxes, pixel_box


def test_uniform_contexts_contain_target_and_keep_header_observation_window():
    target = [.7, .5, .72, .51]
    row, column = context_boxes(target)
    for box in (row, column):
        assert 0 <= box[0] <= target[0] < target[2] <= box[2] <= 1
        assert 0 <= box[1] <= target[1] < target[3] <= box[3] <= 1
    assert row[0] == 0 and row[2] == 1
    assert column[1] == 0
    assert pixel_box([.1, .2, .3, .4], 100, 100) == [10, 20, 30, 40]


def test_invalid_target_cannot_define_source_crop():
    with pytest.raises(ValueError):
        context_boxes([.8, .2, .7, .3])
    with pytest.raises(ValueError):
        context_boxes([-.1, .2, .7, .3])

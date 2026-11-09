import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_witness_features_v23 import anchor_geometry, anchor_vector


def test_missing_location_is_not_a_box_at_the_page_origin():
    target = [.4, .5, .6, .7]
    value = anchor_geometry(None, target)
    assert np.array_equal(value[4:8], np.array(target, dtype="float32"))
    assert not value[:4].any() and not value[8:].any()


def test_actual_ocr_anchor_features_and_unresolved_mask_are_separate():
    words = np.ones((3, 1156), dtype="float32")
    vision = np.ones((768, 32), dtype="float32")
    text = np.ones((384, 48), dtype="float32")
    anchor = {"word_indices": [0, 2], "box": [.1, .2, .3, .4], "full_header_text_aligned": True}
    observed = anchor_vector(anchor, "period", [.4, .5, .6, .7], words, vision, text)
    unresolved = anchor_vector(None, "period", [.4, .5, .6, .7], words, vision, text, unresolved=True)
    assert observed[100] == 1 and observed[101] == 0 and observed[102] == 0
    assert unresolved[100] == 0 and unresolved[101] == 1 and unresolved[102] == 1
    assert observed[:80].any() and not unresolved[:80].any()
    assert observed[97] == unresolved[97] == 1

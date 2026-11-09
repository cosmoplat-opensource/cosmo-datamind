import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_year_scope_features_v24 import labelled_anchor


def test_auxiliary_inputs_use_common_ocr_features_and_ignore_administrative_source_ids():
    words = np.ones((3, 1156), dtype="float32")
    visual = np.ones((768, 32), dtype="float32") / 768
    text = np.ones((384, 48), dtype="float32") / 384
    row = {"header_word_indices": [0], "target_word_indices": [1, 2],
           "header_box": [.4, .1, .5, .12], "target_box": [.4, .3, .5, .32],
           "company": "A", "source_year": 2023}
    result = labelled_anchor(row, words, visual, text)
    row.update(company="B", source_year=2099, source_dom_coordinates=[9, 9, 9, 9])
    assert np.array_equal(result, labelled_anchor(row, words, visual, text))
    assert result.shape == (112,)
    assert np.array_equal(result[80:84], np.asarray(row["header_box"], dtype="float32"))
    assert np.array_equal(result[84:88], np.asarray(row["target_box"], dtype="float32"))


def test_ambiguous_year_locations_are_not_averaged_into_a_known_auxiliary_label():
    row = {"header_word_indices": [0, 1], "target_word_indices": [2],
           "header_box": [.4, .1, .5, .12], "target_box": [.4, .3, .5, .32]}
    with pytest.raises(ValueError, match="one header glyph"):
        labelled_anchor(row, np.ones((3, 1156)), np.ones((768, 32)), np.ones((384, 48)))

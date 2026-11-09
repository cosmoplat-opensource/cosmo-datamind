import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_grid_ocr_witnesses_v24 import common_box, matching_words, pair_label


def test_repeated_year_words_are_all_retained_and_not_forced_to_nearest():
    words = [{"text": "2023", "box": [.1, .1, .2, .2]}, {"text": "2023", "box": [.11, .1, .21, .2]},
             {"text": "2022-2023", "box": [.1, .1, .2, .2]}]
    assert matching_words(words, [10, 10, 21, 20], 100, 100, "2023") == [0, 1]
    assert matching_words(words, None, 100, 100, "2023") == []


def test_year_token_needs_geometric_support_and_numeric_boundaries():
    words = [{"text": "2023", "box": [.7, .1, .8, .2]}, {"text": "12023", "box": [.1, .1, .2, .2]},
             {"text": "(2023)", "box": [.1, .1, .2, .2]}]
    assert matching_words(words, [10, 10, 20, 20], 100, 100, "2023") == [2]


def test_target_learning_box_is_union_of_common_ocr_words():
    words = [{"text": "1,", "box": [.1, .1, .15, .2]}, {"text": "234", "box": [.15, .1, .2, .2]},
             {"text": "Revenue", "box": [.1, .1, .2, .2]}]
    ids = matching_words(words, [10, 10, 20, 20], 100, 100)
    assert ids == [0, 1]
    assert common_box(words, ids) == [.1, .1, .2, .2]


def test_missing_edges_and_same_year_alternatives_remain_unknown():
    header = {"key": "h", "ocr_unique": True, "source_year_token": "2023", "source_block_consistent": True}
    target = {"key": "t", "ocr_observed": True, "source_year_token": "2023"}
    assert pair_label(header, target, {}) is None
    assert pair_label(header, target, {"t": {"another_2023_header"}}) is None
    assert pair_label(header, target, {"t": {"h"}}) == 1
    target["source_year_token"] = "2022"
    assert pair_label(header, target, {"t": {"another_2022_header"}}) == 0
    target["ocr_observed"] = False
    assert pair_label(header, target, {"t": {"h"}}) is None

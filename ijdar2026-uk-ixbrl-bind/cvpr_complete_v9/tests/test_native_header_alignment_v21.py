import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_header_alignment_v21 import align_header, target_words, tokens


def word(text, x, y=.1):
    return {"text": text, "box": [x, y, x+.04, y+.01]}


def test_date_localization_does_not_reuse_an_observation_window():
    words = [word("31", .1), word("December", .16), word("2022", .24)]
    result = align_header({"text": "31 December 2022 restated¹ £m", "kind": "period"}, words)
    assert result["status"] == "unique_lexical_location"
    assert result["anchors"][0]["box"] == pytest.approx([.1, .1, .28, .11])
    assert result["anchors"][0]["basis"] == "visible_date_substring"
    assert not result["scope_or_semantic_identity_certified"]


def test_repeated_units_remain_multiple_locations():
    result = align_header({"text": "£m", "kind": "unit"}, [word("£m", .1), word("£m", .5)])
    assert result["status"] == "ambiguous_lexical_locations"
    assert len(result["anchors"]) == 2


def test_missing_currency_is_not_repaired_and_unknown_is_not_matched():
    assert tokens("Restated¹ 2023") == ["restated", "2023"]
    result = align_header({"text": "£m", "kind": "unit"}, [word("Em", .1)])
    assert result["status"] == "unmatched_unknown"
    assert not align_header({"text": "unknown", "kind": "other"}, [word("unknown", .1)])["anchors"]


def test_pointed_words_are_selected_by_given_geometry_only():
    assert target_words([word("9", .1), word("2023", .5)], [.08, .09, .17, .12]) == [0]

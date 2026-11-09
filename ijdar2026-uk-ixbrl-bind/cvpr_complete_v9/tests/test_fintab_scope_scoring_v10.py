import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fintab_scope_score_v10 import aggregate, score


def reference():
    return {"word_cell_ids": [0, 1, 2, None], "cells": [
        {"row_nums": [0], "column_nums": [1], "is_column_header": True, "is_projected_row_header": False},
        {"row_nums": [1], "column_nums": [1], "is_column_header": False, "is_projected_row_header": False},
        {"row_nums": [1], "column_nums": [2], "is_column_header": False, "is_projected_row_header": False},
    ]}


def test_unknown_scope_does_not_become_verified_positive():
    result = score(reference(), {"header_words": [0], "coverage_edges": [[0, 1], [0, 3]]})
    assert result["coverage_tp"] == 1
    assert result["coverage_unjudgeable_predictions"] == 1
    assert result["known_scope_graph_exact"]
    assert not result["all_predictions_verified_scope_graph_exact"]
    summary = aggregate([result])
    assert summary["coverage_precision_known"] == 1
    assert summary["coverage_precision_unknown_counted_unverified"] == .5


def test_nonheader_and_wrong_column_predictions_are_false_positives():
    result = score(reference(), {"header_words": [0, 1], "coverage_edges": [[0, 2], [1, 2]]})
    assert (result["coverage_tp"], result["coverage_fp"], result["coverage_fn"]) == (0, 2, 1)
    assert result["header_fp"] == 1
    with pytest.raises(ValueError):
        score(reference(), {"header_words": [0], "coverage_edges": [[0, 99]]})

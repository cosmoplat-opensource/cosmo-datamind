import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_year_auxiliary_data_v24 import parent_indices


def test_extra_own_company_scope_teachers_are_excluded_even_for_feedback_runs():
    rows = [{"pair_id": "a", "company": "A", "source_year": 2023},
            {"pair_id": "b", "company": "B", "source_year": 2023}]
    assert parent_indices(rows, "A") == [1]
    assert parent_indices(rows, "B") == [0]
    assert parent_indices(rows, "C") == [0, 1]


def test_later_year_auxiliary_labels_fail_even_if_their_company_would_be_excluded():
    rows = [{"pair_id": "a", "company": "A", "source_year": 2024}]
    with pytest.raises(ValueError, match="historical only"):
        parent_indices(rows, "A")


def test_missing_exclusion_or_duplicate_case_ids_cannot_silently_change_the_budget():
    rows = [{"pair_id": "a", "company": "A", "source_year": 2023}]
    with pytest.raises(ValueError, match="excluded company"):
        parent_indices(rows, "")
    with pytest.raises(ValueError, match="Duplicate"):
        parent_indices(rows + rows, "B")

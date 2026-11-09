import sys
from pathlib import Path

import pytest
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_table_grid_audit_v24 import candidate_blocks, table_grid, year_cue


def table(body):
    return etree.fromstring('<table xmlns="http://www.w3.org/1999/xhtml">' + body + '</table>')


def test_source_row_and_column_spans_are_resolved_without_mixing_nested_tables():
    source = table('<tr><td rowspan="2">A</td><td colspan="2">B</td></tr><tr><td>C</td><td><table><tr><td>nested</td></tr></table></td></tr>')
    grid = table_grid(source)
    assert [(r["row"], r["column"]) for r in grid] == [(0, 0), (0, 1), (1, 1), (1, 2)]


def test_unsupported_or_overlapping_spans_abstain_instead_of_repairing_labels():
    with pytest.raises(ValueError, match="unsupported_rowspan"):
        table_grid(table('<tr><td rowspan="0">A</td></tr>'))
    with pytest.raises(ValueError, match="implicit_rows"):
        table_grid(table('<tr><td rowspan="2">A</td></tr>'))
    with pytest.raises(ValueError, match="overlapping"):
        table_grid(table('<tr><td>A</td><td rowspan="2">B</td></tr><tr><td colspan="2">C</td></tr>'))


def test_row_groups_are_not_silently_crossed():
    with pytest.raises(ValueError, match="crosses_row_group"):
        table_grid(table('<tbody><tr><td rowspan="2">A</td></tr></tbody><tbody><tr><td>B</td></tr></tbody>'))


def test_year_tokens_do_not_create_a_complete_period_or_resolve_multiple_years():
    assert year_cue('For the year ended 31 December 2023') == '2023'
    assert year_cue('2023 and 2022') is None


def test_grid_period_consistency_requires_multiple_known_facts_and_stops_at_new_header():
    grid = table_grid(table('<tr><td>2023</td></tr><tr><td>A</td></tr><tr><td>B</td></tr><tr><td>2022</td></tr><tr><td>C</td></tr>'))
    def fact(path, year):
        return {"xpath": path, "declared_period_end_year": year, "hidden_inline_ancestor": False}
    blocks = candidate_blocks(grid, {1: [fact('a', '2023')], 2: [fact('b', '2023')], 4: [fact('c', '2022')]})
    assert blocks[0]["stop_before_row"] == 3
    assert blocks[0]["all_source_periods_agree_with_year_cue"]
    assert not blocks[1]["all_source_periods_agree_with_year_cue"]  # one example is not a consistent block
    assert not blocks[0]["visual_header_ownership_certified"]

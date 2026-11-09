import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_table_render_audit_v24 import column_intervals, inspect_block, visible_text_box


def observation(box, *, year=False, page="pf1", visible=True):
    return {"resolved": True, "page_id": page, "style_visible": visible, "bbox": box,
            "text_rects": [box], "year_matches": [{"style_visible": visible, "rects": [box]}] if year else []}


def fixture():
    cells = [{"xpath": "h", "column": 1, "colspan": 1, "numeric_facts": []}]
    measurements = {"h": observation([100, 10, 120, 20], year=True)}
    for i, (column, x) in enumerate([(0, 40), (1, 110), (0, 40), (1, 110)]):
        path = str(i)
        cells.append({"xpath": "cell" + path, "column": column, "colspan": 1,
                      "numeric_facts": [{"xpath": path, "hidden_inline_ancestor": False}]})
        y = 40 + 20 * (i // 2)
        measurements[path] = observation([x - 10, y, x + 10, y + 10])
    block = {"cell": 0, "year_token": "2023", "supported_fact_paths": ["1", "3"],
             "all_source_periods_agree_with_year_cue": True}
    return {"cells": cells}, block, measurements


def test_visible_year_geometry_requires_one_glyph_occurrence_and_same_page():
    record = observation([10, 10, 30, 20], year=True)
    assert visible_text_box(record, "pf1", [200, 200], year=True)
    assert visible_text_box(record, "pf2", [200, 200], year=True) is None
    record["year_matches"].append(record["year_matches"][0])
    assert visible_text_box(record, "pf1", [200, 200], year=True) is None


def test_hidden_nonfinite_or_off_page_text_never_qualifies():
    for record in [observation([0, 0, 20, 20], visible=False), observation([0, 0, float("nan"), 20]),
                   observation([190, 10, 220, 20]), observation([10, 20, 10, 30])]:
        assert visible_text_box(record, "pf1", [200, 200]) is None


def test_source_column_is_not_assumed_to_equal_css_order():
    table, _, measurements = fixture()
    assert set(column_intervals(table, measurements, "pf1", [200, 200])) == {0, 1}
    for key in ("0", "2"):
        measurements[key] = observation([140, 30, 160, 40])
    assert column_intervals(table, measurements, "pf1", [200, 200]) == {}


def test_consistent_visible_year_edges_remain_weak_not_ownership_certificates():
    table, block, measurements = fixture()
    result = inspect_block(table, block, measurements, "pf1", [200, 200], True)
    assert result["all_candidate_edges_geometrically_supported"]
    assert not result["visual_scope_ownership_certified"]
    assert not result["complete_financial_period_certified"]


def test_changed_pixels_and_semantic_inconsistency_cannot_be_hidden_by_valid_geometry():
    table, block, measurements = fixture()
    result = inspect_block(table, block, measurements, "pf1", [200, 200], False)
    assert not result["all_candidate_edges_geometrically_supported"]
    assert all("rendered_pixels_differ_from_frozen_page" in e["unknown_reasons"] for e in result["edges"])
    block["all_source_periods_agree_with_year_cue"] = False
    result = inspect_block(table, block, measurements, "pf1", [200, 200], True)
    assert not result["all_candidate_edges_geometrically_supported"]


def test_below_target_or_wrong_column_year_is_not_accepted_as_scope():
    table, block, measurements = fixture()
    measurements["h"] = observation([30, 80, 50, 90], year=True)
    result = inspect_block(table, block, measurements, "pf1", [200, 200], True)
    assert not result["all_candidate_edges_geometrically_supported"]
    for edge in result["edges"]:
        assert "source_column_not_confirmed_by_rendered_text_tracks" in edge["unknown_reasons"]
        assert "year_glyph_not_above_numeric_text" in edge["unknown_reasons"]

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_binding_candidates_v18 import expand, located_periods, visible_unit_hypotheses


def test_dash_zero_is_an_extra_uncertain_semantic_option_with_unknown_preserved():
    task = {"quantity_candidates":[],"unit_candidates":[],"period_candidates":[],"unknown_candidates":{"quantity":{"unknown":True}}}
    source = {"native_ocr_strings":["—"],"public_top_text":"£m","common_visual_observation":None}
    new = expand(task,source)
    assert new["quantity_candidates"][0]["semantic_zero_hypothesis_not_literal_zero"]
    assert new["unknown_candidates"] == task["unknown_candidates"]
    source["native_ocr_strings"] = ["=","N/A",""]
    assert expand(task,source)["quantity_candidates"] == []


def test_no_currency_is_guessed_from_bare_dollar_or_administrative_metadata():
    assert visible_unit_hypotheses({"public_top_text":"$m","company":"USA","source_year":2025}) == []
    unit = visible_unit_hypotheses({"public_top_text":"£m"})[0]
    assert unit["multiplier"] == "1000000" and unit["unit"]["numerator"][0].endswith("}GBP")


def test_year_matching_can_locate_a_period_but_cannot_certify_ownership():
    period = {"candidate_id":"p","period":{"kind":"duration","start_date":"2021-01-01","end_date":"2021-12-31","complete":True}}
    source = {"common_visual_observation":{"headers":[{"id":"h","kind":"period","text":"2021","box":[.7,.1,.8,.2]}]}}
    result = located_periods([period],source)
    assert len(result) == 1 and result[0]["header"]["id"] == "h"
    assert not result[0]["header_ownership_certified"]
    source["common_visual_observation"]["headers"][0]["text"] = "2022"
    assert not located_periods([period],source)

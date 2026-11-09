from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_visible_periods_v17 import date_mentions, visible_period_candidates


def periods(text, **extra):
    rows, _ = visible_period_candidates({"public_top_text": text, "public_row_text": "", **extra})
    return {tuple(sorted((k, v) for k, v in r["period"].items() if k != "complete")): r for r in rows}


def test_complete_visible_dates_are_parsed_even_when_ocr_joins_words():
    assert [d["date"].isoformat() for d in date_mentions("31December2022 / December 31, 2023")] == ["2022-12-31", "2023-12-31"]
    assert date_mentions("31 February 2023") == []


def test_bare_or_administrative_year_cannot_supply_a_period():
    assert periods("2022 2023", source_year=2025, teacher_binding={"period": {"instant": "2025-12-31"}}) == {}


def test_comparative_annual_boundary_is_explicitly_an_uncertain_calendar_hypothesis():
    result = periods("For the year ended 31 March 2023 2023 2022")
    key = tuple(sorted({"kind": "duration", "start_date": "2021-04-01", "end_date": "2022-03-31"}.items()))
    assert result[key]["calendar_convention_assumed"]
    assert result[key]["uncertain"] and not result[key]["header_ownership_certified"]
    assert not result[key]["source_year_used"]


def test_visible_explicit_interval_and_opening_hypothesis_remain_distinct():
    interval = periods("From 1 July 2022 to 30 September 2022")
    key = tuple(sorted({"kind": "duration", "start_date": "2022-07-01", "end_date": "2022-09-30"}.items()))
    assert not interval[key]["calendar_convention_assumed"]
    opening = periods("At 1 January 2022")
    key = tuple(sorted({"kind": "instant", "instant": "2021-12-31"}.items()))
    assert opening[key]["calendar_convention_assumed"]


def test_leap_day_does_not_invent_an_anniversary_convention():
    result = periods("For the year ended 29 February 2024")
    assert all(dict(key)["kind"] == "instant" for key in result)

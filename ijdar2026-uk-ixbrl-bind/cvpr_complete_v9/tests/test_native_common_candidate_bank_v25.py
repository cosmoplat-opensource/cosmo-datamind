import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_common_candidate_bank_v25 import ocr_calendar_bank, report_indices


def declaration(reports, namespace="fixture"):
    return {"name": "F", "namespace": namespace, "field_id": "fixture", "labels": [], "labels_by_report": {},
            "declaration_variants": [{"reports": reports, "declaration": {
                "type": "fixture", "balance": None, "period_type": "duration", "abstract": False}}]}


def words(lines):
    return [{"text": text, "box": [.1, .1 + index * .05, .9, .13 + index * .05]}
            for index, text in enumerate(lines)]


def test_all_eligible_report_fields_survive_beyond_the_old_64_card_cutoff():
    fields = [declaration(["local"]) for _ in range(70)] + [declaration(["other"]), declaration(["local"], "other_ns")]
    fields += [declaration(["public_standard"])]
    assert report_indices(fields, "local", ["fixture"]) == [*range(70), 72]


def test_bare_years_remain_locations_without_fabricated_month_day_or_period():
    bank = ocr_calendar_bank(words(["2023", "2024"]))
    assert not bank["period_hypotheses"] and len(bank["printed_year_anchors"]) == 2


def test_comparative_instant_requires_a_printed_year_and_a_full_date_anchor():
    bank = ocr_calendar_bank(words(["At 31 December 2024", "2023"]))
    row = next(row for row in bank["period_hypotheses"] if row["period"] == {"kind": "instant", "instant": "2023-12-31"})
    assert row["calendar_convention_assumed"] and not row["visible_ownership_certified"]
    assert any(e["basis"] == "printed_year_plus_page_month_day_hypothesis_not_verified_scope" for e in row["supporting_ocr_evidence"])
    no_year = ocr_calendar_bank(words(["At 31 December 2024"]))
    assert not any(row["period"].get("instant") == "2023-12-31" for row in no_year["period_hypotheses"])


def test_repeated_full_dates_retain_all_original_locations():
    bank = ocr_calendar_bank(words(["31 December 2024", "31 December 2024"]))
    row = next(row for row in bank["period_hypotheses"] if row["period"] == {"kind": "instant", "instant": "2024-12-31"})
    assert {tuple(e["word_indices"]) for e in row["supporting_ocr_evidence"] if e["kind"] == "original_ocr_line"} == {(0,), (1,)}


def test_leap_day_completion_is_not_invented_for_a_printed_nonleap_year():
    bank = ocr_calendar_bank(words(["29 February 2024", "2023"]))
    assert not any(row["period"].get("instant") == "2023-02-29" for row in bank["period_hypotheses"])

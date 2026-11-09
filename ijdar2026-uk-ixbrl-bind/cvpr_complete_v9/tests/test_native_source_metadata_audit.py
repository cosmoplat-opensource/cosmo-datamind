import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_source_metadata_audit import name_key, portal_order


def reports(a, b):
    return [{"attributes": {"period_end": "2023-12-31", "date_added": a}},
            {"attributes": {"period_end": "2024-12-31", "date_added": b}}]


def test_portal_backfill_is_detected_without_claiming_publication_chronology():
    row = portal_order(reports("2026-01-01 10:00:00", "2025-05-01 10:00:00"), "date_added")
    assert row["comparable"] and not row["nondecreasing_in_report_period_order"]
    assert row["original_publication_chronology_certified"] is False


def test_missing_and_mixed_timezone_metadata_remains_unknown():
    assert not portal_order(reports(None, "2025-05-01"), "date_added")["comparable"]
    assert not portal_order(reports("2024-05-01T00:00:00Z", "2025-05-01"), "date_added")["comparable"]


def test_name_keys_remove_punctuation_without_guessing_corporate_aliases():
    assert name_key("Example, PLC") == name_key("Ｅｘａｍｐｌｅ plc")
    assert name_key("Example Group plc") != name_key("Example Holdings plc")

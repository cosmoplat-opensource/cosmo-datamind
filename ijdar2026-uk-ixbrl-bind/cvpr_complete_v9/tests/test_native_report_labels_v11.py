import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_field_retrieval_v11 import report_card


def test_future_public_alias_is_not_added_to_past_training_card():
    field = {"name": "A", "namespace": "urn:issuer", "field_id": "a", "labels": ["Standard A"],
             "declaration_variants": [{"reports": ["public_standard"],
                                       "declaration": {"type": "monetary", "balance": "debit",
                                                       "period_type": "duration", "abstract": False}}],
             "labels_by_report": {"issuer_2023": ["Old wording"], "issuer_2024": ["FUTURE_NEW_ALIAS"],
                                  "public_standard": ["Standard A"]}}
    past = report_card(field, "issuer_2023")
    future = report_card(field, "issuer_2024")
    assert past["labels"] == ["Old wording", "Standard A"]
    assert future["labels"] == ["FUTURE_NEW_ALIAS", "Standard A"]
    assert "FUTURE" not in str(past)

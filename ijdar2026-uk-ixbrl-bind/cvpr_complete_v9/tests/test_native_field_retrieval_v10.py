import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_field_retrieval_v10 import query_text, report_card


def test_source_specific_declaration_is_not_transferred_by_namespace_alone():
    field = {"name": "A", "namespace": "urn:issuer", "field_id": "a", "labels": [],
             "declaration_variants": [
                 {"reports": ["issuer_2023"], "declaration": {"type": "monetary", "balance": "debit", "period_type": "duration", "abstract": False}},
                 {"reports": ["issuer_2024"], "declaration": {"type": "shares", "balance": None, "period_type": "instant", "abstract": False}},
             ]}
    assert report_card(field, "issuer_2023")["type"] == "monetary"
    assert report_card(field, "issuer_2024")["type"] == "shares"
    assert report_card(field, "issuer_2025") is None


def test_retrieval_queries_ignore_teacher_values_and_metadata_year():
    task = {"public_row_text": "Cash and cash equivalents", "source_year": 2024,
            "field_name": "PRIVATE_FIELD", "canonical_value": "PRIVATE_ANSWER"}
    observation = {"row_text": "Cash", "section_text": "Balance sheet",
                   "headers": [{"kind": "period", "text": "PRIVATE_PERIOD"},
                               {"kind": "field", "text": "Assets"}]}
    result = query_text(task, observation)
    assert "PRIVATE" not in str(result)
    assert "2024" not in str(result)

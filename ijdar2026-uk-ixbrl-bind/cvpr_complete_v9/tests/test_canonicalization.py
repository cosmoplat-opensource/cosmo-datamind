from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canonicalization_prepare import make_candidates, ordered_prefix, public_scopes
from public_schema import declarations, lookup
from canonicalization_observe import request_targets, validate
from canonicalization_train import (
    allowed_feedback,
    exact_context_key,
    sign_label,
    structured_key,
)


class CanonicalizationPreparationTests(unittest.TestCase):
    def test_literal_sign_and_locale_ambiguity_are_not_resolved_by_answers(self):
        candidates = make_candidates(["(1,234)"], "1000000")
        self.assertEqual(
            {c["computed_answer"] for c in candidates},
            {"-1234000000", "1234000000", "-1234000.000", "1234000.000"},
        )

    def test_missing_numeric_candidate_remains_missing(self):
        self.assertEqual(make_candidates(["—"], "1000"), [])

    def test_invalid_or_missing_scope_is_not_whole_page(self):
        scopes = public_scopes(
            [
                {
                    "text": "£m",
                    "kind": "unit",
                    "box": [0.1, 0.1, 0.2, 0.2],
                    "applies_to": [[0, 0, 2, 1]],
                    "ambiguous": False,
                }
            ]
        )
        self.assertEqual(scopes[0]["scope_candidates"], [])
        self.assertTrue(scopes[0]["scope_unknown"])

    def test_future_feedback_is_never_selected(self):
        tasks = [
            {"task_id": str(y), "company": "a", "source_year": y}
            for y in (2025, 2023, 2024)
        ]
        self.assertEqual(
            [t["source_year"] for t in ordered_prefix(tasks, "a", 10)], [2023]
        )

    def test_exact_namespace_required_for_schema_properties(self):
        catalog = {
            "namespace": "2022",
            "fields": {"Costs": {"name": "Costs", "balance": "debit"}},
        }
        self.assertIsNone(lookup(catalog, "2023", "Costs")["balance"])
        self.assertEqual(lookup(catalog, "2022", "Costs")["balance"], "debit")

    def test_partial_schema_download_cannot_publish_a_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "partial.xsd"
            file.write_text('<schema xmlns="http://www.w3.org/2001/XMLSchema">')
            with self.assertRaises(Exception):
                declarations(file)

    def test_model_target_whitelist_drops_private_answers(self):
        keys = (
            "task_id",
            "target_box",
            "declared_field",
            "field_properties",
            "oracle_multiplier",
            "ocr_strings",
            "public_row_text",
            "public_top_text",
            "candidates",
        )
        task = dict.fromkeys(keys, None)
        task["canonical_value"] = "PRIVATE"
        task["publisher_region_id"] = "PRIVATE_ID"
        self.assertEqual(set(request_targets([task])[0]), set(keys))

    def test_observer_rejects_duplicate_or_unregistered_candidates(self):
        import json

        tasks = [{"task_id": "a", "candidates": [{}]}]
        row = {
            "task_id": "a",
            "candidate_index": 1,
            "presentation": "note",
            "row_text": "Cost",
            "section_text": "Note 1",
            "uncertain": False,
        }
        with self.assertRaises(ValueError):
            validate(json.dumps({"targets": [row]}), tasks)
        row["candidate_index"] = None
        self.assertEqual(
            validate(json.dumps({"targets": [row]}), tasks)[0]["candidate_index"], None
        )
        with self.assertRaises(ValueError):
            validate(json.dumps({"targets": [row, row]}), tasks)

    def test_feedback_loader_checks_both_public_and_teacher_year(self):
        tasks = [{"task_id": "a", "source_year": 2024}]
        with self.assertRaises(ValueError):
            allowed_feedback(tasks, {"a": {"source_year": 2023}}, ["a"])
        tasks[0]["source_year"] = 2023
        with self.assertRaises(ValueError):
            allowed_feedback(tasks, {"a": {"source_year": 2024}}, ["a"])

    def test_zero_and_missing_bags_cannot_supervise_polarity(self):
        task = {
            "candidates": [
                {"computed_answer": "0", "polarity": 1},
                {"computed_answer": "0", "polarity": -1},
            ]
        }
        self.assertIsNone(sign_label(task, {"answer": "0"}))
        self.assertIsNone(sign_label(task, {"answer": "123"}))

    def test_unknown_namespace_or_presentation_does_not_make_a_typed_scope(self):
        task = {
            "field_properties": {
                "namespace": "2023",
                "name": "Costs",
                "declaration_status": "unknown_namespace_or_version",
            }
        }
        self.assertIsNone(structured_key(task, {"presentation": "note"}))
        self.assertIsNone(exact_context_key(task, {"presentation": "unknown"}))


if __name__ == "__main__":
    unittest.main()

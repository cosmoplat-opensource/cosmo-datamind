import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json

from native_correspondence_observe_v10 import multiplier_or_none, validate
from native_correspondence_prepare_v10 import PUBLIC_KEYS, public_target


class NativeInputSeparationTests(unittest.TestCase):
    def test_target_field_scale_and_old_conditioned_observation_not_forwarded(self):
        task = dict.fromkeys(
            (
                "task_id",
                "company",
                "report",
                "source_year",
                "page_id",
                "image",
                "image_sha256",
                "target_box",
                "crop_image",
                "crop_image_sha256",
                "ocr_strings",
                "crop_OCR_hypotheses",
                "public_row_text",
                "public_top_text",
            ),
            "public",
        )
        task.update(
            declared_field="PRIVATE_FIELD",
            oracle_multiplier="PRIVATE_SCALE",
            field_properties={"name": "PRIVATE_FIELD"},
            candidates=[{"computed_answer": "PRIVATE_ANSWER"}],
            shared_headers=["ORACLE_CONDITIONED_RESPONSE"],
            canonical_value="PRIVATE_REFERENCE",
        )
        result = public_target(
            task, [{"field_id": "f0", "name": "A_COMMON_CATALOG_ENTRY"}]
        )
        self.assertEqual(set(result), PUBLIC_KEYS)
        self.assertFalse(
            any(
                token in str(result)
                for token in (
                    "PRIVATE_FIELD",
                    "PRIVATE_SCALE",
                    "PRIVATE_ANSWER",
                    "PRIVATE_REFERENCE",
                    "ORACLE_CONDITIONED_RESPONSE",
                )
            )
        )

    def observation(self):
        task = {
            "task_id": "a",
            "target_box": [0.7, 0.4, 0.8, 0.45],
            "field_candidates": [{"field_id": "f0"}],
        }
        row = {
            "task_id": "a",
            "literal_readings": ["(123.4)"],
            "field_candidates": ["f0"],
            "headers": [
                {
                    "id": "h0",
                    "kind": "unit",
                    "text": "£m",
                    "box": [0.7, 0.1, 0.8, 0.12],
                    "scope_boxes": [[0.65, 0.1, 0.85, 0.8]],
                    "uncertain": False,
                }
            ],
            "unit_candidates": [
                {
                    "header_id": "h0",
                    "kind": "monetary",
                    "currency": "GBP",
                    "multiplier": "1000000",
                    "uncertain": False,
                }
            ],
            "period_candidates": [],
            "context_box": [0, 0, 0.4, 0.4],
            "uncertain": False,
        }
        return task, row

    def test_invalid_scope_does_not_discard_valid_literal_reading(self):
        task, row = self.observation()
        result = validate(json.dumps({"targets": [row]}), [task])[0]
        self.assertEqual(result["literal_readings"], ["(123.4)"])
        self.assertIsNone(result["context_box"])
        self.assertTrue(result["uncertain"])
        self.assertTrue(result["full_visible_binding_not_certified"])

    def test_unregistered_field_and_header_reference_not_accepted(self):
        task, row = self.observation()
        row["field_candidates"] = ["PRIVATE_FIELD_NOT_IN_PUBLIC_POOL"]
        row["unit_candidates"][0]["header_id"] = "nonexistent"
        result = validate(json.dumps({"targets": [row]}), [task])[0]
        self.assertEqual(result["field_candidates"], [])
        self.assertEqual(result["unit_candidates"], [])

    def test_duplicate_or_missing_target_fails_whole_inventory_check(self):
        task, row = self.observation()
        for body in ({"targets": []}, {"targets": [row, row]}):
            with self.assertRaises(ValueError):
                validate(json.dumps(body), [task])

    def test_unknown_or_nondecimal_power_scale_is_not_invented(self):
        self.assertIsNone(multiplier_or_none(None))
        self.assertEqual(multiplier_or_none("0.01"), "0.01")
        for scale in ("NaN", "Infinity", "3", "-1000"):
            with self.assertRaises(ValueError):
                multiplier_or_none(scale)

    def test_duration_without_start_remains_uncertain(self):
        task, row = self.observation()
        row["period_candidates"] = [
            {
                "header_id": "h0",
                "kind": "duration",
                "displayed_year": 2023,
                "start_date": None,
                "end_date": "2023-03-31",
                "uncertain": False,
            }
        ]
        result = validate(json.dumps({"targets": [row]}), [task])[0]
        self.assertTrue(result["period_candidates"][0]["uncertain"])


if __name__ == "__main__":
    unittest.main()

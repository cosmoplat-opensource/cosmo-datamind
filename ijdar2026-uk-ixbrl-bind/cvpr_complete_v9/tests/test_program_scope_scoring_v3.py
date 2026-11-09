import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from program_scope_learning_v3 import METHODS, SEEDS
from score_program_scope_v3 import equal_quantity, holm, validate_matrix


class CompleteProgramScoreTests(unittest.TestCase):
    def fixture(self):
        tasks = {
            "a": {
                "task_id": "a",
                "company": "company",
                "candidates": [{"computed_answer": "17"}],
            }
        }
        rows = [
            {
                "task_id": "a",
                "company": "company",
                "method": "common_visual",
                "budget": 0,
                "seed": None,
                "prediction": {"candidate_index": 0, "forced_answer": "17"},
            }
        ]
        for method in METHODS:
            for budget in (0, 5, 10):
                for seed in SEEDS:
                    rows.append(
                        {
                            "task_id": "a",
                            "company": "company",
                            "method": method,
                            "budget": budget,
                            "seed": seed,
                            "prediction": {
                                "candidate_index": None,
                                "forced_answer": None,
                                "probabilities": [0.25, 0.75],
                            },
                        }
                    )
        return tasks, rows

    def test_complete_matrix_keeps_all_null_and_failure_predictions(self):
        tasks, rows = self.fixture()
        self.assertEqual(len(validate_matrix(tasks, rows)), 28)

    def test_partial_or_duplicate_matrix_cannot_be_scored(self):
        tasks, rows = self.fixture()
        for invalid in (rows[:-1], rows + [rows[0]]):
            with self.assertRaises(ValueError):
                validate_matrix(tasks, invalid)

    def test_null_or_unregistered_answers_rejected(self):
        tasks, rows = self.fixture()
        for mutation in (
            {"candidate_index": None, "forced_answer": "17"},
            {"candidate_index": 1, "forced_answer": "17"},
            {"candidate_index": 0, "forced_answer": "18"},
        ):
            rows[0]["prediction"] = mutation
            with self.assertRaises(ValueError):
                validate_matrix(tasks, rows)

    def test_probability_argmax_and_identity_checked(self):
        tasks, rows = self.fixture()
        rows[1]["prediction"]["probabilities"] = [0.75, 0.25]
        with self.assertRaises(ValueError):
            validate_matrix(tasks, rows)
        tasks, rows = self.fixture()
        rows[0]["company"] = "different"
        with self.assertRaises(ValueError):
            validate_matrix(tasks, rows)

    def test_holm_adjustment_preserves_original_comparison_order(self):
        self.assertEqual(holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])

    def test_numeric_equality_is_finite_and_exact(self):
        self.assertTrue(equal_quantity("123.00", "123"))
        self.assertFalse(equal_quantity(None, "0"))
        self.assertFalse(equal_quantity("NaN", "123"))
        self.assertFalse(equal_quantity("123.0001", "123"))


if __name__ == "__main__":
    unittest.main()

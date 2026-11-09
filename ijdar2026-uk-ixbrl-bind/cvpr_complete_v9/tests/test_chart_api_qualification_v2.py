import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chart_api_inputs_v2 import adapted_size
from chart_native_repair_v2 import available_balance, qualification


class ChartAPIQualificationTests(unittest.TestCase):
    def test_uniform_long_image_resize_preserves_aspect_and_bounds(self):
        size = adapted_size(2552, 12361)
        self.assertEqual(size[1], 8192)
        self.assertLess(abs(size[0] / size[1] - 2552 / 12361), 1 / 8192)
        self.assertEqual(adapted_size(6811, 6055), (6811, 6055))
        self.assertEqual(adapted_size(1, 100000), (1, 8192))

    def test_invalid_dimensions_not_accepted(self):
        for size in ((0, 100), (-1, 100)):
            with self.assertRaises(ValueError):
                adapted_size(*size)

    def test_changed_inputs_include_old_successes_and_model_failures(self):
        for status in (
            "completed",
            "incomplete_or_empty_model_answer",
            "terminal_api_failure",
            "transport_failure",
        ):
            self.assertEqual(
                qualification({"status": status}, True),
                ("uniform_image_input_changed", 4),
            )

    def test_unchanged_model_failures_and_successes_are_preserved(self):
        for status in (
            "completed",
            "incomplete_or_empty_model_answer",
            "terminal_api_failure",
        ):
            self.assertEqual(
                qualification({"status": status}, False),
                ("byte_identical_preserved", 0),
            )
        self.assertEqual(
            qualification({"status": "transport_failure"}, False),
            ("registered_transport_failure", 2),
        )

    def test_balance_network_fault_retries_do_not_advance_model_attempts(self):
        with patch(
            "chart_native_repair_v2.balance",
            side_effect=[httpx.ConnectError("offline"), (True, 20)],
        ) as mocked:
            self.assertEqual(available_balance(Mock(), "test-key"), (True, 20))
            self.assertEqual(mocked.call_count, 2)

    def test_balance_failure_hides_credentials_and_has_finite_retries(self):
        with patch(
            "chart_native_repair_v2.balance",
            side_effect=httpx.ConnectError("secret-test-key"),
        ) as mocked:
            with self.assertRaises(RuntimeError) as caught:
                available_balance(Mock(), "secret-test-key")
            self.assertNotIn("secret-test-key", str(caught.exception))
            self.assertEqual(mocked.call_count, 3)


if __name__ == "__main__":
    unittest.main()

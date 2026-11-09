from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import continue_tat_ocr_v2 as recovery


class UniformInputRepairTests(unittest.TestCase):
    def test_cache_reuse_cannot_overwrite_different_model_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "old.json"
            source.write_text("old")
            target = root / "new.json"
            target.write_text("new")
            with self.assertRaises(RuntimeError):
                recovery.copy_exact(source, target)
            self.assertEqual(target.read_text(), "new")

    def test_account_network_retry_does_not_log_credentials_or_change_inference(self):
        calls = []

        def probe(client, key):
            calls.append(1)
            if len(calls) < 3:
                raise httpx.ConnectError("SECRET_KEY")
            return True, 100

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(recovery, "DEST", Path(folder)),
                patch.object(recovery.time, "sleep"),
            ):
                self.assertEqual(
                    recovery.resilient_account_probe(probe)(None, "SECRET_KEY"),
                    (True, 100),
                )
                log = (
                    Path(folder) / "account_probe_transport_failures.jsonl"
                ).read_text()
                self.assertNotIn("SECRET_KEY", log)
                self.assertIn("not_a_model_inference_retry", log)


if __name__ == "__main__":
    unittest.main()

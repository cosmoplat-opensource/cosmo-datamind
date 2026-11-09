import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from review_native_scope_v23 import validate_exposure


def test_budget_audit_rejects_extra_steps_and_missing_actual_case_accounting():
    record = {"optimizer_steps": 256, "sampled_local_cases": 2048, "sampled_replay_cases": 0,
              "case_sequence_sha256": "recorded-sequence", "sampled_witness_factor_pairs": 20000}
    validate_exposure(record, 256, 2048, 0)
    with pytest.raises(ValueError, match="budget"):
        validate_exposure({**record, "optimizer_steps": 257}, 256, 2048, 0)
    with pytest.raises(ValueError, match="accounting"):
        validate_exposure({**record, "case_sequence_sha256": None}, 256, 2048, 0)

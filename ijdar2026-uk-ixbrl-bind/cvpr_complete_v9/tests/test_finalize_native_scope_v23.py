import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finalize_native_scope_v23 import geometry_summary, readiness


def test_negative_calibration_is_retained_and_no_geometry_update_certifies_ownership():
    summary = geometry_summary([{"method": "scope_relational", "geometry_scale": -.04,
                                 "geometry_parameter_delta_l2": 1.}])
    assert summary["scope_relational"]["calibration_sign_counts"] == {"negative": 1}
    assert summary["scope_relational"]["positive_density_coverage_not_established_by_parameter_updates_alone"]


def test_nonfinite_mechanism_audit_cannot_silently_pass():
    with pytest.raises(ValueError, match="Nonfinite"):
        geometry_summary([{"method": "scope_relational", "geometry_scale": float("nan"),
                           "geometry_parameter_delta_l2": 1.}])


def test_incomplete_or_missing_observations_do_not_imply_a_stopped_training_process(tmp_path):
    assert readiness(tmp_path) == "pending"
    (tmp_path / "manifest.json").write_text(json.dumps({"complete": True}))
    assert readiness(tmp_path) == "pending"
    (tmp_path / "development_results.json").write_text(json.dumps({"complete": True}))
    assert readiness(tmp_path) == "ready"
    (tmp_path / "progress.json").write_text(json.dumps({"phase": "failed"}))
    assert readiness(tmp_path) == "pipeline_reported_failure"

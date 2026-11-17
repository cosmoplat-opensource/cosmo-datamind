"""Execute the real build UI functions with deterministic DOM and stream fixtures."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case", [
    "workload_stays_separate_from_acceptance",
    "semantic_review_escapes_evidence_and_separates_formula_check",
    "truncated_stream", "http_error", "terminal_error", "success_and_duplicate_submit",
    "crlf_stream", "result_html_escaping", "iteration_race", "clear_iteration_race",
    "busy_iteration_guard", "corrupt_history", "late_reference_catalog",
    "duplicate_preview_ids", "history_clears_old_base", "failed_context_blocks_build",
    "stream_guards", "failure_preserves_new_draft",
    "built_load_failure", "sources_load_failure", "skills_load_failure", "failed_delete_preserves_base",
    "engine_empty_registry",
    "upload_failure_is_not_success",
    "dropped_stream_is_followed_from_the_server_job", "reload_resumes_a_running_build",
    "reload_reports_an_interrupted_build", "reload_with_nothing_pending_does_nothing",
    "cancel_asks_the_server_to_stop",
])
def test_build_ui_behavior(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for executable frontend behavior tests")
    result = subprocess.run(
        [node, str(ROOT / "tests/frontend/build-ui.cjs"), case],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr

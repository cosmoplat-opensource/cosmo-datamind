"""Execute the real build UI functions with deterministic DOM and stream fixtures."""
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case", [
    "truncated_stream", "http_error", "terminal_error", "success_and_duplicate_submit",
    "crlf_stream", "result_html_escaping", "iteration_race", "clear_iteration_race",
    "busy_iteration_guard", "corrupt_history", "late_reference_catalog",
    "duplicate_preview_ids", "history_clears_old_base", "failed_context_blocks_build",
    "stream_guards", "failure_preserves_new_draft",
    "built_load_failure", "sources_load_failure", "skills_load_failure", "failed_delete_preserves_base",
    "engine_empty_registry",
    "upload_failure_is_not_success",
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

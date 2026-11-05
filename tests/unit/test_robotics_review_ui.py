"""T06 RED: editable knowledge controller and unified browser wiring — DR-063 / DR-064 / DR-065."""
import ast
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ['load_read_only', 'disabled_writes', 'capability_failure', 'exact_save', 'required_identity',
         'duplicate_save', 'transport_retry', 'changed_input_new_id', 'conflict_preserves_input',
         'non_json_error', 'late_version', 'wrong_identity', 'dirty_navigation', 'edited_during_save',
         'dispose_ignores_late', 'immutable_snapshot', 'review_exact_version', 'publish_guard',
         'publish_exact_review', 'review_invalidation', 'dirty_review_guard', 'duplicate_review_publish',
         'diff_is_read_only', 'merge_pins_preview']


def run_case(case, *args):
    node = shutil.which('node')
    assert node, 'Node is required for reviewed state tests, not an optional skip'
    result = subprocess.run([node, str(ROOT / 'tests/frontend/robotics-review-ui.cjs'), case, *args],
                            cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('case', CASES)
def test_knowledge_controller_retains_reviewed_state_contracts(case):
    run_case(case)


@pytest.mark.parametrize('status', [400, 404, 409, 413, 422, 503])
def test_knowledge_http_errors_preserve_unsaved_input(status):
    run_case('error_preserves_input', str(status))


def test_new_browser_suite_enters_existing_unified_check():
    tree = ast.parse((ROOT / 'scripts/check.py').read_text())
    commands = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == 'run_check']
    assert any(any(isinstance(n, ast.Constant) and n.value == 'tests/frontend/robotics-review-browser.py'
                   for n in ast.walk(call)) for call in commands), 'T07 must wire the real governance browser suite'

"""T04 RED: task controller races plus mandatory actual-browser check wiring — DR-063 / DR-066."""
import ast
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(case, *args):
    node = shutil.which('node')
    assert node, 'Node is required, not an optional skip'
    result = subprocess.run([node, str(ROOT / 'tests/frontend/robotics-task-ui.cjs'), case, *args],
        cwd=ROOT, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('case', ['restore_get_only', 'scoped_save', 'duplicate_save', 'non_json_error',
    'retry_identity', 'late_session', 'changed_during_save', 'late_dispose', 'clear_previous_result',
    'wrong_identity', 'snapshot_copy'])
def test_task_controller_state_contract(case):
    run(case)


@pytest.mark.parametrize('status', [400, 409, 413, 422, 503])
def test_task_save_error_preserves_editable_answers(status):
    run('failed_save_keeps_input', str(status))


def test_actual_task_browser_suite_is_wired_into_all_checks():
    source = ast.parse((ROOT / 'scripts/check.py').read_text())
    calls = [node for node in ast.walk(source) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'run_check']
    assert any(any(isinstance(n, ast.Constant) and n.value == 'tests/frontend/robotics-task-browser.py'
                   for n in ast.walk(call)) for call in calls)

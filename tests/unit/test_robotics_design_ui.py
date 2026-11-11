"""Design controller behavior and mandatory browser regression wiring — DR-070 / T08 RED."""
import ast
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(case, *args):
    node = shutil.which('node')
    assert node, 'Node is required; no silent skip'
    result = subprocess.run([node, str(ROOT / 'tests/frontend/robotics-design-ui.cjs'), case, *args],
        cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('case', ['build_description_and_session', 'configuration_problem_blocks_submit',
    'restore_get_only', 'message_exact_revision_stage', 'confirmation_snapshot', 'schema_confirmation',
    'edit_clears_current_output', 'duplicate_message', 'transport_retry_identity', 'changed_input_new_identity',
    'new_text_survives_late_success', 'failure_preserves_input_and_design', 'non_json_error',
    'wrong_session_identity', 'late_session_response', 'dispose_ignores_pending', 'polling_single_flight',
    'history_does_not_change_writable_revision', 'explicit_retry_bound_to_failure', 'retry_guard', 'snapshot_is_detached'])
def test_design_controller_contract(case):
    run(case)


@pytest.mark.parametrize('status', ['needs_description', 'awaiting_input', 'awaiting_confirmation',
                                    'completed', 'failed', 'interrupted'])
def test_design_waiting_and_terminal_states_stop_polling(status):
    run('stopped_status_does_not_poll', status)


def test_design_browser_suite_is_wired_into_full_regression():
    source = ast.parse((ROOT / 'scripts/check.py').read_text())
    calls = [n for n in ast.walk(source) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == 'run_check']
    assert any(any(isinstance(n, ast.Constant) and n.value == 'tests/frontend/robotics-design-browser.py'
                   for n in ast.walk(call)) for call in calls)

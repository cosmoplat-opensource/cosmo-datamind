"""T06 RED: executable browser-independent UI contracts — DR-063 / DR-064 / DR-065."""
import ast
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BUILD_CASES = [
    'disabled_feature', 'capability_failure', 'empty_file', 'size_limit', 'unsupported_profile',
    'exact_multipart_roles', 'duplicate_submit', 'transport_retry_identity', 'changed_input_new_identity',
    'changed_file_new_identity', 'preserve_new_draft', 'non_json_error', 'cursor_deduplication', 'poll_single_flight', 'refresh_never_posts',
    'late_run_response', 'disposal_stops_polling_and_late_render', 'poll_error_preserves_last_state',
    'explicit_retry_new_run', 'retry_guards',
]
ASSET_CASES = ['assets_catalog_roles', 'assets_catalog_error_not_empty_success', 'assets_catalog_late_filter', 'assets_fixed_version', 'assets_late_response',
               'assets_error_no_old_detail', 'assets_wrong_identity_rejected', 'assets_dispose_ignores_pending']


def run_case(case, *args):
    node = shutil.which('node')
    assert node, 'Node is required; missing environment must fail, not silently skip reviewed UI coverage'
    result = subprocess.run([node, str(ROOT / 'tests/frontend/robotics-ui.cjs'), case, *args], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('case', BUILD_CASES)
def test_robotics_build_controller_behavior(case):
    run_case(case)


@pytest.mark.parametrize('field', ['name', 'behavior_tree', 'node_models'])
def test_robotics_required_inputs_are_validated_before_upload(field):
    run_case('required_input', field)


@pytest.mark.parametrize('status', [400, 409, 413, 429, 503])
def test_robotics_submission_errors_preserve_editable_draft(status):
    run_case('http_error', str(status))


@pytest.mark.parametrize('status', ['completed', 'failed', 'interrupted'])
def test_robotics_terminal_states_stop_polling(status):
    run_case('terminal_stops_polling', status)


@pytest.mark.parametrize('case', ASSET_CASES)
def test_robotics_asset_controller_behavior(case):
    run_case(case)


def test_real_robotics_browser_suite_is_wired_into_unified_check():
    source = ast.parse((ROOT / 'scripts/check.py').read_text())
    # Require an executable run_check command, not a reassuring comment or README link.
    commands = [node for node in ast.walk(source) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == 'run_check']
    assert any(any(isinstance(n, ast.Constant) and n.value == 'tests/frontend/robotics-build-browser.py'
                   for n in ast.walk(call)) for call in commands), 'New real browser suite must enter scripts/check.py'

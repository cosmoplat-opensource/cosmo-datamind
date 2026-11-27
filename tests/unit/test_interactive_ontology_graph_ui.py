"""Execute shared controller behavior in Node; browser geometry follows in T04 — DR-166."""
from pathlib import Path
import shutil
import subprocess
import sys

import playwright

ROOT = Path(__file__).resolve().parents[2]


def run_case(case):
    node = shutil.which('node') or str(Path(playwright.__file__).parent / 'driver/node')
    assert Path(node).is_file(), 'Node runtime must be available before collecting RED evidence'
    run = subprocess.run([node,
                          str(ROOT / 'tests/frontend/interactive-ontology-graph-ui.mjs'), case],
                         cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr


def test_revision_updates_preserve_view_and_reject_stale_context():
    run_case('revision_updates_preserve_view_and_reject_stale_context')


def test_multiple_canvases_dispose_resources_and_scope_events():
    run_case('multiple_canvases_dispose_resources_and_scope_events')


def test_paged_expansion_deduplicates_shared_membership():
    run_case('paged_expansion_deduplicates_shared_membership')


def test_deleting_node_clears_selection_and_marks_removal():
    run_case('deleting_node_clears_selection_and_marks_removal')


def test_empty_classes_notice_and_updates_do_not_trigger_writes():
    run_case('empty_classes_notice_and_updates_do_not_trigger_writes')


def test_snapshot_is_detached_and_pause_pin_are_preserved():
    run_case('snapshot_is_detached_and_pause_pin_are_preserved')


def test_oversized_updates_are_transactional():
    run_case('oversized_updates_are_transactional')


def test_moving_labels_follow_edges_and_refine_when_settled():
    run_case('moving_labels_follow_edges_and_refine_when_settled')


def test_settled_labels_are_reused_until_layout_inputs_change():
    run_case('settled_labels_are_reused_until_layout_inputs_change')


def test_graph_browser_shared_fixture_dependencies_resolve(tmp_path):
    result = subprocess.run([sys.executable, '-m', 'pytest',
        'tests/frontend/robotics-interactive-ontology-graph-browser.py', '--setup-plan', '-q',
        '--junitxml=' + str(tmp_path / 'fixture-plan.xml')],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    # Resolve the complete fixture graph without starting a browser or service.
    import xml.etree.ElementTree as ET
    cases = list(ET.parse(tmp_path / 'fixture-plan.xml').getroot().iter('testcase'))
    assert len(cases) == 17
    assert any(c.get('name', '').startswith('test_blank_search_prompts_without_changing_selection_or_scope') for c in cases)
    assert any(c.get('name', '').startswith('test_reopening_fixed_graph_revalidates_with_etag') for c in cases)
    assert sum(c.get('name', '').startswith('test_first_oversized_graph_') for c in cases) == 2
    assert any(c.get('name', '').startswith('test_recycle_bin_graph_round_trip_') for c in cases)
    assert all(c.find('error') is None and c.find('failure') is None for c in cases)

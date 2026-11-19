"""Execute shared controller behavior in Node; browser geometry follows in T04 — DR-161."""
from pathlib import Path
import shutil
import subprocess

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

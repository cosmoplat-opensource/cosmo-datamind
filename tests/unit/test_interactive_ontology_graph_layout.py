"""Graph routing and label placement keep exact output while bounding work — DR-190."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case', ['routing_and_labels_match_reference', 'limit_scale_matches_reference',
                                  'work_is_pruned_at_limit_scale', 'non_finite_positions_fall_back_to_full_scan'])
def test_interactive_graph_layout_matches_reference(case):
    node = shutil.which('node')
    assert node, 'Node is required; no silent skip'
    run = subprocess.run([node, str(ROOT / 'tests/frontend/interactive-ontology-graph-layout.mjs'), case],
                         cwd=ROOT, text=True, capture_output=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr

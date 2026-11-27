"""Timeout, navigation cancellation and response-body deadlines for graph/design reads."""
from pathlib import Path
import shutil
import subprocess

import playwright
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case', ['stalled_headers', 'stalled_body', 'parent_abort', 'already_aborted',
                                'success_and_failure_cleanup', 'design_disposal_aborts_pending',
                                'design_timeout_releases_busy_state'])
def test_interactive_ontology_graph_network_deadlines_and_cleanup(case):
    node = shutil.which('node') or str(Path(playwright.__file__).parent / 'driver/node')
    result = subprocess.run([node, 'tests/frontend/interactive-ontology-graph-network.cjs', case], cwd=ROOT,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr

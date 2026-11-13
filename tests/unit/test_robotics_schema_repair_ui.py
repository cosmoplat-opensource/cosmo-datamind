"""Report-bound, escaped, stage-specific repair suggestions — DR-072."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case', ['legacy_names', 'context_identity', 'stages', 'escaped_html', 'historical', 'adaptation', 'explicit_stage_change'])
def test_schema_repair_context_and_suggestions(case):
    node = shutil.which('node')
    assert node, 'Node is required to verify the actual UI module'
    result = subprocess.run([node, str(ROOT / 'tests/frontend/schema-repair-ui.cjs'), case],
        cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr

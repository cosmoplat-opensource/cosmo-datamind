"""Execute source switching and visualization races in production frontend functions."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('case', [
    'request_scope_preserves_headers_and_signal', 'switch_retains_page_and_recovers_without_storage',
    'saved_history_is_separate_for_each_database', 'changed_visual_source_discards_late_result',
    'all_200_tables_can_be_selected', 'stale_source_is_reported_and_bootstrap_is_unscoped',
    'drafts_are_restored_only_for_their_database',
    'scalar_kpi_does_not_invent_a_period', 'large_graph_opens_in_overview_and_preserves_readable_mode',
])
def test_database_source_frontend_behavior(case):
    node=shutil.which('node')
    if not node: pytest.skip('Node.js required for frontend behavior tests')
    root=Path(__file__).resolve().parents[2]
    result=subprocess.run([node,str(root/'tests/frontend/database-scope.cjs'),case],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr

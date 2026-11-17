"""评审台前端模块的可执行行为测试(DR-107):证据展示、转义、筛选、批量选择、分流移出的关系、取值样例。"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case", [
    "evidence_shows_keys_counts_orphans_and_reasons", "evidence_escapes_text_from_data_and_models",
    "incomplete_sample_evidence_is_flagged", "pending_filter_is_default_only_when_something_is_pending",
    "unreviewed_verified_rows_are_not_labelled_pending", "text_and_status_filters_and_paging",
    "batch_uses_only_visible_selected_rows", "set_aside_relations_are_listed_and_can_be_added_back",
    "samples_are_fetched_on_demand_and_late_results_are_dropped",
])
def test_review_ui_behavior(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for executable frontend behavior tests")
    result = subprocess.run([node, str(ROOT / "tests/frontend/review-ui.cjs"), case],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_review_module_is_loaded_and_submissions_are_attributed():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    assert "/assets/modules/review.js" in html and "/assets/styles/review.css" in html
    assert "source=RVM.graphPage?'graph':'review'" in html          # 评审台与图谱页的决定不再记成外部调用

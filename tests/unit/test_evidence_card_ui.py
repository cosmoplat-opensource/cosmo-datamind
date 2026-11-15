"""依据卡前端模块的可执行行为测试(DR-119):各类依据的展示、转义、「下一步」的宿主隔离。"""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case", [
    "empty_or_untyped_cards_render_nothing", "a_judgement_card_shows_conclusion_values_rule_and_sql",
    "constraints_show_parameters_and_evidence_and_missing_data_says_what_is_needed",
    "text_from_data_and_models_is_escaped", "next_step_questions_go_to_the_registered_host_and_links_change_the_page",
])
def test_evidence_card_behavior(case):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for executable frontend behavior tests")
    result = subprocess.run([node, str(ROOT / "tests/frontend/evidence-card.cjs"), case],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_module_is_loaded_and_rendered_under_answers():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    assert "/assets/modules/evidence-card.js" in html
    assert "renderEvidenceCard(d.evidence_card,'dq')" in html and "EVC_HOSTS" in html

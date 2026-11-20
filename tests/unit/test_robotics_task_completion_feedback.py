"""Specific completion feedback without weakening staged saves — DR-168."""
from pathlib import Path
import shutil
import subprocess

import pytest
from rdflib import Literal, XSD

from domains.robotics import task_knowledge
from tests.robotics_governance_support import NOW, TASK
from tests.robotics_support import S
from tests.unit.test_robotics_task_completion import ready_graph

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case', ['confirmation', 'mapping', 'parameter', 'new_name',
                                  'evidence', 'multiple', 'partial', 'retry'])
def test_completion_feedback_identifies_missing_fields_and_preserves_drafts(case):
    node = shutil.which('node')
    assert node, 'Node is required'
    result = subprocess.run([node, str(ROOT / 'tests/frontend/robotics-task-ui.cjs'),
                            'specific_completion_feedback', case], cwd=ROOT,
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['confirmation', 'parameter', 'mapping'])
def test_empty_answers_explain_current_gap_without_mutation(monkeypatch, kind):
    monkeypatch.setattr(task_knowledge.evidence, 'utc_now', lambda: NOW)
    graph = ready_graph()
    if kind == 'confirmation':
        graph.set((S.Part_A_001, S.confirmationStatus, Literal('source_extracted', datatype=XSD.string)))
        expected = '我已核对当前任务知识'
    elif kind == 'parameter':
        graph.remove((S.Perception_Current, S.exposureTime, None))
        expected = '曝光时间'
    else:
        graph.remove((graph.value(S.Part_A_001, S.hasPartType), S.hasPerceptionProfile, None))
        expected = '感知配置'
    before = set(graph)
    with pytest.raises(ValueError) as error:
        task_knowledge.apply_answers(graph, TASK, [], mode='real')
    assert expected in str(error.value)
    assert '请至少填写一项' not in str(error.value)
    assert set(graph) == before

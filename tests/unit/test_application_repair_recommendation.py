"""Frozen vocabulary selects the default repair, without weakening checks — DR-167."""
import pytest

from application_contracts.adapters import service
from tests.robotics_application_integration_support import candidate, check_body
from tests.robotics_auto_adaptation_support import custom_candidate


@pytest.mark.parametrize('custom', [False, True])
def test_recommended_repair_uses_frozen_vocabulary_and_retains_both_actions(tmp_path, monkeypatch, custom):
    if custom:
        _, _, ref = custom_candidate(tmp_path, monkeypatch)
    else:
        _, _, ref = candidate(tmp_path, missing_role='ParameterBinding')
    report = service(tmp_path).evaluate(**check_body(ref))['report']
    gaps = [i for i in report['issues'] if i['layer'] == 'schema']
    assert gaps
    expected = 'robotics.adapt_execution' if custom else 'robotics.revise_tbox'
    for gap in gaps:
        actions = gap['repair_actions']
        assert actions[0]['action_id'] == expected
        assert {a['action_id'] for a in actions} == {'robotics.revise_tbox', 'robotics.adapt_execution'}
        assert all(a['availability'] == 'available' for a in actions)
    assert report['preparation_status'] == 'blocked'

"""Direct schema repair phases preserve confirmed classes and old versions — DR-070 / DR-072."""
from copy import deepcopy

import pytest

from tests.robotics_design_support import Harness
from tests.build_runtime_support import BASE_URL, http_app
from tests.build_pipeline_support import blob_bytes, db_rows


def test_edit_properties_retains_class_confirmation_and_full_property_snapshot(tmp_path):
    from domains.robotics.modeling import tbox
    h = Harness(tmp_path); original = h.candidate()
    state = h.service.get_revision(original['session_id'], original['revision'])['state']
    before = deepcopy(state)
    revised = tbox.edit_properties(state)
    assert state == before
    assert revised['stage'] == 'properties' and revised['revision'] == state['revision'] + 1
    assert revised['class_confirmation'] == state['class_confirmation']
    assert revised['result'] == state['result'] and revised['snapshot_sha256'] == state['snapshot_sha256']
    assert revised['schema_confirmation'] is None


@pytest.mark.parametrize('kinds,stage', [
    (['data_properties'], 'properties'), (['object_properties'], 'properties'),
    (['classes'], 'classes'), (['data_properties', 'classes'], 'classes'), (['unknown'], 'classes'),
])
def test_repair_stage_uses_all_gaps_and_prioritizes_classes(kinds, stage):
    from application_contracts import robotics_checks
    state = {'class_confirmation': {'confirmed': True}, 'vocabulary': {}, 'namespace': 'urn:test#'}
    issues = [{'expected': {'kind': kind}} for kind in kinds]
    assert robotics_checks.repair_stage(issues, state) == stage
    state['class_confirmation'] = None
    assert robotics_checks.repair_stage(issues, state) == 'classes'


def test_old_report_cpp_type_can_be_classified_using_frozen_vocabulary():
    from application_contracts import robotics_checks
    state = {'class_confirmation': {'confirmed': True}, 'namespace': 'urn:test#',
        'vocabulary': {'data_properties': [{'id': 'cppType'}], 'classes': [], 'object_properties': []}}
    issues = [{'expected': 'old diagnostic', 'entity_refs': ['urn:test#cppType']}]
    assert robotics_checks.repair_stage(issues, state) == 'properties'


def test_edit_properties_http_is_explicit_idempotent_and_keeps_archived_candidate(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); original = h.candidate()
        before, files = db_rows(root), blob_bytes(root)
        path = '/api/robotics/design-sessions/' + original['session_id'] + '/edit-properties'
        body = {'request_id': 'edit-properties', 'expected_revision': original['revision']}
        response = client.post(path, json=body, base_url=BASE_URL)
        assert response.status_code == 200, response.json
        current = response.json
        assert current['stage'] == 'properties' and current['status'] == 'awaiting_input'
        assert current['class_confirmation'] == original['class_confirmation']
        assert current['result'] == original['result'] and current['schema_confirmation'] is None
        assert current['output_asset_ref'] is None
        assert client.post(path, json=body, base_url=BASE_URL).json == current
        after = db_rows(root)
        for table in ('assets', 'versions', 'sources', 'runs', 'run_artifacts'):
            assert after[table] == before[table]
        assert len(after['design_revisions']) == len(before['design_revisions']) + 1
        assert all(blob_bytes(root)[path] == content for path, content in files.items())
        assert len(h.runtime.calls) == 2
        stale = client.post(path, json={**body, 'request_id': 'stale'}, base_url=BASE_URL)
        assert stale.json['error']['code'] == 'revision_conflict'


def test_properties_shortcut_cannot_bypass_unconfirmed_classes(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); original = h.classes()
        before, files = db_rows(root), blob_bytes(root)
        path = '/api/robotics/design-sessions/' + original['session_id'] + '/edit-properties'
        response = client.post(path, json={'request_id': 'skip-confirmation', 'expected_revision': original['revision']},
            base_url=BASE_URL)
        assert response.status_code == 409 and response.json['error']['code'] == 'confirmation_required'
        assert db_rows(root) == before and blob_bytes(root) == files


def test_property_repair_rejects_active_design_without_changing_queued_run(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        h = Harness(root); original = h.properties(); h.message(original)
        before, files = db_rows(root), blob_bytes(root)
        path = '/api/robotics/design-sessions/' + original['session_id'] + '/edit-properties'
        response = client.post(path, json={'request_id': 'busy-repair', 'expected_revision': original['revision']},
            base_url=BASE_URL)
        assert response.status_code == 409 and response.json['error']['code'] == 'session_busy'
        assert db_rows(root) == before and blob_bytes(root) == files

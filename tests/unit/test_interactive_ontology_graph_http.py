"""Fixed-version Ontology graph reads keep provenance, identity and write isolation — DR-166 T04."""
import pytest

from tests.build_runtime_support import BASE_URL, http_app
from tests.build_pipeline_support import db_rows, blob_bytes
from tests.robotics_task_support import fixture


def test_design_and_history_expose_interactive_graph_view_without_persisting_presentation(tmp_path):
    from tests.robotics_design_support import Harness
    with http_app(tmp_path) as (_, client, _, root):
        state = Harness(root).classes()
        path = '/api/robotics/design-sessions/' + state['session_id']
        before = db_rows(root), blob_bytes(root)
        current = client.get(path, base_url=BASE_URL)
        assert current.status_code == 200, current.json
        view = current.json['interactive_graph_view']
        assert view['schema'] == 'datamind-interactive-ontology-graph/1'
        assert view['context']['key'] == state['session_id']
        assert view['context']['revision'] == state['revision']
        assert view['metadata']['read_only'] is True
        historical = client.get(path + '/revisions/' + str(state['revision']), base_url=BASE_URL)
        assert historical.status_code == 200, historical.json
        assert historical.json['interactive_graph_view']['context']['status'] == 'historical'
        assert (db_rows(root), blob_bytes(root)) == before


def test_fixed_version_mismatch_and_deleted_asset_follow_existing_policy(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        path = f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}/views/interactive-ontology-graph"
        before = db_rows(root), blob_bytes(root)
        result = client.get(path, base_url=BASE_URL)
        assert result.status_code == 200, result.json
        view = result.json
        assert view['context']['revision'] == version['payload_sha256']
        assert view['ontology_id'] == version['ontology_id'] and view['version_id'] == version['version_id']
        assert view['schema'] == 'datamind-interactive-ontology-graph/1'
        assert view['individuals'] and any(n['type'] == 'class' for n in view['nodes'])
        assert (db_rows(root), blob_bytes(root)) == before
        assert client.get(path.replace(version['version_id'], 'missing'), base_url=BASE_URL).status_code == 404
        other = assets.create_asset(name='other', domain='robotics')
        assert client.get(path.replace(version['ontology_id'], other['ontology_id']), base_url=BASE_URL).status_code == 404
        (root / assets.get_version(version['ontology_id'], version['version_id'])['path']).write_bytes(b'bad')
        assert client.get(path, base_url=BASE_URL).status_code == 409


@pytest.mark.parametrize('view', ['interactive-ontology-graph', 'relations', 'behavior-tree'])
def test_interactive_ontology_graph_history_remains_readable_after_delete_without_reactivating_asset(tmp_path, view):
    from ontology_assets.lifecycle import LifecycleService
    from tests.unit.test_asset_lifecycle import apply
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        lifecycle = LifecycleService(root)
        oid = version['ontology_id']
        path = f"/api/ontology-assets/{oid}/versions/{version['version_id']}/views/{view}"
        active = client.get(path, base_url=BASE_URL)
        assert active.status_code == 200, active.json
        apply(lifecycle, oid)
        before = db_rows(root), blob_bytes(root)
        for query in ('', '?state=active'):
            denied = client.get(path + query, base_url=BASE_URL)
            assert denied.status_code == 409 and denied.json['error']['code'] == 'ontology_deleted'
        result = client.get(path + '?state=deleted', base_url=BASE_URL)
        assert result.status_code == 200, result.json
        assert (result.json['ontology_id'], result.json['version_id']) == (oid, version['version_id'])
        if view == 'interactive-ontology-graph':
            assert result.json['context']['revision'] == version['payload_sha256']
        assert lifecycle.get(oid)['state'] == 'deleted'
        assert (db_rows(root), blob_bytes(root)) == before
        apply(lifecycle, oid, operation='restore', request_id='restore-graph')
        restored = client.get(path, base_url=BASE_URL)
        assert restored.status_code == 200 and restored.json == active.json
        assert client.get(path + '?state=active', base_url=BASE_URL).json == restored.json


def test_graph_history_query_preserves_validation_and_integrity(tmp_path):
    from ontology_assets.lifecycle import LifecycleService
    from tests.unit.test_asset_lifecycle import apply
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        oid, vid = version['ontology_id'], version['version_id']
        path = f'/api/ontology-assets/{oid}/versions/{vid}/views/interactive-ontology-graph'
        apply(LifecycleService(root), oid)
        for query in ('state=all', 'state=', 'state=deleted&state=active', 'state=deleted&extra=1', 'mode=demo'):
            bad = client.get(path + '?' + query, base_url=BASE_URL)
            assert bad.status_code == 400 and bad.json['error']['code'] == 'invalid_request'
        history = path + '?state=deleted'
        assert client.get(history.replace(vid, 'missing'), base_url=BASE_URL).status_code == 404
        other = assets.create_asset(name='other', domain='robotics')
        assert client.get(history.replace(oid, other['ontology_id']), base_url=BASE_URL).status_code == 404
        data = assets.create_asset(name='data', domain='data')
        assert client.get(history.replace(oid, data['ontology_id']), base_url=BASE_URL).status_code == 400
        source = assets.get_source(version['source_asset_ids'][0])
        (root / source['path']).write_bytes(b'corrupt')
        damaged = client.get(history, base_url=BASE_URL)
        assert damaged.status_code == 409 and damaged.json['error']['code'] == 'artifact_integrity'


def test_interactive_ontology_graph_rejects_data_domain_and_corrupt_provenance(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        data = assets.create_asset(name='data', domain='data')
        path = f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}/views/interactive-ontology-graph"
        assert client.get(path.replace(version['ontology_id'], data['ontology_id']), base_url=BASE_URL).status_code == 400
        source = assets.get_source(version['source_asset_ids'][0])
        (root / source['path']).write_bytes(b'corrupt')
        assert client.get(path, base_url=BASE_URL).status_code == 409


def test_interactive_graph_rejects_a_complete_data_version_without_mutating_it(tmp_path):
    from tests.asset_support import add_data
    from ontology_assets import AssetStore
    with http_app(tmp_path) as (_, client, _, root):
        assets = AssetStore(root)
        asset, version = add_data(assets)
        path = (f"/api/ontology-assets/{asset['ontology_id']}/versions/"
                f"{version['version_id']}/views/interactive-ontology-graph")
        before = db_rows(root), blob_bytes(root)
        response = client.get(path, base_url=BASE_URL)
        assert response.status_code == 400, response.json
        assert (db_rows(root), blob_bytes(root)) == before
        assert assets.get_version(asset['ontology_id'], version['version_id']) == version

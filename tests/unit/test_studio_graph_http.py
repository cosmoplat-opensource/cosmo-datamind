"""Fixed-version Studio reads keep provenance, identity and write isolation — DR-159 T04."""
from tests.build_runtime_support import BASE_URL, http_app
from tests.build_pipeline_support import db_rows, blob_bytes
from tests.robotics_task_support import fixture


def test_fixed_version_mismatch_and_deleted_asset_follow_existing_policy(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        path = f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}/views/studio"
        before = db_rows(root), blob_bytes(root)
        result = client.get(path, base_url=BASE_URL)
        assert result.status_code == 200, result.json
        view = result.json
        assert view['context']['revision'] == version['payload_sha256']
        assert view['ontology_id'] == version['ontology_id'] and view['version_id'] == version['version_id']
        assert view['schema'] == 'datamind-studio/1'
        assert view['individuals'] and any(n['type'] == 'class' for n in view['nodes'])
        assert (db_rows(root), blob_bytes(root)) == before
        assert client.get(path.replace(version['version_id'], 'missing'), base_url=BASE_URL).status_code == 404
        other = assets.create_asset(name='other', domain='robotics')
        assert client.get(path.replace(version['ontology_id'], other['ontology_id']), base_url=BASE_URL).status_code == 404
        (root / assets.get_version(version['ontology_id'], version['version_id'])['path']).write_bytes(b'bad')
        assert client.get(path, base_url=BASE_URL).status_code == 409

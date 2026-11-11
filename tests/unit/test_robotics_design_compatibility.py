"""Candidate eligibility is independent of model availability — DR-070 / T06 RED."""
import json

import pytest

from domain_runs import RunStore
from domain_runs.robotics_task_service import TaskError, TaskService
from ontology_assets import AssetStore
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_design_support import Harness, assets_api, no_network, restore_v5
from tests.robotics_governance_support import actual_build
from tests.robotics_modeling_support import api, bundle, confirmation, ur_schema
from tests.robotics_task_support import model

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_application_catalog_distinguishes_supported_versions(tmp_path):
    h = Harness(tmp_path / 'store')
    design = h.candidate()
    _, old = actual_build(h.root, name='old')
    old_ref = old['output_asset_ref']
    reader = assets_api().DesignAssets(h.root, read_only=True)
    before = db_rows(h.root)
    denied = reader.compatibility(**design['output_asset_ref'])
    assert denied['status'] == 'unsupported' and denied['reasons']
    assert denied['payload_sha256'] == AssetStore(h.root).get_version(**design['output_asset_ref'])['payload_sha256']
    allowed = reader.compatibility(**old_ref)
    assert allowed['status'] == 'supported' and allowed['reasons'] == []
    assert allowed['payload_sha256'] == AssetStore(h.root).get_version(**old_ref)['payload_sha256']
    assert db_rows(h.root) == before


def test_task_service_rejects_saved_design_before_model_and_generation(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    client, transport = model()
    tasks = TaskService(h.root, model_client=client, generation_policy='offline')
    before, files = db_rows(h.root), blob_bytes(h.root)
    with pytest.raises(TaskError) as error:
        tasks.start(**state['output_asset_ref'], mode='real', request_id='cannot-bypass')
    assert error.value.code == 'invalid_ontology'
    assert db_rows(h.root) == before and blob_bytes(h.root) == files
    assert transport.calls == [] and len(h.runtime.calls) == 2
    assert {r['operation'] for r in RunStore(h.root).list_runs()} == {'design_tbox', 'extract_abox'}


def test_task_api_rejects_incompatible_rdf_even_with_forged_compatibility_metadata(tmp_path):
    # No new service dependency: existing HTTP consumer is the baseline gate.
    evidence, schema = bundle(), ur_schema()
    result = api('abox').extract(confirmation(schema, evidence), evidence)['result']
    raw = api('export').abox_owl(result, schema, evidence['evidence'])
    with http_app(tmp_path) as (_, client, _, root):
        assets = AssetStore(root)
        asset = assets.create_asset(name='forged-ready', domain='robotics')
        version = assets.add_version(asset['ontology_id'], raw, payload_kind='robotics_rdf',
            metadata={'application_compatibility': 'supported', 'artifact_contract': 'ur-pick-place-owl-v2'})
        before, files = db_rows(root), blob_bytes(root)
        response = client.post('/api/robotics/tasks', base_url=BASE_URL, json={
            'ontology_id': asset['ontology_id'], 'version_id': version['version_id'],
            'mode': 'real', 'request_id': 'bypass'})
        assert response.status_code == 422 and response.json['error']['code'] == 'invalid_ontology'
        assert db_rows(root) == before and blob_bytes(root) == files


def test_old_asset_links_and_downloads_remain_valid(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, old = actual_build(root, name='old')
        h = Harness(root)
        h.candidate()
        ref = old['output_asset_ref']
        detail = client.get(f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}", base_url=BASE_URL)
        assert detail.status_code == 200
        raw = AssetStore(root).read_payload(**ref)
        assert client.get(detail.json['payload_url'], base_url=BASE_URL).data == raw
        record = next(a for a in detail.json['artifacts'] if a['kind'] == 'ontology')
        download = client.get(record['download_url'], base_url=BASE_URL)
        assert download.status_code == 200 and download.data == raw
        assert RunStore(root).manifest(old['run_id'])['output_asset_ref'] == ref


def test_legacy_offline_task_policy_remains_unchanged(tmp_path):
    # Genuine legacy run + current task service, no model response provided.
    root = tmp_path / 'store'
    fixture = restore_v5(root)
    client, transport = model()
    service = TaskService(root, model_client=client, generation_policy='offline')
    state = service.start(**fixture['source_build_ref'], mode='real', request_id='old-offline')
    assert state['generation_policy'] == 'offline' and state['status'] == 'idle'
    assert transport.calls == []
    assert AssetStore(root).load_robotics_application(**fixture['source_build_ref'], mode='real')['validation']
    assert json.loads(db_rows(root)['robotics_task_sessions'][-1]['record'])['version_id'] == state['version_id']

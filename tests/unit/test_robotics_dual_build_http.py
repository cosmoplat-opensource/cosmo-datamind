"""Shared input admission and observable legacy behavior — DR-070 / IR-024 T02 RED."""
import io
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from rdflib import RDF

from domain_runs import RunStore
from domain_runs.robotics_build import compute_build
from ontology_assets import AssetStore
from tests.build_pipeline_support import blob_bytes, db_rows, sha
from tests.build_runtime_support import BASE_URL, http_app, multipart, post
from tests.robotics_dual_build_support import (
    RuntimeRegistry, ScriptedRuntime, isolated_platform, model_config,
)
from tests.robotics_support import MODELS, SOURCE, S, api, config, trees

__all__ = ['isolated_platform']


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('role', ['behavior_tree', 'node_models'])
def test_both_xml_roles_required_before_admission(tmp_path, isolated_platform, configured, role):
    runtime = ScriptedRuntime()
    isolated_platform(model_config() if configured else {},
                      RuntimeRegistry(openai=runtime) if configured else RuntimeRegistry())
    with http_app(tmp_path) as (_, client, _, root):
        data = multipart()
        data.poplist(role)
        before, files = db_rows(root), blob_bytes(root)
        response = post(client, data)
        assert response.status_code == 400 and response.json['error']['code'] == 'invalid_request'
        assert db_rows(root) == before and blob_bytes(root) == files
        assert runtime.calls == []


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('role', ['behavior_tree', 'node_models'])
@pytest.mark.parametrize('case', ['malformed', 'dtd', 'utf16_entity', 'depth', 'nodes'])
def test_invalid_xml_never_invokes_model_or_registers_sources(tmp_path, isolated_platform, configured, role, case):
    runtime = ScriptedRuntime()
    isolated_platform(model_config() if configured else {},
                      RuntimeRegistry(openai=runtime) if configured else RuntimeRegistry())
    values = {
        'malformed': b'<root>\n<broken></root>',
        'dtd': b'<!DOCTYPE root [<!ENTITY x "expanded">]><root>&x;</root>',
        'utf16_entity': '<!DOCTYPE root [<!ENTITY x "expanded">]><root>&x;</root>'.encode('utf-16'),
        'depth': b'<root>' + b'<n>' * 101 + b'</n>' * 101 + b'</root>',
        'nodes': b'<root>' + b'<n/>' * 20001 + b'</root>',
    }
    with http_app(tmp_path) as (_, client, _, root):
        data = multipart()
        data.setlist(role, [(io.BytesIO(values[case]), 'input.xml')])
        before, files = db_rows(root), blob_bytes(root)
        response = post(client, data)
        assert response.status_code == 400, response.get_data(as_text=True)
        assert response.json['error']['code'] == 'invalid_source'
        assert any(issue['source_role'] == role for issue in response.json['error']['issues'])
        assert db_rows(root) == before and blob_bytes(root) == files
        assert runtime.calls == []


@pytest.mark.parametrize('field', ['method', 'driver', 'model', 'base_url', 'api_key'])
def test_client_cannot_override_build_method_or_model(tmp_path, isolated_platform, field):
    with http_app(tmp_path) as (_, client, _, root):
        data = multipart()
        data[field] = 'untrusted-override'
        before = db_rows(root)
        response = post(client, data)
        assert response.status_code == 400 and response.json['error']['code'] == 'invalid_request'
        assert db_rows(root) == before


def test_incomplete_configuration_is_not_admitted_as_offline_success(tmp_path, isolated_platform):
    isolated_platform({'llm': {'base': 'https://model.invalid/v1'}})
    with http_app(tmp_path) as (_, client, _, root):
        before, files = db_rows(root), blob_bytes(root)
        response = post(client)
        assert response.status_code == 503, response.get_data(as_text=True)
        assert response.json['error']['code'] == 'configuration_error'
        assert db_rows(root) == before and blob_bytes(root) == files


def test_offline_admission_exposes_method_and_preserves_old_response(tmp_path, isolated_platform):
    with http_app(tmp_path) as (_, client, _, root):
        response = post(client)
        assert response.status_code == 202
        body = response.json
        assert body['method'] == 'legacy_xml' and body['model'] is None
        row = RunStore(root).get_run(body['run_id'])
        assert row['status'] == 'queued' and row['operation'] == 'build_ontology'
        assert row['output_asset_ref'] is None
        before = db_rows(root)
        replay = post(client)
        assert replay.status_code == 202 and replay.json == body
        assert db_rows(root) == before


def test_legacy_graph_and_application_semantics_preserved(tmp_path, isolated_platform):
    # Keep this independent of the new routing module, so the existing baseline
    # really runs during RED instead of failing early on a missing import.
    with http_app(tmp_path) as (_, client, _, root):
        response = post(client)
        assert response.status_code == 202
        runs, assets = RunStore(root), AssetStore(root)
        rid = response.json['run_id']
        runs.start(rid)
        product = compute_build(root, rid)
        saved = runs.finish_build(rid, **product)
        assert saved['status'] == 'completed' and saved['result']['status'] == 'built'
        assert saved['result']['execution_readiness'] == 'not_verified'
        assert saved['components']['model']['invoked'] is False
        content = assets.read_payload(**saved['output_asset_ref'])
        context = api('consumer').load_ontology_bytes(content, mode='real')
        assert context['validation']['artifact_contract'] == 'ur-pick-place-owl-v2'
        assert context['validation']['source_sha256'] == sha(SOURCE.read_bytes())
        assert context['validation']['models_sha256'] == sha(MODELS.read_bytes())
        assert trees(api('semantics').xml_from_graph(context['graph'])) == trees(ET.parse(SOURCE).getroot())
        assert len(set(context['graph'].subjects(RDF.type, S.SkillInvocation))) == 25
        draft = api('generation').create_authoring_draft(context, config(), output_dir=tmp_path / 'draft')
        assert draft['status'] == 'draft' and Path(draft['draft_path']).is_file()
        assert draft['execution_readiness'] == 'not_verified' and draft['candidate_written'] is False
        artifact = next(a for a in runs.artifacts(rid) if a['kind'] == 'ontology')
        detail = client.get(f'/api/domain-runs/{rid}', base_url=BASE_URL)
        link = next(a['download_url'] for a in detail.json['artifacts'] if a['artifact_id'] == artifact['artifact_id'])
        download = client.get(link, base_url=BASE_URL)
        assert download.status_code == 200 and download.data == content


def test_disabled_feature_does_not_admit_design_or_probe_model(tmp_path, isolated_platform):
    runtime = ScriptedRuntime()
    registry = isolated_platform(model_config(), RuntimeRegistry(openai=runtime))
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        response = post(client)
        assert response.status_code == 503 and response.json['error']['code'] == 'feature_disabled'
        assert not root.exists()
        assert runtime.calls == [] and registry.lookups == []

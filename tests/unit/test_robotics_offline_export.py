"""Lossless offline downloads keep old versions, source bytes and reports unchanged."""
import base64
from copy import deepcopy
import hashlib
import json
import shutil

import pytest
from rdflib import Graph
from rdflib.compare import isomorphic

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService
from tests.asset_support import add_data
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_governance_support import actual_build, publish_args, review, save_args


@pytest.fixture(scope='module')
def seed(tmp_path_factory):
    root = tmp_path_factory.mktemp('offline-export') / 'assets'
    version, run = actual_build(root)
    return root, version, run


@pytest.fixture
def live(tmp_path, seed):
    original, version, run = seed
    shutil.copytree(original, tmp_path / 'assets')
    with http_app(tmp_path) as (_, client, _, root):
        yield client, root, deepcopy(version), deepcopy(run)


def download(client, version):
    return client.get('/api/ontology-assets/' + version['ontology_id'] + '/versions/' + version['version_id']
                      + '/exports/json', base_url=BASE_URL)


def graph_view(client, version):
    path = (f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}"
            "/views/interactive-ontology-graph")
    return client.get(path, base_url=BASE_URL)


@pytest.mark.parametrize('limit', [None, 'MAX_INPUT_TRIPLES', 'MAX_TYPED_SUBJECTS'])
def test_offline_graph_checks_input_budget_before_single_unlocked_canonicalization(live, monkeypatch, limit):
    from rdflib.compare import to_canonical_graph
    from ontology_assets import interactive_ontology_graph_view as projection, robotics_offline
    from srv_context import _RDF_LOCK

    client, _, version, _ = live
    lock_states = []

    def counted(graph):
        lock_states.append(_RDF_LOCK._is_owned())
        return to_canonical_graph(graph)

    for module in (projection, robotics_offline):
        monkeypatch.setattr(module, 'to_canonical_graph', counted)
    if limit:
        monkeypatch.setattr(projection, limit, 1)
    response = graph_view(client, version)
    if limit:
        assert lock_states == []
        assert response.status_code == 409 and response.json['error']['code'] == 'artifact_integrity'
    else:
        assert response.status_code == 200, response.json
        assert response.json['context']['revision'] == version['payload_sha256']
        assert lock_states == [False]


def test_offline_complete_json_contains_exact_payload_sources_and_build_reports_without_writes(live):
    client, root, version, run = live
    assets = AssetStore(root, read_only=True); runs = RunStore(root, read_only=True)
    before = db_rows(root), blob_bytes(root)
    response = download(client, version)
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.mimetype == 'application/json' and 'attachment' in response.headers['Content-Disposition']
    doc = json.loads(response.data)
    assert doc['contract'] == {'id': 'robotics-offline-bundle', 'version': 1}
    assert doc['output_asset_ref'] == {k: version[k] for k in ('ontology_id', 'version_id')}
    raw = base64.b64decode(doc['payload_base64'], validate=True)
    assert raw == assets.read_payload(version['ontology_id'], version['version_id'])
    assert hashlib.sha256(raw).hexdigest() == doc['payload_sha256']
    assert doc['current']['tbox'] and doc['current']['abox']
    assert doc['method'] == 'legacy_xml' and 'model' not in doc
    assert doc['coverage'] == {'status': 'not_recorded'} and doc['execution_readiness'] == 'not_verified'
    assert {s['source_asset_id'] for s in doc['sources']} == set(version['source_asset_ids'])
    for source in doc['sources']:
        content = base64.b64decode(source['content_base64'], validate=True)
        assert content == assets.read_source(source['source_asset_id'])
        assert hashlib.sha256(content).hexdigest() == source['sha256']
    assert doc['build']['manifest'] == runs.manifest(run['run_id'])
    assert doc['build']['result'] == run['result']
    for artifact in doc['build']['artifacts']:
        assert base64.b64decode(artifact['content_base64']) == runs.read_artifact(run['run_id'], artifact['artifact_id'])
        assert 'path' not in artifact
    assert str(root) not in response.get_data(as_text=True)
    assert all('path' not in source for source in doc['sources'])
    assert (db_rows(root), blob_bytes(root)) == before


def test_historical_and_edited_offline_exports_bind_reports_to_original_build(live):
    client, root, original, run = live
    assets = AssetStore(root); first = download(client, original).data
    raw = assets.read_payload(original['ontology_id'], original['version_id']) + b'\n'
    latest = assets.add_version(original['ontology_id'], raw, payload_kind='robotics_rdf',
        parent_version_id=original['version_id'], source_asset_ids=original['source_asset_ids'], metadata=original['metadata'])
    before = db_rows(root), blob_bytes(root)
    doc = json.loads(download(client, latest).data)
    assert base64.b64decode(doc['payload_base64']) == raw
    assert doc['version_number'] == 2 and doc['parent_version_id'] == original['version_id']
    assert doc['build']['output_asset_ref'] == run['output_asset_ref']
    assert doc['build']['output_asset_ref']['version_id'] != latest['version_id']
    assert doc['validation'] is None
    assert download(client, original).data == first
    assert (db_rows(root), blob_bytes(root)) == before


@pytest.mark.parametrize('broken', ['payload', 'source', 'ontology', 'validation', 'manifest'])
def test_incomplete_offline_bundle_fails_instead_of_returning_partial_success(live, broken):
    client, root, version, run = live
    assets = AssetStore(root)
    if broken == 'payload':
        record = version
    elif broken == 'source':
        record = assets.get_source(version['source_asset_ids'][0])
    else:
        record = next(a for a in RunStore(root).artifacts(run['run_id']) if a['kind'] == broken)
    (root / record['path']).write_bytes(b'corrupt export fixture')
    before = db_rows(root), blob_bytes(root)
    response = download(client, version)
    assert response.status_code == 409 and response.json['error']['code'] == 'artifact_integrity'
    assert 'attachment' not in response.headers.get('Content-Disposition', '')
    owl = client.get('/api/ontology-assets/' + version['ontology_id'] + '/versions/' + version['version_id']
                     + '/payload', base_url=BASE_URL)
    assert owl.status_code == 409 and owl.json['error']['code'] == 'artifact_integrity'
    graph = graph_view(client, version)
    assert graph.status_code == 409 and graph.json['error']['code'] == 'artifact_integrity'
    assert (db_rows(root), blob_bytes(root)) == before


def test_export_cannot_use_another_asset_or_data_payload(live):
    client, root, version, _ = live
    other = AssetStore(root).create_asset(name='另一个资产', domain='robotics')
    assert download(client, {**version, 'ontology_id': other['ontology_id']}).status_code == 404
    _, data = add_data(AssetStore(root))
    assert download(client, data).status_code == 409


def test_bundle_current_facts_preserve_the_owl_graph(live):
    client, _, version, _ = live
    from domains.robotics._application_bundle import _node
    doc = json.loads(download(client, version).data)
    graph = Graph()
    for section in ('tbox', 'abox'):
        for triple in doc['current'][section]:
            graph.add(tuple(_node(term) for term in triple))
    assert isomorphic(graph, Graph().parse(data=base64.b64decode(doc['payload_base64']), format='xml'))


def test_older_offline_version_without_a_run_does_not_invent_a_report(live):
    client, root, original, _ = live
    assets = AssetStore(root)
    version = assets.add_version(original['ontology_id'], assets.read_payload(original['ontology_id'], original['version_id']),
        payload_kind='robotics_rdf', parent_version_id=original['version_id'],
        source_asset_ids=original['source_asset_ids'], metadata={'profile_id': 'ur_pick_place'})
    doc = json.loads(download(client, version).data)
    assert doc['build'] is None and doc['validation'] is None
    assert doc['current']['abox'] and doc['sources']


@pytest.mark.parametrize('corrupt', [False, True])
def test_published_version_includes_its_own_artifacts_in_addition_to_original_build(live, corrupt):
    client, root, original, _ = live
    store = GovernanceService(root)
    edited = store.save(original['ontology_id'], **save_args(original))
    checked = review(store, original['ontology_id'], edited)
    published = store.publish(original['ontology_id'], **publish_args(edited, checked))
    records = store.artifacts(original['ontology_id'], published['version_id'])
    if corrupt:
        (root / next(r for r in records if r['kind'] == 'validation')['path']).write_bytes(b'broken publication report')
        assert download(client, published).status_code == 409
        graph = graph_view(client, published)
        assert graph.status_code == 409 and graph.json['error']['code'] == 'artifact_integrity'
        return
    before = db_rows(root), blob_bytes(root)
    response = download(client, published)
    assert response.status_code == 200, response.get_data(as_text=True)
    doc = json.loads(response.data)
    assert {r['kind'] for r in doc['artifacts']} == {'ontology', 'validation', 'manifest'}
    assert doc['build']['output_asset_ref'] == {k: original[k] for k in ('ontology_id', 'version_id')}
    assert doc['metadata']['origin_build_run_id'] == original['metadata']['build_run_id']
    for item in doc['artifacts']:
        assert base64.b64decode(item['content_base64']) == store.read_artifact(
            original['ontology_id'], published['version_id'], item['artifact_id'])
    assert doc['validation'] == published['validation']
    assert (db_rows(root), blob_bytes(root)) == before

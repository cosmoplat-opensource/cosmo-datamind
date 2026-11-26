"""Persist a graph edit as an isolated candidate — DR-191."""
from concurrent.futures import ThreadPoolExecutor

from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef
from rdflib.compare import isomorphic

from ontology_assets.governance import GovernanceService
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_task_support import fixture

NEW = 'urn:example:added'
OP = dict(op='create', kind='class', name='新增类别', iri=NEW)


def endpoint(version):
    return f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}/graph-edit-copy/save"


def test_save_preserves_parent_and_is_idempotent(tmp_path):
    with http_app(tmp_path) as (app, client, _, root):
        assets, version, build = fixture(root, ready=True)
        oid, vid = version['ontology_id'], version['version_id']
        version = assets.publish_version(oid, vid, mode='real')
        original = assets.read_payload(oid, vid)
        before = assets.list_versions(oid)
        governance = GovernanceService(root)
        head = governance.head(oid)
        body = dict(base_sha256=version['payload_sha256'], operations=[OP])

        def submit(_):
            with app.test_client() as peer:
                result = peer.post(endpoint(version), json=body, base_url=BASE_URL)
                assert result.status_code == 200, result.json
                return result.json

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, retry = pool.map(submit, range(2))
        assert first == retry
        child = assets.get_version(oid, first['version_id'])
        assert child['parent_version_id'] == vid and child['publication_state'] == 'candidate'
        assert child['validation'] is None and child['publication_mode'] is None
        assert assets.list_versions(oid) == before + [child]
        assert assets.read_payload(oid, vid) == original and assets.get_version(oid, vid) == version
        assert governance.head(oid) == head
        assert assets.load_robotics(oid, vid)['validation']['ontology_sha256'] == version['payload_sha256']
        from domain_runs import RunStore
        assert RunStore(root).get_run(build['run_id'])['output_asset_ref'] == build['output_asset_ref']
        expected = Graph().parse(data=original, format='xml')
        expected.add((URIRef(NEW), RDF.type, OWL.Class))
        expected.add((URIRef(NEW), RDFS.label, Literal('新增类别', lang='zh')))
        assert isomorphic(expected, Graph().parse(data=assets.read_payload(oid, first['version_id']), format='xml'))
        next_edit = client.post(endpoint(child), json=dict(base_sha256=child['payload_sha256'],
            operations=[{**OP, 'iri': NEW + '2'}]), base_url=BASE_URL)
        assert next_edit.status_code == 200
        assert assets.get_version(oid, next_edit.json['version_id'])['parent_version_id'] == child['version_id']


def test_rejected_save_keeps_version_list(tmp_path):
    from ontology_assets.lifecycle import LifecycleService
    from tests.unit.test_asset_lifecycle import apply
    with http_app(tmp_path) as (_, client, runtime, root):
        assets, version, _ = fixture(root)
        oid = version['ontology_id']
        before = assets.list_versions(oid)
        body = dict(base_sha256=version['payload_sha256'], operations=[OP])
        def post(**changes):
            return client.post(endpoint(version), json={**body, **changes}, base_url=BASE_URL)
        assert post(base_sha256='0' * 64).status_code == 409
        assert post(operations=[]).status_code == 422
        assert post(operations=[OP, OP]).status_code == 422
        assert post(content='<rdf/>').status_code == 400
        runtime.enabled = False
        assert post().status_code == 503
        runtime.enabled = True
        apply(LifecycleService(root), oid)
        assert post().status_code == 409
        assert assets.list_versions(oid) == before


def test_design_save_preserves_inflight_adaptation(tmp_path):
    from application_contracts.adapters import service as application_checks
    from tests.robotics_application_integration_support import begin, candidate, check_body, confirm, submit
    from ontology_assets import AssetStore
    from ontology_assets.robotics_design import DesignAssets
    with http_app(tmp_path) as (_, client, _, root):
        _, _, ref = candidate(root)
        prepared = begin(root, ref)
        version = AssetStore(root).get_version(**ref)
        design = DesignAssets(root)
        original = design.export_json(**ref)
        checks = application_checks(root)
        report = checks.evaluate(**check_body(ref))['report']
        response = client.post(endpoint(version), json=dict(base_sha256=version['payload_sha256'],
                               operations=[OP]), base_url=BASE_URL)
        assert response.status_code == 200, response.json
        view = client.get(f"/api/ontology-assets/{ref['ontology_id']}/versions/{response.json['version_id']}"
                          "/views/interactive-ontology-graph", base_url=BASE_URL)
        assert view.status_code == 200 and NEW in {n['id'] for n in view.json['nodes']}
        assert design.export_json(**ref) == original
        assert checks.read(report['check_id'])['freshness'] == 'current'
        assert submit(root, confirm(root, prepared))['asset_ref'] == ref

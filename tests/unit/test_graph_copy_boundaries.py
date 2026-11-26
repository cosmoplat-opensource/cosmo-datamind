"""Saved graph copies stay out of the catalog head and application generation — DR-192."""
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_task_support import fixture

OP = dict(op='create', kind='class', name='新增类别', iri='urn:example:added')


def save(client, version, iri=OP['iri']):
    response = client.post(f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}"
                           '/graph-edit-copy/save', json=dict(base_sha256=version['payload_sha256'],
                           operations=[{**OP, 'iri': iri}]), base_url=BASE_URL)
    assert response.status_code == 200, response.json
    return response.json


def test_catalog_keeps_work_version_after_graph_save(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root, ready=True)
        oid, vid = version['ontology_id'], version['version_id']
        version = assets.publish_version(oid, vid, mode='real')
        before = [v['version_id'] for v in assets.list_versions(oid)]
        saved = save(client, version)

        def card():
            items = client.get('/api/ontology-assets', base_url=BASE_URL).json['items']
            return next(item for item in items if item.get('ontology_id') == oid)

        item = card()
        assert (item['version_id'], item['version_number'], item['publication_state']) == (
            vid, assets.version_number(oid, vid), 'published')
        assert [v['version_id'] for v in item['versions']] == before + [saved['version_id']]
        # A later regular version still becomes the catalog head.
        work = assets.add_version(oid, assets.read_payload(oid, vid), payload_kind='robotics_rdf',
                                  parent_version_id=vid)
        assert card()['version_id'] == work['version_id']


def test_application_copy_is_blocked_before_generation(tmp_path):
    from application_contracts.adapters import service as application_checks
    from application_contracts.models import ContractError
    from ontology_assets import AssetStore
    from tests.robotics_application_integration_support import adapted, check_body

    with http_app(tmp_path) as (_, client, _, root):
        _, _, state = adapted(root)
        assets = AssetStore(root)
        design, application = state['asset_ref'], state['output_asset_ref']
        copy = assets.get_version(**save(client, assets.get_version(**application)))
        nested = assets.get_version(**save(client, copy, 'urn:example:nested'))
        design_copy = save(client, assets.get_version(**design), 'urn:example:design')
        checks = application_checks(root)

        def report(ref, request_id):
            return checks.evaluate(**check_body({k: ref[k] for k in ('ontology_id', 'version_id')},
                                                request_id=request_id))['report']

        def blocked(result):
            return [i for i in result['issues'] if i['code'] == 'schema.application_copy']

        for index, ref in enumerate((copy, nested)):
            result = report(ref, f'copy-{index}')
            assert result['preparation_status'] == 'blocked'
            [issue] = blocked(result)
            assert '应用版本' in issue['message'] and issue['repair_actions'] == []
            assert result['layers']['schema']['summary'] == 'unsupported'
            try:
                checks.require_ready(entrypoint=result['entrypoint'], asset_refs=result['asset_refs'],
                                     context={'mode': 'real'})
            except ContractError as exc:
                assert exc.code == 'application_not_ready'
            else:
                raise AssertionError('application copy passed the generation guard')
        assert not blocked(report(application, 'original'))
        assert not blocked(report(design_copy, 'design-copy'))

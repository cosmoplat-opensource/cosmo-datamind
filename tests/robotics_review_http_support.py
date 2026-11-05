"""IR-019 HTTP fixtures with actual storage and RDF payloads — DR-059 / DR-061 / DR-064 / DR-065."""
import io

from werkzeug.datastructures import MultiDict

from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService
from tests.build_runtime_support import BASE_URL
from tests.robotics_governance_support import publish_args, review, save_args
from tests.robotics_support import SOURCE, MODELS
from tests.storage_pipeline_support import fresh_payload


def seed(root):
    assets = AssetStore(root)
    sources = [assets.put_source(path.read_bytes(), role=role, media_type='application/xml')
               for path, role in [(SOURCE, 'behavior_tree'), (MODELS, 'node_models')]]
    asset = assets.create_asset(name='HTTP 合成知识测试', domain='robotics')
    version = assets.add_version(asset['ontology_id'], fresh_payload('real'), payload_kind='robotics_rdf',
        source_asset_ids=[s['source_asset_id'] for s in sources],
        metadata={'profile_id': 'ur_pick_place', 'build_recipe': 'bt_xml'})
    return assets, asset['ontology_id'], version


def endpoint(oid, suffix):
    return f'/api/ontology-assets/{oid}/{suffix}'


def version_endpoint(oid, version, suffix=''):
    return endpoint(oid, 'versions/' + version['version_id'] + ('/' + suffix if suffix else ''))


def get(client, url, **kwargs):
    return client.get(url, base_url=BASE_URL, **kwargs)


def post(client, url, body):
    return client.post(url, json=body, base_url=BASE_URL)


def ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.is_json
    return response.get_json()


def error(response, status, code):
    body = ok(response, status)
    assert body['error']['code'] == code and body['error']['message']
    return body


def edit_args(version, **kwargs):
    args = save_args(version, **kwargs)
    args.pop('operations')
    return args


def candidate(root, oid, base):
    return GovernanceService(root).save(oid, **save_args(base))


def published(root, oid, base):
    service = GovernanceService(root)
    version = candidate(root, oid, base)
    reviewed = review(service, oid, version)
    return service.publish(oid, **publish_args(version, reviewed)), reviewed


def attachment(content=b'synthetic evidence; not a physical robot test', filename='report.txt'):
    return MultiDict([('file', (io.BytesIO(content), filename, 'text/plain'))])

"""IR-019 proposed contracts and independent fixture observations — DR-059 / DR-061 / DR-064 / DR-065."""
import base64
from copy import deepcopy
import importlib
import json
import sqlite3

from rdflib import Graph

from ontology_assets import AssetStore
from tests.build_pipeline_support import SNAPSHOT, sha
from tests.robotics_support import S, SOURCE, MODELS
from tests.storage_pipeline_support import fresh_payload

SNAPSHOT4 = SNAPSHOT.with_name('schema4_snapshot.json')
NOW = '2030-01-01T00:00:00+00:00'
LATER = '2030-01-03T00:00:00+00:00'
POLICY = 'robotics-governance-v1'
TASK = {'operation': 'pick_place', 'part': '零部件A', 'station': '工位B',
        'carrier': '载具B01', 'slot': '放置位B01', 'unsupported_requests': []}


def module(name):
    # Import inside the collected test, never skip missing implementation.
    return importlib.import_module(name)


def graph():
    return Graph().parse(data=fresh_payload('real'), format='xml')


def field(iri=S.PartType_A, name='label', value='人工零件类型'):
    return {'op': 'set_field', 'iri': str(iri), 'field': name, 'value': value,
            'basis': '合成测试资料；不代表现场适配'}


def relation(iri=S.PartType_A, name='hasPerceptionProfile', targets=None):
    return {'op': 'set_relation', 'iri': str(iri), 'relation': name,
            'targets': [str(S.Perception_Current)] if targets is None else [str(t) for t in targets],
            'basis': '合成测试的人工映射说明'}


def evidence_spec(**changes):
    return {'iri': str(S.TestEvidence), 'kind': 'grasp_compatibility',
            'implementation': str(S.grasp_Subtree_Impl), 'part_type': str(S.PartType_A),
            'slot': None, 'reference': '合成评审资料 TEST-ONLY-001；不证明现场能力',
            'source_asset_ids': [], 'valid_until': '2030-01-02T00:00:00+00:00', **changes}


def approved_evidence(api, source=None, **changes):
    original = graph() if source is None else source
    registered = api.register_evidence(original, evidence_spec(**changes), mode='real', now=NOW)
    return api.review_evidence(registered, str(S.TestEvidence), decision='accepted',
                              actor='合成测试审核人', reason='仅测试审核契约', now=NOW)


def setup(tmp_path):
    root = tmp_path / 'governance'
    assets = AssetStore(root)
    sources = [assets.put_source(p.read_bytes(), role=role, media_type='application/xml')
               for p, role in [(SOURCE, 'behavior_tree'), (MODELS, 'node_models')]]
    asset = assets.create_asset(name='合成治理测试', domain='robotics')
    version = assets.add_version(asset['ontology_id'], fresh_payload('real'), payload_kind='robotics_rdf',
        source_asset_ids=[s['source_asset_id'] for s in sources],
        metadata={'profile_id': 'ur_pick_place', 'build_recipe': 'bt_xml'})
    return root, assets, asset['ontology_id'], version


def save_args(version, *, request_id='save-1', revision=0, head=None, operations=None):
    return {'base_version_id': version['version_id'], 'base_payload_sha256': version['payload_sha256'],
            'expected_head_version_id': head or version['version_id'], 'expected_revision': revision,
            'request_id': request_id, 'actor': '合成测试编辑者', 'reason': '补充业务知识，非现场确认',
            'operations': [field()] if operations is None else deepcopy(operations)}


def review(service, oid, version, *, decision='accepted', request_id='review-1'):
    return service.review(oid, version['version_id'], payload_sha256=version['payload_sha256'],
        decision=decision, actor='合成测试评审人', reason='结构评审；任务证据仍缺失', request_id=request_id)


def publish_args(version, reviewed, *, revision=1, request_id='publish-1'):
    return {'version_id': version['version_id'], 'payload_sha256': version['payload_sha256'],
            'review_id': reviewed['review_id'], 'expected_head_version_id': version['version_id'],
            'expected_revision': revision, 'request_id': request_id,
            'actor': '合成测试发布人', 'reason': '发布结构，非执行就绪'}


def restore_v4(root):
    """Restore captured v4 SQL/bytes without calling current production constructors."""
    fixture = json.loads(SNAPSHOT4.read_text())
    assert fixture['schema_version'] == 4
    assert sha(SNAPSHOT.read_bytes()) == fixture['base_fixture_sha256']
    base = json.loads(SNAPSHOT.read_text())
    root.mkdir(parents=True)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        for entry in fixture['schema']:
            conn.execute(entry['sql'])
        for table, rows in fixture['rows'].items():
            for row in rows:
                keys = ','.join('"' + key + '"' for key in row)
                conn.execute(f'INSERT INTO "{table}" ({keys}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
        conn.execute('PRAGMA user_version=4')
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    for relative, item in fixture['blobs'].items():
        encoded = base['blobs'][item['base_fixture_path']]['base64'] if 'base_fixture_path' in item else item['base64']
        content = base64.b64decode(encoded)
        assert sha(content) == item['sha256']
        path = root / relative
        assert path.resolve().is_relative_to(root.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return fixture


def actual_build(root, *, name='new source', source=None):
    from domain_runs import RunStore
    from domain_runs.build_service import BuildService
    from domain_runs.robotics_build import compute_build
    run = BuildService(root).submit(name=name, recipe='bt_xml', profile_id='ur_pick_place', request_id=name,
        sources={'behavior_tree': {'filename': 'tree.xml', 'content': SOURCE.read_bytes() if source is None else source},
                 'node_models': {'filename': 'models.xml', 'content': MODELS.read_bytes()}})
    runs = RunStore(root)
    runs.start(run['run_id'])
    saved = runs.finish_build(run['run_id'], **compute_build(root, run['run_id']))
    assert saved['result']['status'] == 'built'
    ref = saved['output_asset_ref']
    return AssetStore(root).get_version(**ref), saved

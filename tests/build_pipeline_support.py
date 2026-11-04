"""T02 build contracts, independent observations and frozen schema-2 restore — DR-060 / DR-064 / DR-065."""
import base64
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

from domain_runs import RunStore
from ontology_assets import AssetStore
from tests.robotics_support import SOURCE, MODELS
from tests.run_support import components

SNAPSHOT = Path(__file__).parent / 'fixtures/domain_storage/schema2_snapshot.json'


def sha(content):
    return hashlib.sha256(content).hexdigest()


def compute():
    # Missing implementation fails a collected case; never skip or fabricate success.
    return importlib.import_module('domain_runs.robotics_build').compute_build


def inputs(assets, source=None, models=None):
    return [assets.put_source(content, role=role, media_type='application/xml')
            for role, content in [('behavior_tree', SOURCE.read_bytes() if source is None else source),
                                  ('node_models', MODELS.read_bytes() if models is None else models)]]


def setup(tmp_path, *, source=None, models=None):
    root = tmp_path / 'build-storage'
    assets, runs = AssetStore(root), RunStore(root)
    sources = inputs(assets, source, models)
    args = {'name': '抓移放候选', 'recipe': 'bt_xml', 'profile_id': 'ur_pick_place',
            'source_asset_ids': [s['source_asset_id'] for s in sources],
            'session_ref': {'namespace': 'robotics_build', 'id': 'build-test'},
            'components': {**components(), 'profile_id': 'ur_pick_place'}}
    return root, assets, runs, sources, args


def begin(tmp_path, **changes):
    root, assets, runs, sources, args = setup(tmp_path, **changes)
    run = runs.create_build_run(**args)
    runs.start(run['run_id'])
    return root, assets, runs, sources, runs.get_run(run['run_id'])


def db_rows(root):
    """Read durable visibility without the production store's caches/decoders."""
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: [dict(row) for row in conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid')]
                for table in tables}


def blob_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and p.name not in ('domain.sqlite3', 'domain.sqlite3-journal',
                                             'domain.sqlite3-wal', 'domain.sqlite3-shm')}


def check_complete(root, before, saved):
    runs, assets = RunStore(root), AssetStore(root)
    rid = saved['run_id']
    assert runs.get_run(rid) == saved
    assert saved['status'] == 'completed' and saved['result']['status'] == 'built'
    assert saved['ontology_id'] is None and saved['version_id'] is None
    assert saved['ontology_snapshot'] is None
    assert saved['build_input'] == before['build_input']
    ref = saved['output_asset_ref']
    assert set(ref) == {'ontology_id', 'version_id'}
    version = assets.get_version(**ref)
    assert version['publication_state'] == 'candidate' and version['publication_mode'] is None
    assert version['source_asset_ids'] == [s['source_asset_id'] for s in before['build_input']['sources']]
    assert version['metadata']['build_run_id'] == rid
    assert version['metadata']['profile_id'] == 'ur_pick_place'
    assert saved['result']['execution_readiness'] == 'not_verified'
    assert saved['result']['task_readiness'] == 'not_assessed'
    artifacts = runs.artifacts(rid)
    assert sorted(a['kind'] for a in artifacts) == ['manifest', 'ontology', 'validation']
    for artifact in artifacts:
        data = runs.read_artifact(rid, artifact['artifact_id'])
        assert sha(data) == artifact['sha256'] and len(data) == artifact['byte_size']
        if artifact['kind'] == 'ontology':
            assert data == assets.read_payload(**ref)
            assert artifact['sha256'] == version['payload_sha256']
        if artifact['kind'] == 'validation':
            assert json.loads(data) == saved['result']
    manifest = runs.manifest(rid)
    assert manifest['build_input'] == before['build_input']
    assert manifest['output_asset_ref'] == ref
    assert manifest['result'] == saved['result']
    assert str(root) not in json.dumps(manifest, ensure_ascii=False)
    events = runs.events(rid)
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    assert [e['payload']['status'] for e in events if e['kind'] == 'state'] == ['queued', 'running', 'completed']
    return version


def failure_result(code='build_error'):
    return {'status': 'error', 'execution_readiness': 'not_verified', 'task_readiness': 'not_assessed',
            'issues': [{'code': code, 'message': '测试构建失败', 'source_role': None,
                        'source_asset_id': None, 'location': {'kind': 'unknown'}}]}


def bad_xml(case):
    source, models = ET.parse(SOURCE).getroot(), ET.parse(MODELS).getroot()
    role, code, location = 'behavior_tree', '', {}
    if case == 'malformed_tree':
        return b'<root>\n<broken></root>', MODELS.read_bytes(), role, 'invalid_xml', {'line': 2}
    if case == 'malformed_models':
        return SOURCE.read_bytes(), b'<root>\n<broken></root>', 'node_models', 'invalid_xml', {'line': 2}
    if case in ('dtd', 'utf16_entity'):
        content = '<!DOCTYPE root [<!ENTITY secret SYSTEM "file:///not-readable">]><root>&secret;</root>'
        return content.encode('utf-16' if case == 'utf16_entity' else 'utf-8'), MODELS.read_bytes(), role, 'forbidden_xml_declaration', {}
    if case == 'unknown_node':
        source.find("BehaviorTree[@ID='Perception']/Sequence")[0].tag = 'UnknownMotion'
        code, location = 'unknown_node', {'tree_id': 'Perception', 'node_tag': 'UnknownMotion', 'step_index': 1}
    elif case == 'model_conflict':
        models.find("TreeNodesModel/Action[@ID='MoveJ']/input_port[@name='target']").set('type', 'double')
        role, code, location = 'node_models', 'model_conflict', {'model_id': 'MoveJ', 'port': 'target'}
    elif case == 'duplicate_port':
        model = models.find("TreeNodesModel/Action[@ID='MoveJ']")
        model.append(deepcopy(model.find("input_port[@name='target']")))
        role, code, location = 'node_models', 'invalid_port', {'model_id': 'MoveJ', 'port': 'target'}
    elif case == 'unknown_port':
        source.find("BehaviorTree[@ID='Perception']/Sequence")[0].set('bogus_port', '1')
        code, location = 'invalid_port', {'tree_id': 'Perception', 'node_tag': 'MoveJ', 'step_index': 1, 'port': 'bogus_port'}
    elif case == 'missing_port':
        del source.find("BehaviorTree[@ID='Perception']/Sequence")[0].attrib['target']
        code, location = 'invalid_port', {'tree_id': 'Perception', 'node_tag': 'MoveJ', 'step_index': 1, 'port': 'target'}
    elif case == 'template':
        source.find("BehaviorTree[@ID='Perception']/Sequence").tag = 'Fallback'
        code, location = 'unsupported_template', {'tree_id': 'Perception'}
    else:
        raise AssertionError(case)
    return ET.tostring(source), ET.tostring(models), role, code, location


def restore_v2(root):
    """Restore frozen SQL/bytes directly; never invoke today's schema initializer."""
    fixture = json.loads(SNAPSHOT.read_text())
    assert fixture['schema_version'] == 2
    root.mkdir(parents=True)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        for item in fixture['schema']:
            conn.execute(item['sql'])
        for table, rows in fixture['rows'].items():
            for row in rows:
                keys = ','.join('"' + k + '"' for k in row)
                placeholders = ','.join('?' for _ in row)
                conn.execute(f'INSERT INTO "{table}" ({keys}) VALUES ({placeholders})', tuple(row.values()))
        conn.execute('PRAGMA user_version=2')
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    for relative, item in fixture['blobs'].items():
        content = base64.b64decode(item['base64'])
        assert sha(content) == item['sha256']
        target = root / relative
        assert target.resolve().is_relative_to(root.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return fixture

"""Fixed-version detail, truthful history and concurrent read availability — DR-155 / T04 RED."""
from copy import deepcopy
import json
import sqlite3
import threading

import pytest
from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef

from domain_runs import RunStore
from domain_runs.robotics_design_service import DesignService
from domain_runs.robotics_task_service import TaskService
from ontology_assets import AssetStore
from ontology_assets.robotics_application import ApplicationAssets
from ontology_assets.robotics_design import DesignAssets
from ontology_assets.storage import Storage
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.ontology_detail_integration_support import clone_seed, create_check, detail_path, prepare_seed, ref, stale_check
from tests.robotics_governance_support import TASK
from tests.robotics_support import S


@pytest.fixture(scope='module')
def detail_seed(tmp_path_factory):
    root = tmp_path_factory.mktemp('detail-http-seed') / 'assets'
    return root, prepare_seed(root)


@pytest.fixture
def live_http(tmp_path, detail_seed):
    info = clone_seed(detail_seed, tmp_path / 'assets')
    with http_app(tmp_path) as (app, client, _, root):
        yield app, client, root, info


def get(client, path):
    return client.get(path, base_url=BASE_URL)


def ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)[:2000]
    return response.get_json()


def test_design_projection_uses_verified_schema_and_fixed_instance_identities_without_writes(live_http):
    _, client, root, info = live_http
    record = info['design']; before = db_rows(root), blob_bytes(root)
    document = DesignAssets(root).detail(**record)
    body = ok(get(client, detail_path(record))); view = body['detail_view']
    assert all(view[key] == body[key] for key in ('ontology_id', 'version_id', 'payload_sha256'))
    tbox, abox = document['tbox'], document['abox']
    assert {row['id'] for row in view['classes']} == {tbox['namespace'] + row['id'] for row in tbox['classes']}
    assert {row['id'] for row in view['individuals']} == {tbox['namespace'] + 'individual/' + row['id'] for row in abox['individuals']}
    assert len(view['object_assertions']) == len(abox['object_assertions'])
    assert len(view['data_assertions']) == len(abox['data_assertions'])
    assert body['application_compatibility']['status'] == 'not_assessed'
    assert (db_rows(root), blob_bytes(root)) == before


def test_application_projection_reads_completed_current_facts_instead_of_parent_design(live_http):
    _, client, root, info = live_http
    record = info['current_application']; before = db_rows(root), blob_bytes(root)
    document = ApplicationAssets(root).detail(**record)
    view = ok(get(client, detail_path(record)))['detail_view']
    workpieces = [row for row in view['individuals'] if str(S.Workpiece) in row['class_ids']]
    assert any(row['label'] == TASK['part'] and row['group'] == 'business' for row in workpieces)
    parent = document['origin']['design_snapshot']['abox']['individuals']
    assert not any(row['label'] == TASK['part'] and 'Workpiece' in row['class_ids'] for row in parent)
    assert len({row['id'] for row in view['individuals']}) == len(view['individuals'])
    assert (db_rows(root), blob_bytes(root)) == before


def test_projection_deduplicates_multiple_types_and_does_not_guess_groups_by_local_name(live_http):
    _, client, root, _ = live_http
    graph = Graph()
    fake = URIRef('urn:untrusted#Workpiece')
    for cls in (fake, S.Workpiece, S.SkillInvocation):
        graph.add((cls, RDF.type, OWL.Class)); graph.add((cls, RDFS.label, Literal('同名类别')))
    real, misleading = URIRef('urn:instance:real'), URIRef('urn:instance:other')
    for individual in (real, misleading):
        graph.add((individual, RDF.type, OWL.NamedIndividual))
    graph.add((real, RDF.type, S.Workpiece)); graph.add((real, RDF.type, S.SkillInvocation))
    graph.add((misleading, RDF.type, fake)); graph.add((real, S.relatedTo, misleading))
    assets = AssetStore(root); asset = assets.create_asset(name='合成同名词边界', domain='robotics')
    version = assets.add_version(asset['ontology_id'], graph.serialize(format='xml', encoding='utf-8'), payload_kind='robotics_rdf')
    view = ok(get(client, detail_path(version)))['detail_view']
    assert len(view['classes']) == 3 and len(view['individuals']) == 2
    assert {row['id']: row['group'] for row in view['individuals']} == {str(real): 'business', str(misleading): 'other'}
    assert len(view['object_assertions']) == 1 and not view['data_assertions']


@pytest.mark.parametrize('kind', ['legacy', 'design', 'application', 'current_application'])
def test_owl_download_remains_bound_to_the_requested_version(live_http, kind):
    _, client, root, info = live_http
    record = info[kind]; before = db_rows(root), blob_bytes(root)
    expected = AssetStore(root).read_payload(**record)
    body = ok(get(client, detail_path(record)))
    response = get(client, body['payload_url'])
    assert response.status_code == 200 and response.data == expected
    assert body['version_id'] == record['version_id'] and body['detail_view']['version_id'] == record['version_id']
    assert (db_rows(root), blob_bytes(root)) == before


@pytest.mark.parametrize('kind', ['design', 'application', 'current_application'])
def test_complete_json_download_remains_bound_to_the_requested_version(live_http, kind):
    _, client, root, info = live_http
    record = info[kind]
    reader = DesignAssets(root) if kind == 'design' else ApplicationAssets(root)
    before = db_rows(root), blob_bytes(root)
    response = get(client, detail_path(record) + '/exports/json')
    assert response.status_code == 200 and response.data == reader.export_json(**record)
    assert json.loads(response.data)['output_asset_ref'] == record
    assert (db_rows(root), blob_bytes(root)) == before


def test_data_detail_keeps_original_ir_kind_without_robotics_projection(live_http):
    _, client, root, info = live_http
    before = db_rows(root), blob_bytes(root)
    body = ok(get(client, detail_path(info['data'])))
    assert body['payload_kind'] == 'datamind_ir' and body['asset']['domain'] == 'data'
    assert 'detail_view' not in body and 'application_compatibility' not in body
    assert (db_rows(root), blob_bytes(root)) == before


@pytest.mark.parametrize('case', ['missing', 'wrong_asset', 'corrupt_payload', 'unknown_design', 'unknown_application'])
def test_invalid_or_mismatched_artifacts_are_rejected_without_latest_or_legacy_fallback(live_http, case):
    _, client, root, info = live_http
    assets = AssetStore(root); record = deepcopy(info['design']); status = 409
    if case == 'missing':
        record['version_id'] = 'ver_missing'; status = 404
    elif case == 'wrong_asset':
        record['ontology_id'] = info['legacy']['ontology_id']; status = 404
    elif case == 'corrupt_payload':
        stored = assets.get_version(**record)
        (root / stored['path']).write_bytes(b'<corrupt/>')
    else:
        original = assets.get_version(**record)
        metadata = deepcopy(original['metadata'])
        metadata['application_contract' if case == 'unknown_application' else 'design_contract'] = {'id': 'unknown', 'version': 999}
        record = ref(assets.add_version(record['ontology_id'], assets.read_payload(**record), payload_kind='robotics_rdf',
            parent_version_id=record['version_id'], source_asset_ids=original['source_asset_ids'], metadata=metadata))
    before = db_rows(root), blob_bytes(root)
    body = ok(get(client, detail_path(record)), status)
    assert body['error']['code'] == ('not_found' if status == 404 else 'artifact_integrity')
    assert 'detail_view' not in body and (db_rows(root), blob_bytes(root)) == before


def test_stale_report_preserves_historical_conclusion_on_read(live_http):
    _, client, root, info = live_http
    response = stale_check(root, info); check_id = response['report']['check_id']
    before = db_rows(root), blob_bytes(root)
    read = ok(get(client, '/api/application-checks/' + check_id))
    assert read['freshness'] == 'stale' and read['report'] == response['report']
    body = ok(get(client, detail_path(info['legacy'])))
    assert body['application_compatibility']['check_id'] == check_id
    assert body['application_compatibility']['freshness'] == 'stale'
    assert (db_rows(root), blob_bytes(root)) == before


def test_stale_repair_deep_link_cannot_change_design_or_accept_adaptation(live_http):
    _, client, root, info = live_http
    response = create_check(root, info['blocked'])
    issue = next(issue for issue in response['report']['issues'] if any(
        action['action_id'] == 'robotics.revise_tbox' for action in issue['repair_actions']))
    action = next(action for action in issue['repair_actions'] if action['action_id'] == 'robotics.revise_tbox')
    DesignService(root).edit_classes(info['blocked_session'], expected_revision=action['target']['expected_revision'],
        request_id='new-design-revision')
    before = db_rows(root), blob_bytes(root)
    reply = client.post('/api/application-checks/' + response['report']['check_id'] + '/repairs',
        json={'issue_id': issue['issue_id'], 'action_id': action['action_id'], 'input': {},
              'expected_revision': action['target']['expected_revision'], 'request_id': 'stale-repair'}, base_url=BASE_URL)
    assert ok(reply, 409)['error']['code'] == 'stale_result'
    assert (db_rows(root), blob_bytes(root)) == before


@pytest.mark.parametrize('field', ['ready', 'check_id', 'report'])
def test_client_report_fields_cannot_authorize_task_creation(live_http, field):
    _, client, root, info = live_http
    before = db_rows(root), blob_bytes(root)
    reply = client.post('/api/robotics/tasks', json={**info['legacy'], 'mode': 'real', 'request_id': 'forged-ready',
        field: True if field == 'ready' else 'untrusted'}, base_url=BASE_URL)
    assert ok(reply, 400)['error']['code'] == 'invalid_request'
    assert (db_rows(root), blob_bytes(root)) == before


def test_missing_schema_has_two_bound_repair_routes_without_creating_versions(live_http):
    _, client, root, info = live_http
    response = create_check(root, info['blocked'])
    assert response['report']['preparation_status'] == 'blocked'
    actions = [action for issue in response['report']['issues'] for action in issue['repair_actions']]
    revise = next(action for action in actions if action['action_id'] == 'robotics.revise_tbox')
    mapping = next(action for action in actions if action['action_id'] == 'robotics.adapt_execution')
    assert revise['target']['resource_id'] == info['blocked_session']
    assert mapping['target']['ontology_id'] == info['blocked']['ontology_id']
    assert mapping['target']['version_id'] == info['blocked']['version_id']
    before = db_rows(root), blob_bytes(root)
    ok(get(client, '/api/application-checks/' + response['report']['check_id']))
    assert (db_rows(root), blob_bytes(root)) == before


@pytest.mark.parametrize('kind', ['legacy_task', 'application_task'])
def test_existing_generation_and_xml_remain_readable_after_later_task_revision(live_http, kind):
    _, client, root, info = live_http
    task = info[kind]
    runs = RunStore(root); run = runs.get_run(task['run_id'])
    artifact = next(row for row in runs.artifacts(run['run_id']) if row['kind'] == 'candidate')
    expected = runs.read_artifact(run['run_id'], artifact['artifact_id'])
    TaskService(root).save_notes(task['session_id'], notes=[{'kind': 'grasp_compatibility', 'text': '历史生成后追加的合成备注'}],
        expected_revision=task['revision'], request_id='later-task-note')
    before = db_rows(root), blob_bytes(root)
    base = f"/api/robotics/tasks/{task['session_id']}/generations/{task['run_id']}"
    saved = ok(get(client, base))
    assert saved['version_id'] == run['version_id'] and saved['tree'] and saved['read_only'] is True
    assert get(client, base + '/candidate').data == expected
    assert (db_rows(root), blob_bytes(root)) == before


@pytest.mark.parametrize('kind', ['candidate', 'current_owl'])
def test_history_availability_does_not_relax_application_snapshot_or_candidate_integrity(live_http, kind):
    _, client, root, info = live_http; task = info['application_task']
    if kind == 'candidate':
        record = next(row for row in RunStore(root).artifacts(task['run_id']) if row['kind'] == 'candidate')
    else:
        record = AssetStore(root).get_version(**ref(task))
    (root / record['path']).write_bytes(b'corrupted immutable fixture')
    before = db_rows(root), blob_bytes(root)
    base = f"/api/robotics/tasks/{task['session_id']}/generations/{task['run_id']}"
    for suffix in ('', '/candidate'):
        assert ok(get(client, base + suffix), 409)['error']['code'] == 'artifact_integrity'
    assert (db_rows(root), blob_bytes(root)) == before


def test_slow_historical_snapshot_read_does_not_block_unrelated_database_commit(live_http, monkeypatch):
    app, _, root, info = live_http
    task = info['application_task']; version = AssetStore(root).get_version(**ref(task))
    entered, release = threading.Event(), threading.Event()
    original = Storage.read_bytes; observed = {}

    def delayed(storage, relative, *args, **kwargs):
        if (storage.root == root and relative == version['path'] and threading.current_thread().name == 'history-reader'
                and not entered.is_set()):
            entered.set()
            assert release.wait(8), 'The concurrency control must release the real file read.'
        return original(storage, relative, *args, **kwargs)

    monkeypatch.setattr(Storage, 'read_bytes', delayed)

    def read():
        try:
            with app.test_client() as client:
                response = get(client, f"/api/robotics/tasks/{task['session_id']}/generations/{task['run_id']}")
                observed['status'] = response.status_code
        except Exception as error:
            observed['error'] = repr(error)

    thread = threading.Thread(target=read, name='history-reader', daemon=True)
    thread.start(); writer_error = None
    try:
        assert entered.wait(5), 'The real history read must reach its immutable payload.'
        with sqlite3.connect(root / 'domain.sqlite3', timeout=.2) as writer:
            writer.execute('UPDATE assets SET name=? WHERE ontology_id=?', ('并发写入已提交', info['data']['ontology_id']))
            writer.commit()
    except sqlite3.OperationalError as error:
        writer_error = str(error)
    finally:
        release.set(); thread.join(timeout=15)
    assert not thread.is_alive() and observed.get('status') == 200, observed
    assert writer_error is None, f'Historical file verification blocked an unrelated metadata commit: {writer_error}'


def test_new_browser_suite_is_registered_with_full_regression():
    import ast
    from pathlib import Path
    source = ast.parse((Path(__file__).resolve().parents[2] / 'scripts/check.py').read_text())
    calls = [node for node in ast.walk(source) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'run_check']
    assert any(any(isinstance(node, ast.Constant) and node.value == 'tests/frontend/ontology-detail-browser.py'
                   for node in ast.walk(call)) for call in calls), 'T05 must wire actual detail browser regression into --suite all.'

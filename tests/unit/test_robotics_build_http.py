"""T04 RED: robotics Blueprint contracts with real stores and application guards — DR-060 / DR-063 / DR-064 / DR-065."""
from copy import deepcopy
import io
import json
import subprocess
import sys

import pytest

from domain_runs import RunStore
from domain_runs.robotics_build import compute_build
from ontology_assets import AssetStore
from tests.asset_support import add_data, data_bytes
from tests.build_pipeline_support import blob_bytes, db_rows, failure_result, sha
from tests.build_runtime_support import BASE_URL, ROOT, http_app, multipart, post, terminal, upload_args


def get(client, path):
    return client.get(path, base_url=BASE_URL)


def assert_error(response, status, code):
    assert response.status_code == status, response.get_data(as_text=True)
    body = response.get_json()
    assert body['error']['code'] == code and body['error']['message']
    return body


def seed_completed(runtime, root, request_id='completed'):
    row = runtime.service.submit(**upload_args(request_id))
    runs = RunStore(root)
    runs.start(row['run_id'])
    return runs.finish_build(row['run_id'], **compute_build(root, row['run_id']))


def assert_public(body, root):
    assert str(root) not in json.dumps(body, ensure_ascii=False)
    if isinstance(body, dict):
        assert not {'path', 'storage_root', 'database_path', 'command'} & set(body)
        for value in body.values():
            assert_public(value, root)
    elif isinstance(body, list):
        for value in body:
            assert_public(value, root)


def test_disabled_capabilities_and_writes_do_not_initialize_storage(tmp_path):
    with http_app(tmp_path, enabled=False) as (_, client, runtime, root):
        caps = get(client, '/api/robotics/capabilities')
        assert caps.status_code == 200 and caps.json['enabled'] is False
        assert_error(post(client), 503, 'feature_disabled')
        retry = client.post('/api/domain-runs/run_missing/retry', json={'request_id': 'retry'}, base_url=BASE_URL)
        assert_error(retry, 503, 'feature_disabled')
        assert not root.exists()
        assert runtime.executor is None


def test_enabled_capabilities_and_upload_have_explicit_roles_and_limits(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        caps = get(client, '/api/robotics/capabilities').json
        assert caps['enabled'] is True
        recipe = next(r for r in caps['recipes'] if r['id'] == 'bt_xml')
        assert recipe['profiles'] == ['ur_pick_place']
        assert set(recipe['input_roles']) == {'behavior_tree', 'node_models'}
        assert caps['limits'] == {'file_bytes': 10 * 1024 * 1024, 'pending': 4, 'timeout_seconds': 30}
        response = post(client)
        assert response.status_code == 202
        body = response.json
        row = RunStore(root).get_run(body['run_id'])
        assert row['status'] == 'queued' and row['output_asset_ref'] is None
        assert {s['role'] for s in row['build_input']['sources']} == {'behavior_tree', 'node_models'}
        assert len({s['source_asset_id'] for s in row['build_input']['sources']}) == 2
        assert_public(body, root)
        assert not (tmp_path / 'work/uploads.db').exists()
        assert not list((tmp_path / 'work').glob('uploads_*'))


@pytest.mark.parametrize('bad', ['missing_tree', 'missing_models', 'duplicate_role', 'extra_role',
                                'duplicate_field', 'unknown_field', 'empty_file', 'empty_name',
                                'missing_request_id', 'bad_recipe', 'bad_profile', 'source_path'])
def test_malformed_upload_is_rejected_before_any_run_or_source_registration(tmp_path, bad):
    with http_app(tmp_path) as (_, client, _, root):
        data = multipart()
        if bad == 'missing_tree':
            data.poplist('behavior_tree')
        elif bad == 'missing_models':
            data.poplist('node_models')
        elif bad == 'duplicate_role':
            data.add('behavior_tree', (io.BytesIO(b'<root/>'), 'extra.xml'))
        elif bad == 'extra_role':
            data.add('demo_supplement', (io.BytesIO(b'{}'), 'demo.json'))
        elif bad == 'duplicate_field':
            data.add('profile', 'ur_pick_place')
        elif bad == 'unknown_field':
            data.add('arbitrary_rules', 'untrusted rules')
        elif bad == 'empty_file':
            data.setlist('behavior_tree', [(io.BytesIO(b''), 'empty.xml')])
        elif bad == 'empty_name':
            data['name'] = '  '
        elif bad == 'missing_request_id':
            data.poplist('request_id')
        elif bad == 'bad_recipe':
            data['recipe'] = 'sql'
        elif bad == 'bad_profile':
            data['profile'] = 'unsupported_robot'
        else:
            data.poplist('behavior_tree')
            data.add('behavior_tree', '/etc/passwd')
        before = db_rows(root)
        assert_error(post(client, data), 400, 'invalid_request')
        assert db_rows(root) == before


@pytest.mark.parametrize('limit_kind', ['file', 'request'])
def test_upload_size_limit_returns_413_without_partial_admission(tmp_path, limit_kind):
    config = {'DATAMIND_ROBOTICS_FILE_LIMIT_BYTES': 16} if limit_kind == 'file' else {'MAX_CONTENT_LENGTH': 128}
    with http_app(tmp_path, **config) as (_, client, _, root):
        before = db_rows(root)
        response = post(client)
        assert response.status_code == 413
        assert db_rows(root) == before


def test_idempotent_upload_conflict_and_queue_full_http_statuses(tmp_path):
    with http_app(tmp_path, DATAMIND_ROBOTICS_MAX_PENDING=1) as (_, client, _, root):
        first = post(client)
        assert first.status_code == 202
        before = db_rows(root)
        repeated = post(client)
        assert repeated.status_code == 202 and repeated.json['run_id'] == first.json['run_id']
        assert db_rows(root) == before
        changed = multipart()
        changed['name'] = 'different name'
        assert_error(post(client, changed), 409, 'request_conflict')
        assert_error(post(client, multipart('second')), 429, 'queue_full')
        assert db_rows(root) == before


def test_upload_filename_is_display_only_and_cannot_escape_storage(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        data = multipart()
        raw = data.get('behavior_tree')[0].getvalue()
        data.setlist('behavior_tree', [(io.BytesIO(raw), '../../escaped.xml')])
        response = post(client, data)
        assert response.status_code == 202
        row = RunStore(root).get_run(response.json['run_id'])
        assert not (tmp_path / 'escaped.xml').exists()
        assert not (tmp_path.parent / 'escaped.xml').exists()
        for source in row['build_input']['sources']:
            record = AssetStore(root).get_source(source['source_asset_id'])
            assert (root / record['path']).resolve().is_relative_to(root.resolve())
        assert_public(get(client, f"/api/domain-runs/{row['run_id']}").json, root)


@pytest.mark.parametrize('guard', ['origin', 'json_array', 'malformed_json', 'host'])
def test_existing_application_guards_apply_to_new_routes(tmp_path, guard):
    with http_app(tmp_path) as (_, client, _, root):
        before = db_rows(root)
        if guard == 'origin':
            response = post(client, headers={'Origin': 'https://untrusted.invalid'})
            expected = 403
        elif guard == 'host':
            response = client.post('/api/robotics/builds', data=multipart(), base_url='http://untrusted.invalid')
            expected = 400
        elif guard == 'json_array':
            response = client.post('/api/domain-runs/missing/retry', json=[], base_url=BASE_URL)
            expected = 400
        else:
            response = client.post('/api/domain-runs/missing/retry', data='{broken', content_type='application/json', base_url=BASE_URL)
            expected = 400
        assert response.status_code == expected
        assert db_rows(root) == before


def test_storage_error_cannot_be_returned_as_accepted_or_leak_paths(tmp_path, monkeypatch):
    with http_app(tmp_path) as (_, client, runtime, root):
        def broken(**kwargs):
            raise OSError(str(root / 'private/domain.sqlite3'))
        monkeypatch.setattr(runtime.service, 'submit', broken)
        before = db_rows(root)
        body = assert_error(post(client), 503, 'storage_unavailable')
        assert_public(body, root)
        assert db_rows(root) == before


def test_run_history_and_cursor_events_are_bounded_read_only_and_stable(tmp_path):
    with http_app(tmp_path) as (_, client, runtime, root):
        rows = [runtime.service.submit(**upload_args(f'r{i}')) for i in range(4)]
        rid = rows[0]['run_id']
        runs = RunStore(root)
        runs.start(rid)
        runs.append_event(rid, kind='stage', payload={'stage': 'computing'})
        before = db_rows(root)
        first = get(client, '/api/domain-runs?operation=build_ontology&limit=2&offset=0').json['items']
        second = get(client, '/api/domain-runs?operation=build_ontology&limit=2&offset=2').json['items']
        assert len(first) == len(second) == 2
        assert {r['run_id'] for r in first + second} == {r['run_id'] for r in rows}
        events = get(client, f'/api/domain-runs/{rid}/events?after=1').json['items']
        assert [e['seq'] for e in events] == [2, 3]
        assert get(client, f'/api/domain-runs/{rid}/events?after=3').json['items'] == []
        detail = get(client, f'/api/domain-runs/{rid}').json
        assert detail['status'] == 'running'
        assert_public(detail, root)
        assert db_rows(root) == before
        for query in ('limit=0', 'limit=101', 'offset=-1', 'limit=oops'):
            assert_error(get(client, '/api/domain-runs?' + query), 400, 'invalid_request')
        for cursor in ('-1', 'oops'):
            assert_error(get(client, f'/api/domain-runs/{rid}/events?after={cursor}'), 400, 'invalid_request')


def test_catalog_keeps_legacy_references_distinct_and_does_not_migrate_on_get(tmp_path):
    legacy = [{'key': 'legacy-one', 'name': '旧图谱'}, {'key': 'legacy-two', 'name': '另一个旧图谱'}]
    with http_app(tmp_path, legacy=legacy) as (_, client, runtime, root):
        assets = AssetStore(root)
        robot = seed_completed(runtime, root)
        registered = assets._register_legacy([('legacy-one', '旧图谱快照', data_bytes(), {'legacy_key': 'legacy-one'})])[0]
        before, files = db_rows(root), blob_bytes(root)
        response = get(client, '/api/ontology-assets')
        assert response.status_code == 200
        items = response.json['items']
        assert len(items) == 3, 'Registered legacy key is represented once; unregistered key stays read-only'
        old = next(i for i in items if i.get('legacy_key') == 'legacy-two')
        assert old['entry_kind'] == 'legacy_reference' and old['domain'] == 'data'
        assert not old.get('ontology_id') and not old.get('version_id')
        assert any(i.get('ontology_id') == registered['ontology_id'] and i['entry_kind'] == 'asset' for i in items)
        robot_item = next(i for i in items if i.get('ontology_id') == robot['output_asset_ref']['ontology_id'])
        assert robot_item['domain'] == 'robotics'
        assert db_rows(root) == before and blob_bytes(root) == files
        only_robot = get(client, '/api/ontology-assets?domain=robotics').json['items']
        assert [i['ontology_id'] for i in only_robot] == [robot_item['ontology_id']]
        assert_public(items, root)


def test_fixed_version_detail_and_download_match_actual_artifact_bytes(tmp_path):
    with http_app(tmp_path) as (_, client, runtime, root):
        row = seed_completed(runtime, root)
        ref, runs, assets = row['output_asset_ref'], RunStore(root), AssetStore(root)
        original = assets.get_version(**ref)
        original_bytes = assets.read_payload(**ref)
        newer = assets.add_version(ref['ontology_id'], original_bytes, payload_kind='robotics_rdf',
                                  parent_version_id=ref['version_id'], source_asset_ids=original['source_asset_ids'])
        url = f"/api/ontology-assets/{ref['ontology_id']}/versions/{ref['version_id']}"
        detail = get(client, url)
        assert detail.status_code == 200
        assert detail.json['version_id'] == ref['version_id'] != newer['version_id']
        assert detail.json['publication_state'] == 'candidate'
        assert detail.json['source_asset_ids'] == original['source_asset_ids']
        assert detail.json['build_run_id'] == row['run_id']
        assert_public(detail.json, root)
        run_detail = get(client, f"/api/domain-runs/{row['run_id']}").json
        assert {a['kind'] for a in run_detail['artifacts']} == {'ontology', 'validation', 'manifest'}
        assert_public(run_detail, root)
        for record in runs.artifacts(row['run_id']):
            endpoint = f"/api/domain-runs/{row['run_id']}/artifacts/{record['artifact_id']}"
            downloaded = get(client, endpoint)
            assert downloaded.status_code == 200
            assert downloaded.data == runs.read_artifact(row['run_id'], record['artifact_id'])
            assert sha(downloaded.data) == record['sha256']
            assert downloaded.mimetype == record['media_type']
            assert 'attachment' in downloaded.headers['Content-Disposition']
            assert_public(dict(downloaded.headers), root)
        assert assets.read_payload(**ref) == original_bytes
        unrelated, _ = add_data(assets)
        assert get(client, f"/api/ontology-assets/{unrelated['ontology_id']}/versions/{ref['version_id']}").status_code == 404


@pytest.mark.parametrize('damage', ['missing', 'modified', 'symlink'])
def test_damaged_registered_download_returns_integrity_error_without_rewriting_history(tmp_path, damage):
    with http_app(tmp_path) as (_, client, runtime, root):
        row = seed_completed(runtime, root)
        runs = RunStore(root)
        record = next(r for r in runs.artifacts(row['run_id']) if r['kind'] == 'ontology')
        path = root / record['path']
        if damage == 'missing':
            path.unlink()
        elif damage == 'modified':
            path.write_bytes(b'tampered ontology')
        else:
            outside = tmp_path / 'outside.owl'
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
        before = deepcopy(runs.get_run(row['run_id'])), deepcopy(runs.events(row['run_id']))
        response = get(client, f"/api/domain-runs/{row['run_id']}/artifacts/{record['artifact_id']}")
        assert_error(response, 409, 'artifact_integrity')
        assert_public(response.json, root)
        assert (runs.get_run(row['run_id']), runs.events(row['run_id'])) == before


def test_unknown_cross_run_and_path_downloads_are_not_resolved(tmp_path):
    with http_app(tmp_path) as (_, client, runtime, root):
        row = seed_completed(runtime, root)
        other = runtime.service.submit(**upload_args('other'))
        record = RunStore(root).artifacts(row['run_id'])[0]
        before = db_rows(root)
        endpoints = [f"/api/domain-runs/{other['run_id']}/artifacts/{record['artifact_id']}",
                     f"/api/domain-runs/{row['run_id']}/artifacts/missing",
                     '/api/domain-runs/missing', '/api/domain-runs/missing/events',
                     f"/api/domain-runs/{row['run_id']}/artifacts/..%2F..%2Fdomain.sqlite3"]
        for endpoint in endpoints:
            response = get(client, endpoint)
            assert response.status_code == 404
            assert str(root) not in response.get_data(as_text=True)
        assert db_rows(root) == before


def test_retry_endpoint_uses_fixed_sources_and_new_idempotency_key(tmp_path):
    with http_app(tmp_path) as (_, client, runtime, root):
        row = runtime.service.submit(**upload_args())
        runs = RunStore(root)
        runs.start(row['run_id'])
        old = runs.finish_build(row['run_id'], result=failure_result(), ontology=None)
        url = f"/api/domain-runs/{row['run_id']}/retry"
        response = client.post(url, json={'request_id': 'retry-1'}, base_url=BASE_URL)
        assert response.status_code == 202
        again = client.post(url, json={'request_id': 'retry-1'}, base_url=BASE_URL)
        assert again.status_code == 202 and again.json['run_id'] == response.json['run_id']
        saved = runs.get_run(response.json['run_id'])
        assert saved['retry_of'] == old['run_id'] and saved['build_input'] == old['build_input']
        assert runs.get_run(old['run_id']) == old
        assert_error(client.post(url, json={'request_id': 'retry-2', 'sources': {}}, base_url=BASE_URL), 400, 'invalid_request')


def test_default_runtime_connects_upload_to_real_compute_and_download(tmp_path):
    with http_app(tmp_path, start_executor=True) as (_, client, _, root):
        response = post(client)
        assert response.status_code == 202
        saved = terminal(root, response.json['run_id'])
        assert saved['status'] == 'completed' and saved['result']['status'] == 'built'
        detail = get(client, f"/api/domain-runs/{saved['run_id']}").json
        ontology = next(a for a in detail['artifacts'] if a['kind'] == 'ontology')
        downloaded = get(client, ontology['download_url'])
        assert downloaded.status_code == 200
        assert downloaded.data == AssetStore(root).read_payload(**saved['output_asset_ref'])


@pytest.mark.parametrize('key,value', [('DATAMIND_DOMAIN_STORAGE_ROOT', 'relative/path'),
    ('DATAMIND_ROBOTICS_MAX_PENDING', 0), ('DATAMIND_ROBOTICS_MAX_PENDING', 65),
    ('DATAMIND_ROBOTICS_TIMEOUT_SECONDS', 'nan'), ('DATAMIND_ROBOTICS_TIMEOUT_SECONDS', 'inf'),
    ('DATAMIND_ROBOTICS_TIMEOUT_SECONDS', 0), ('DATAMIND_ROBOTICS_TIMEOUT_SECONDS', 3601),
    ('DATAMIND_ROBOTICS_FILE_LIMIT_BYTES', -1), ('DATAMIND_ROBOTICS_ENABLED', 'not-a-bool')])
def test_invalid_enabled_configuration_fails_before_storage_writes(tmp_path, key, value):
    # Resolve module before raises: missing implementation is never an expected config rejection.
    import importlib
    importlib.import_module('bp_robotics')
    with pytest.raises(ValueError):
        with http_app(tmp_path, **{key: value}):
            pytest.fail('Invalid config was silently accepted')
    assert not (tmp_path / 'assets/domain.sqlite3').exists()


def test_default_storage_root_uses_workdir_and_two_apps_are_isolated(tmp_path):
    with http_app(tmp_path / 'one', DATAMIND_DOMAIN_STORAGE_ROOT=None) as (_, one, _, _):
        response = post(one)
        assert response.status_code == 202
        expected_root = tmp_path / 'one/work/domain_assets'
        assert RunStore(expected_root).get_run(response.json['run_id'])['status'] == 'queued'
        with http_app(tmp_path / 'two') as (_, two, _, root_two):
            second = post(two)
            assert second.status_code == 202 and second.json['run_id'] != response.json['run_id']
            assert get(two, f"/api/domain-runs/{response.json['run_id']}").status_code == 404
            assert len(RunStore(root_two).list_runs()) == 1


def test_real_server_mounts_routes_and_disabled_mode_keeps_legacy_endpoints(tmp_path):
    # A subprocess imports actual server.py so a test-only Blueprint wiring cannot pass.
    import importlib
    importlib.import_module('bp_robotics')
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith('DATAMIND_')}
    work = tmp_path / 'work'
    work.mkdir()
    (work / 'demo_ir.json').write_bytes(data_bytes())
    env.update(DATAMIND_WORKDIR=str(work), DATAMIND_DB=str(ROOT / 'examples/demo_metrics.db'),
               DATAMIND_ROBOTICS_ENABLED='0', DATAMIND_DOMAIN_STORAGE_ROOT=str(tmp_path / 'storage'),
               DATAMIND_ENGINE_DIR=str(tmp_path / 'no-engine'), DATAMIND_OUTPUTS_DIR=str(tmp_path / 'no-outputs'))
    code = '''import json
from server import app
with app.test_client() as client:
    caps=client.get('/api/robotics/capabilities', base_url='http://127.0.0.1')
    assert caps.status_code == 200 and caps.json['enabled'] is False
    for endpoint in ('/api/jobs','/api/graphs','/api/build/defaults'):
        response=client.get(endpoint, base_url='http://127.0.0.1')
        assert response.status_code == 200, endpoint
    assert isinstance(client.get('/api/jobs', base_url='http://127.0.0.1').json,list)
print(json.dumps({'passed':True}))
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout.splitlines()[-1])['passed']
    assert not (tmp_path / 'storage').exists()


def test_actual_server_enabled_build_and_new_process_history_use_the_same_storage(tmp_path):
    import importlib
    importlib.import_module('bp_robotics')
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith('DATAMIND_')}
    work, root = tmp_path / 'work', tmp_path / 'storage'
    work.mkdir()
    (work / 'demo_ir.json').write_bytes(data_bytes())
    env.update(DATAMIND_WORKDIR=str(work), DATAMIND_DB=str(ROOT / 'examples/demo_metrics.db'),
               DATAMIND_ROBOTICS_ENABLED='1', DATAMIND_DOMAIN_STORAGE_ROOT=str(root),
               DATAMIND_ROBOTICS_MAX_PENDING='2', DATAMIND_ROBOTICS_TIMEOUT_SECONDS='30',
               DATAMIND_ENGINE_DIR=str(tmp_path / 'no-engine'), DATAMIND_OUTPUTS_DIR=str(tmp_path / 'no-outputs'))
    code = '''import hashlib,json,time
from server import app
from tests.build_runtime_support import BASE_URL,multipart
try:
    with app.test_client() as c:
        caps=c.get('/api/robotics/capabilities',base_url=BASE_URL)
        assert caps.status_code==200 and caps.json['enabled'] is True
        assert caps.json['limits']['pending']==2
        response=c.post('/api/robotics/builds',data=multipart(),base_url=BASE_URL)
        assert response.status_code==202, response.get_data(as_text=True)
        rid=response.json['run_id']
        deadline=time.monotonic()+40
        while True:
            row=c.get('/api/domain-runs/'+rid,base_url=BASE_URL).json
            if row['status'] in ('completed','failed','interrupted'):break
            assert time.monotonic()<deadline
            time.sleep(.02)
        assert row['status']=='completed',row
        artifact=next(a for a in row['artifacts'] if a['kind']=='ontology')
        download=c.get(artifact['download_url'],base_url=BASE_URL)
        assert download.status_code==200 and hashlib.sha256(download.data).hexdigest()==artifact['sha256']
        print(json.dumps({'run_id':rid,'ref':row['output_asset_ref'],'sha256':artifact['sha256']}),flush=True)
finally:
    app.extensions['robotics'].close()
'''
    built = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=55)
    assert built.returncode == 0, built.stdout + built.stderr
    saved = json.loads(built.stdout.splitlines()[-1])
    before = db_rows(root)
    reopen = '''import hashlib,json,sys
from wsgi import application as app
try:
    with app.test_client() as c:
        row=c.get('/api/domain-runs/'+sys.argv[1],base_url='http://127.0.0.1').json
        assert row['status']=='completed'
        artifact=next(a for a in row['artifacts'] if a['kind']=='ontology')
        data=c.get(artifact['download_url'],base_url='http://127.0.0.1').data
        print(json.dumps({'run_id':row['run_id'],'ref':row['output_asset_ref'],'sha256':hashlib.sha256(data).hexdigest()}),flush=True)
finally:
    app.extensions['robotics'].close()
'''
    result = subprocess.run([sys.executable, '-c', reopen, saved['run_id']], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == saved
    assert db_rows(root) == before, 'Server/WSGI reopen must not rerun a terminal build'


def test_actual_invalid_xml_is_accepted_then_reported_as_located_failure(tmp_path):
    with http_app(tmp_path, start_executor=True) as (_, client, _, root):
        data = multipart()
        data.setlist('behavior_tree', [(io.BytesIO(b'<broken>'), 'broken.xml')])
        response = post(client, data)
        assert response.status_code == 202
        saved = terminal(root, response.json['run_id'])
        detail = get(client, f"/api/domain-runs/{saved['run_id']}").json
        assert detail['status'] == 'failed' and detail['output_asset_ref'] is None
        problem = next(i for i in detail['result']['issues'] if i['code'] == 'invalid_xml')
        source = next(s for s in saved['build_input']['sources'] if s['role'] == 'behavior_tree')
        assert problem['source_role'] == 'behavior_tree'
        assert problem['source_asset_id'] == source['source_asset_id']
        assert problem['location']['line'] == 1
        assert {a['kind'] for a in detail['artifacts']} == {'validation', 'manifest'}
        report = next(a for a in detail['artifacts'] if a['kind'] == 'validation')
        downloaded = get(client, report['download_url'])
        assert downloaded.status_code == 200
        assert downloaded.json['issues'] == detail['result']['issues']
        assert_public(detail, root)

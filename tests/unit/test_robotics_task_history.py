"""Application history retains distinct task runs and immutable downloads — DR-069."""
import json

from tests.build_pipeline_support import db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_task_support import fixture, model, start, message, TEXT
from domain_runs.robotics_task_service import TaskService
from domain_runs import RunStore


def get(client, path):
    return client.get('/api/robotics/tasks' + path, base_url=BASE_URL)


def test_history_keeps_multiple_runs_and_sessions_with_fixed_results(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root, ready=True)
        model_client, transport = model()
        service = TaskService(root, model_client, generation_policy='offline')
        first = message(service, start(service, version))
        assert first['status'] == 'generated'
        second = service.generate(first['session_id'], expected_revision=first['revision'], request_id='again')
        assert second['status'] == 'generated' and first['run_id'] != second['run_id']
        draft = start(service, version, request_id='second-task')
        before = db_rows(root)
        response = get(client, '?limit=2')
        assert response.status_code == 200
        listing = response.get_json()
        assert listing['total'] == 3 and len(listing['items']) == 2
        rest = get(client, '?limit=2&offset=2').get_json()['items']
        items = listing['items'] + rest
        assert {r['run_id'] for r in items if r['kind'] == 'generation'} == {first['run_id'], second['run_id']}
        assert any(r['session_id'] == draft['session_id'] and r['kind'] == 'task' for r in items)
        assert all(r['asset_name'] == first['asset_name'] and r['version_number'] == first['version_number'] for r in items)
        assert len(get(client, '?q=' + first['asset_name']).get_json()['items']) == 3
        assert get(client, '?q=不存在的任务').get_json()['items'] == []
        for saved in [first, second]:
            path = f"/{saved['session_id']}/generations/{saved['run_id']}"
            result = get(client, path)
            assert result.status_code == 200
            body = result.get_json()
            assert body['tree'] == saved['tree'] and body['version_id'] == saved['version_id']
            assert body['title'] == TEXT and body['read_only'] is True
            raw = get(client, path + '/candidate')
            assert raw.status_code == 200
            assert raw.data == RunStore(root, read_only=True).read_artifact(**saved['candidate'])
        assert get(client, f"/{draft['session_id']}/generations/{first['run_id']}").status_code == 404
        assert db_rows(root) == before
        # No list/read operation repeats extraction; one original parse only.
        assert len(transport.calls) == 1


def test_history_empty_validation_and_legacy_records_are_read_only(tmp_path):
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        assert get(client, '').get_json() == {'items': [], 'total': 0, 'limit': 30, 'offset': 0}
        assert not (root / 'domain.sqlite3').exists()
    with http_app(tmp_path / 'existing') as (_, client, _, root):
        _, version, build = fixture(root)
        service = TaskService(root, model()[0])
        state = start(service, version)
        with service.storage.transaction(write=True) as conn:
            record = json.loads(conn.execute('SELECT record FROM robotics_task_sessions').fetchone()[0])
            record.pop('created_at', None); record.pop('updated_at', None)
            conn.execute('UPDATE robotics_task_sessions SET record=?', (json.dumps(record),))
        before = db_rows(root)
        item = get(client, '').get_json()['items'][0]
        assert item['updated_at'] is None and item['status'] == 'idle'
        for query in ['?limit=0', '?limit=101', '?offset=-1', '?limit=a', '?q=a&q=b', '?extra=x']:
            assert get(client, query).status_code == 400
        assert get(client, f"/{state['session_id']}/generations/{build['run_id']}").status_code == 404
        assert db_rows(root) == before


def test_history_rejects_corrupted_candidate(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root, ready=True)
        service = TaskService(root, model()[0], generation_policy='offline')
        saved = message(service, start(service, version))
        runs = RunStore(root)
        artifact = next(a for a in runs.artifacts(saved['run_id']) if a['kind'] == 'candidate')
        (root / artifact['path']).write_bytes(b'broken')
        path = f"/{saved['session_id']}/generations/{saved['run_id']}"
        for suffix in ['', '/candidate']:
            response = get(client, path + suffix)
            assert response.status_code == 409
            assert response.get_json()['error']['code'] == 'artifact_integrity'

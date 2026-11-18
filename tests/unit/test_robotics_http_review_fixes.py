"""PR1 评审问题回归：演示重放、上传去重与配额、三元组上限、评审署名、并发与引擎标签。"""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import sqlite3
import threading

import pytest

from domains.robotics import consumer, evidence
from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService
from tests.build_pipeline_support import blob_bytes, db_rows, sha
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_governance_support import NOW, publish_args, save_args
from tests.robotics_review_http_support import attachment, candidate, endpoint, error, ok, post, seed, version_endpoint
from tests.robotics_task_support import TEXT, fixture, model_server
from tests.storage_pipeline_support import fresh_payload

DEMO = '/api/robotics/task-demo'
IMPORT = '/api/robotics/imports'


def counts(root, *tables):
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        return {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in tables}


def upload(client, oid, content):
    return client.post(endpoint(oid, 'evidence-sources'), data=attachment(content), base_url=BASE_URL)


def import_owl(client, *, name='导入合成 OWL', request_id='import', content=None):
    return client.post(IMPORT, data={'name': name, 'mode': 'real', 'request_id': request_id,
        'file': (io.BytesIO(fresh_payload('real') if content is None else content), 'ontology.owl')}, base_url=BASE_URL)


def review_body(version, *, actor='独立评审人', request_id='review-x', decision='accepted'):
    return {'payload_sha256': version['payload_sha256'], 'decision': decision, 'actor': actor,
            'reason': '结构评审', 'request_id': request_id}


# 问题 1：演示入口先查重放、复用已打包演示、限制并发

def test_task_demo_replay_and_new_request_reuse_packaged_demo_without_rebuilding(tmp_path, monkeypatch):
    import domain_runs.robotics_task_demo as demo
    calls, real = [], demo.build_example
    monkeypatch.setattr(demo, 'build_example', lambda *a, **k: calls.append(1) or real(*a, **k))
    with http_app(tmp_path) as (_, client, _, root):
        first = ok(post(client, DEMO, {'request_id': 'demo-1'}), 201)
        tables = counts(root, 'versions', 'sources', 'runs')
        files = set(blob_bytes(root))
        assert ok(post(client, DEMO, {'request_id': 'demo-1'}), 201) == first
        assert len(calls) == 1 and counts(root, 'versions', 'sources', 'runs') == tables
        second = ok(post(client, DEMO, {'request_id': 'demo-2'}), 201)
        assert second['version_id'] == first['version_id'] and second['session_id'] != first['session_id']
        assert len(calls) == 1 and counts(root, 'versions', 'sources', 'runs') == tables
        assert set(blob_bytes(root)) == files


def test_task_demo_rejects_concurrent_builds(tmp_path, monkeypatch):
    import domain_runs.robotics_task_demo as demo
    entered, release, real = threading.Event(), threading.Event(), demo.build_example

    def slow(*args, **kwargs):
        entered.set()
        release.wait(timeout=30)
        return real(*args, **kwargs)
    monkeypatch.setattr(demo, 'build_example', slow)
    with http_app(tmp_path) as (_, client, _, root):
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(lambda: post(client, DEMO, {'request_id': 'demo-a'}))
            assert entered.wait(timeout=30)
            try:
                error(post(client, DEMO, {'request_id': 'demo-b'}), 409, 'demo_busy')
                error(post(client, DEMO, {'request_id': 'demo-a'}), 409, 'task_busy')
            finally:
                release.set()
            ok(pending.result(timeout=120), 201)
        ok(post(client, DEMO, {'request_id': 'demo-b'}), 201)


# 问题 2：证据附件去重与单资产配额；OWL 导入去重

def test_evidence_upload_dedups_same_bytes_and_enforces_asset_quota(tmp_path):
    with http_app(tmp_path, DATAMIND_ROBOTICS_ASSET_SOURCE_LIMIT_BYTES=40) as (_, client, _, root):
        _, oid, _ = seed(root)
        first = ok(upload(client, oid, b'a' * 30), 201)
        rows, files = counts(root, 'sources'), blob_bytes(root)
        again = ok(upload(client, oid, b'a' * 30), 201)
        assert again['source_asset_id'] == first['source_asset_id'] and again['sha256'] == sha(b'a' * 30)
        assert counts(root, 'sources') == rows and blob_bytes(root) == files
        before = db_rows(root)
        error(upload(client, oid, b'b' * 30), 413, 'quota_exceeded')
        assert db_rows(root) == before and blob_bytes(root) == files
        assert GovernanceService(root).history(oid) == []


def test_owl_import_with_new_request_id_reuses_same_content(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        first = ok(import_owl(client, request_id='import-1'), 201)
        tables, files = counts(root, 'assets', 'versions', 'sources'), blob_bytes(root)
        assert ok(import_owl(client, request_id='import-2'), 201) == first
        assert counts(root, 'assets', 'versions', 'sources') == tables and blob_bytes(root) == files
        other = ok(import_owl(client, name='另一个名称', request_id='import-3'), 201)
        assert other['ontology_id'] != first['ontology_id']
        assert counts(root, 'sources') == {'sources': tables['sources']}
        source_ids = {s for oid, vid in [(first['ontology_id'], first['version_id']),
                      (other['ontology_id'], other['version_id'])]
                      for s in AssetStore(root).get_version(oid, vid)['source_asset_ids']}
        assert len(source_ids) == 1


# 问题 3：三元组上限在推理校验前拒绝

def test_owl_import_rejects_too_many_triples_before_validation(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(consumer, 'load_ontology_bytes', lambda *a, **k: called.append(1))
    with http_app(tmp_path, DATAMIND_ROBOTICS_MAX_TRIPLES=10) as (_, client, _, root):
        response = import_owl(client)
        assert response.status_code == 413 and response.json['error']['code'] == 'ontology_too_large'
        assert called == []


def test_review_rejects_too_many_triples_before_shacl(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(consumer, 'validate_graph', lambda *a, **k: called.append(1))
    with http_app(tmp_path, DATAMIND_ROBOTICS_MAX_TRIPLES=10) as (_, client, _, root):
        _, oid, base = seed(root)
        error(post(client, version_endpoint(oid, base, 'reviews'), review_body(base)), 413, 'ontology_too_large')
        assert called == []


# 问题 4：评审署名不得为系统/模型身份，不得自审，记录为客户端声明

@pytest.mark.parametrize('actor', ['system', ' SYSTEM ', 'model', 'llm', 'assistant'])
def test_review_rejects_system_or_model_identity(tmp_path, actor):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = candidate(root, oid, base)
        before = db_rows(root)
        error(post(client, version_endpoint(oid, saved, 'reviews'), review_body(saved, actor=actor)), 422, 'reviewer_invalid')
        assert db_rows(root) == before


def test_review_refuses_self_review_by_version_author(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = candidate(root, oid, base)
        author = save_args(base)['actor']
        before = db_rows(root)
        error(post(client, version_endpoint(oid, saved, 'reviews'), review_body(saved, actor=' ' + author)), 409, 'self_review')
        assert db_rows(root) == before


def test_review_records_reviewer_as_client_declared_and_publish_rejects_system(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = candidate(root, oid, base)
        record = ok(post(client, version_endpoint(oid, saved, 'reviews'), review_body(saved, actor=' 独立评审人 ')), 201)
        assert record['reviewer'] == '独立评审人' and record['actor'] == '独立评审人'
        assert record['actor_source'] == 'client_declared' and record['authenticated_actor'] is False
        args = {**publish_args(saved, record), 'actor': 'system'}
        args.pop('version_id')
        error(post(client, version_endpoint(oid, saved, 'publish'), args), 422, 'reviewer_invalid')


def test_publish_rejects_a_stored_self_review(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = candidate(root, oid, base)
        service = GovernanceService(root)
        # 模拟修复前已登记的自审记录：直接写入登记表，绕过评审入口。
        record = {'review_id': 'review_legacy', 'ontology_id': oid, 'version_id': saved['version_id'],
                  'payload_sha256': saved['payload_sha256'], 'decision': 'accepted',
                  'actor': save_args(base)['actor'], 'reason': 'legacy', 'created_at': NOW,
                  'report_sha256': '0' * 64, 'report': {}}
        with service.storage.transaction(write=True) as conn:
            conn.execute('INSERT INTO governance_reviews VALUES (?,?,?,?)',
                         (record['review_id'], oid, saved['version_id'], json.dumps(record)))
        args = publish_args(saved, record)
        args.pop('version_id')
        error(post(client, version_endpoint(oid, saved, 'publish'), args), 409, 'self_review')


# 问题 6：同一任务会话只允许一条进行中的消息

def test_second_message_on_running_session_is_rejected_without_model_call(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    entered, release = threading.Event(), threading.Event()
    with model_server({'entered': entered, 'release': release}) as (url, calls):
        monkeypatch.setenv('DATAMIND_LLM_BASE', url)
        monkeypatch.setenv('DATAMIND_LLM_MODEL', 'scripted-test-model')
        monkeypatch.setenv('DATAMIND_LLM_KEY', 'synthetic-secret')
        with http_app(tmp_path) as (_, client, _, root):
            _, version, _ = fixture(root, ready=True)
            session = ok(post(client, '/api/robotics/tasks', {'ontology_id': version['ontology_id'],
                'version_id': version['version_id'], 'mode': 'real', 'request_id': 'start'}), 201)
            path = '/api/robotics/tasks/' + session['session_id'] + '/messages'
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(lambda: post(client, path, {'text': TEXT, 'expected_revision': 0, 'request_id': 'm1'}))
                assert entered.wait(timeout=30)
                try:
                    for revision in (0, 1):
                        response = post(client, path, {'text': TEXT, 'expected_revision': revision, 'request_id': f'm2-{revision}'})
                        assert response.status_code == 409, response.text
                    assert len(calls) == 1
                finally:
                    release.set()
                pending.result(timeout=60)


# 问题 7：每个引擎任务都有中文标签

def test_every_engine_task_has_a_chinese_label():
    import srv_engine
    assert set(srv_engine.ENGINE_TASKS) <= set(srv_engine.ENGINE_TASK_CN)
    assert srv_engine.ENGINE_TASK_CN['bt_extract'] == '行为树任务抽取'


# 问题 5：逻辑删除的资产默认不可读版本详情，显式 state=deleted 才进入历史视图

def _deleted_data_asset(root):
    from ontology_assets.lifecycle import LifecycleService
    from tests.asset_support import add_data
    from tests.unit.test_asset_lifecycle import apply
    asset, version = add_data(AssetStore(root))
    apply(LifecycleService(root), asset['ontology_id'])
    return '/api/ontology-assets/' + asset['ontology_id'] + '/versions/' + version['version_id']


def test_deleted_asset_version_detail_requires_explicit_history_view(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        path = _deleted_data_asset(root)
        error(client.get(path, base_url=BASE_URL), 409, 'ontology_deleted')
        error(client.get(path + '?state=bogus', base_url=BASE_URL), 400, 'invalid_request')
        body = ok(client.get(path + '?state=deleted', base_url=BASE_URL))
        assert body['lifecycle']['state'] == 'deleted'


def test_history_view_download_links_carry_deleted_state_only_for_deleted_assets(tmp_path):
    from tests.asset_support import add_data
    with http_app(tmp_path) as (_, client, _, root):
        path = _deleted_data_asset(root)
        body = ok(client.get(path + '?state=deleted', base_url=BASE_URL))
        assert all('state=deleted' in a['download_url'] for a in body['artifacts']) and body['artifacts']
        asset, version = add_data(AssetStore(root), name='有效数据本体')
        active = ok(client.get('/api/ontology-assets/' + asset['ontology_id'] + '/versions/' + version['version_id'],
                               base_url=BASE_URL))
        assert active['artifacts'] and all('state=' not in a['download_url'] for a in active['artifacts'])

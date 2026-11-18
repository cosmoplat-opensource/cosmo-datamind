"""PR1 评审问题回归:符号链接工作目录、锁顺序、问数闸门、迁移与存储 — DR-156 / DR-157 / DR-158。"""
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
import server

import asset_lifecycle
from ontology_assets.lifecycle import LifecycleService
from srv_context import _RDF_LOCK
from tests.unit.test_asset_lifecycle_http import activate, request
from tests.unit.test_deepqa_application_http import ask, qa_http, report
from tests.unit.test_merge_lifecycle_regressions import alias

__all__ = ['qa_http']


def finishes(fn, timeout=5):
    """在独立线程里执行;超时返回 (False, …) 而不是挂住整个测试进程。"""
    out = {}

    def run():
        try:
            out['value'] = fn()
        except BaseException as exc:  # noqa: BLE001
            out['error'] = exc
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout)
    return not worker.is_alive(), out


def hold(lock):
    """另一线程持有 lock,直到调用返回的 release()。"""
    ready, done = threading.Event(), threading.Event()

    def run():
        with lock:
            ready.set()
            done.wait(10)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert ready.wait(5)

    def release():
        done.set()
        worker.join(5)
    return release


def free_elsewhere(lock):
    """当前线程是否未持有 lock(由另一线程限时抢锁判断)。"""
    got = []

    def run():
        if lock.acquire(timeout=0.5):
            got.append(True)
            lock.release()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(5)
    return got == [True]


def linked(qa_http, monkeypatch):
    _, work, *_ = qa_http
    link = work.parent / 'linked-work'
    link.symlink_to(work, target_is_directory=True)
    monkeypatch.setattr(server, 'WORK', str(link))
    monkeypatch.setattr(server, 'FORGED_DIR', str(link / 'forged'))
    monkeypatch.setattr(server, 'IR_SOURCES', {'demo': {'name': '示例', 'paths': [str(link / 'demo.json')]}})
    return link


# ── 1. 工作目录含符号链接时,编辑与重写仍发布新固定版本 ──
def test_symlinked_workdir_edit_and_rewrite_publish_new_versions(qa_http, monkeypatch):
    linked(qa_http, monkeypatch)
    activate(qa_http)
    client = qa_http[0]
    alias(client, '链接别称')
    assert server.load_ir_edited('built_scope')['objects'][0]['aliases'] == ['链接别称']
    ir = json.loads((qa_http[1] / 'forged' / 'onto_test.json').read_text())
    ir['objects'][0]['cn'] = '新名称'
    server._atomic_json(server._forged_path('onto_test'), ir)
    assert server.load_ir_edited('forged_onto_test')['objects'][0]['cn'] == '新名称'


# ── 2/3. 生命周期查询不取 RDF 锁;检查服务不在 RDF 锁内回调解析器 ──
def test_lifecycle_lookups_complete_while_rdf_lock_is_held_elsewhere(qa_http):
    activate(qa_http)
    release = hold(_RDF_LOCK)
    try:
        done, out = finishes(lambda: (asset_lifecycle.deleted(server._QA_HOST, 'built_scope'),
                                      server.load_ir_edited('built_scope')['objects'][0]['id'],
                                      qa_http[0].get('/api/graphs').status_code))
    finally:
        release()
    assert done, '生命周期查询在等待 RDF 锁'
    assert out.get('value') == (False, 'customer', 200), out


def test_write_lock_then_lifecycle_does_not_deadlock_with_rdf_then_write(qa_http):
    activate(qa_http)
    barrier = threading.Barrier(2, timeout=5)

    def write_first():
        with server._WRITE_LOCK:
            barrier.wait()
            return asset_lifecycle.legacy_state(server._QA_HOST, 'built_scope')['state']

    def rdf_first():
        with _RDF_LOCK:
            barrier.wait()
            # 限时抢锁:回归时以失败结束而不是让整个测试进程死锁。
            if not server._WRITE_LOCK.acquire(timeout=3):
                return 'deadlock'
            server._WRITE_LOCK.release()
            return 'ok'
    threads = []
    outs = [{}, {}]
    for index, fn in enumerate((write_first, rdf_first)):
        def run(fn=fn, out=outs[index]):
            try:
                out['value'] = fn()
            except BaseException as exc:  # noqa: BLE001
                out['error'] = exc
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(8)
    assert not any(t.is_alive() for t in threads), 'ABBA 死锁'
    assert [o.get('value') for o in outs] == ['active', 'ok'], outs


class LockProbe:
    """记录每次回调解析器时当前线程是否持有 RDF 锁。"""
    def __init__(self, inner):
        self.inner, self.free = inner, []

    def resolve(self, **kwargs):
        self.free.append(free_elsewhere(_RDF_LOCK))
        return self.inner.resolve(**kwargs)

    def peek(self, report):
        self.free.append(free_elsewhere(_RDF_LOCK))
        return self.inner.peek(report)

    def resource_revision(self, target):
        # 只读存储、不取宿主锁,可在事务内调用。
        return self.inner.resource_revision(target)


def test_check_service_calls_resolver_outside_rdf_lock(tmp_path):
    from tests.application_contract_integration_support import harness, repair_args, request_body
    service, resolver, *_ = harness(tmp_path, repair=True)
    probe = LockProbe(resolver)
    service.resolver = probe
    created = service.evaluate(**request_body(_ref(tmp_path)))
    assert created['report']['check_id']
    response = service.repair(**repair_args(created['report']))
    assert response['replayed'] is False
    assert probe.free and all(probe.free), probe.free


def _ref(root):
    from ontology_assets import AssetStore
    assets = AssetStore(root, read_only=True)
    asset = assets.list_assets()[0]
    return {'ontology_id': asset['ontology_id'], 'version_id': assets.list_versions(asset['ontology_id'])[-1]['version_id']}


# ── 4. 查询执行期间不持全局写锁,也不在每次检查时清空共享缓存 ──
def test_sql_execution_releases_global_write_lock(qa_http, monkeypatch):
    real, seen = server.q, []

    def query(sql, **kwargs):
        seen.append(free_elsewhere(server._WRITE_LOCK))
        return real(sql, **kwargs)
    monkeypatch.setattr(server, 'q', query)
    answer, _, _ = ask(qa_http, '/api/chat')
    assert answer['results'] and seen and all(seen), seen


def test_unchanged_basis_keeps_shared_schema_caches(qa_http):
    assert ask(qa_http, '/api/chat')[0]['results']
    server._COLS_CACHE['probe'] = ['x']
    server._INV_CACHE['probe'] = ('sig', (set(), set()))
    assert ask(qa_http, '/api/chat')[0]['results']
    assert 'probe' in server._COLS_CACHE and 'probe' in server._INV_CACHE


@pytest.mark.parametrize('route', ['/api/chat', '/api/chat/stream'])
def test_edit_during_execution_discards_result_as_stale(qa_http, route, monkeypatch):
    _, work, _, ir, _ = qa_http
    real = server.q

    def query(sql, **kwargs):
        value = real(sql, **kwargs)
        ir['objects'][0]['table'] = 'outsiders'
        (work / 'built_scope.json').write_text(json.dumps(ir))
        return value
    monkeypatch.setattr(server, 'q', query)
    answer, _, _ = ask(qa_http, route)
    assert answer['code'] == 'stale_result' and not server._QA_CACHE


# ── 5. 业务数据写入不使进行中的问数失效 ──
@pytest.mark.parametrize('route', ['/api/chat', '/api/chat/stream'])
def test_business_row_write_during_planning_is_not_stale(qa_http, route, monkeypatch):
    db = qa_http[2]

    def plan(*args, **kwargs):
        with sqlite3.connect(db) as conn:
            conn.execute('INSERT INTO customers VALUES(4,40)')
        return {'analyses': [{'sql': 'SELECT COUNT(*) AS 记录数 FROM customers'}]}
    monkeypatch.setattr(server, 'agent_sql_plan', plan)
    answer, _, _ = ask(qa_http, route)
    assert answer.get('code') != 'stale_result', answer
    assert answer['results'][0]['data']['rows'] == [{'记录数': 4}]


# ── 6. 坏图不阻断迁移;协议只在 ready 后生效 ──
def test_bad_legacy_graph_is_skipped_and_migration_finishes(qa_http):
    client, work, *_ = qa_http
    (work / 'built_bad.json').write_text(json.dumps({'scenario': {'name': '坏图'}}))   # 缺 objects 列表
    assert 'built_bad' in [g['id'] for g in client.get('/api/graphs').json]
    server.init_asset_lifecycle()
    with sqlite3.connect(work / 'domain_assets' / 'domain.sqlite3') as conn:
        names = {r[0] for r in conn.execute('SELECT name FROM asset_lifecycle_settings')}
    assert 'ready' in names and 'migrating' not in names
    service = LifecycleService(work / 'domain_assets')
    assert service.resolve_legacy('built_scope')['lifecycle']['state'] == 'active'
    response = client.post('/api/build/delete', json={'key': 'built_bad'})
    assert response.status_code == 200, response.json
    assert not (work / 'built_bad.json').exists()


def test_interrupted_migration_does_not_enable_versioned_delete(qa_http):
    client, work, *_ = qa_http
    service = LifecycleService(work / 'domain_assets')
    with service._atomic(write=True) as conn:
        conn.execute("INSERT OR REPLACE INTO asset_lifecycle_settings VALUES ('migrating','1')")
    assert not asset_lifecycle.enabled(work / 'domain_assets')
    assert client.post('/api/build/delete', json={'key': 'built_second'}).status_code == 200


# ── 7. 绕过 _atomic_json 的写入在启动时按来源摘要重新绑定 ──
def test_startup_rebinds_graph_changed_outside_lifecycle_writer(qa_http):
    _, work, _, ir, _ = qa_http
    activate(qa_http)
    ir['objects'][0]['cn'] = '外部改写'
    (work / 'built_scope.json').write_text(json.dumps(ir))
    assert server.load_ir_edited('built_scope')['objects'][0]['cn'] == '客户'
    server.init_asset_lifecycle()
    assert server.load_ir_edited('built_scope')['objects'][0]['cn'] == '外部改写'


# ── 8. 基线 IR 按内容寻址只存一份,旧格式仍可读 ──
def _versions(work):
    with sqlite3.connect(work / 'domain_assets' / 'domain.sqlite3') as conn:
        return [json.loads(r[0]) for r in conn.execute(
            "SELECT v.metadata FROM versions v JOIN legacy_assets l USING(ontology_id) "
            "WHERE l.legacy_key='built_scope' ORDER BY v.rowid")]


def test_edit_versions_reference_one_content_addressed_baseline(qa_http):
    activate(qa_http)
    client, work, *_ = qa_http
    bases = work / 'domain_assets' / 'legacy_bases'
    before = sorted(bases.glob('*.json'))
    for name in ('一', '二', '三'):
        alias(client, name)
    saved = [m['provenance'] for m in _versions(work)]
    assert len(saved) >= 4 and all('base_ir' not in p for p in saved)
    assert len({p['base_ir_sha256'] for p in saved}) == 1
    assert sorted(bases.glob('*.json')) == before      # 编辑不新增基线副本
    assert client.post('/api/ont/undo', json={'graph': 'built_scope'}).status_code == 200
    assert server.load_ir_edited('built_scope')['objects'][0]['aliases'] == ['二']


def test_inline_baseline_from_older_versions_still_loads(qa_http):
    activate(qa_http)
    client, work, _, ir, _ = qa_http
    alias(client, '旧格式')
    with sqlite3.connect(work / 'domain_assets' / 'domain.sqlite3') as conn:
        vid = conn.execute("SELECT version_id FROM legacy_assets WHERE legacy_key='built_scope'").fetchone()[0]
        metadata = json.loads(conn.execute('SELECT metadata FROM versions WHERE version_id=?', (vid,)).fetchone()[0])
        metadata['provenance'].pop('base_ir_sha256', None)
        metadata['provenance']['base_ir'] = {**ir, 'scenario': {'name': '内联基线'}}
        conn.execute('UPDATE versions SET metadata=? WHERE version_id=?', (json.dumps(metadata), vid))
    assert asset_lifecycle.base(server._QA_HOST, 'built_scope')['scenario'] == {'name': '内联基线'}


# ── 9. 已删除本体的构建历程与数据载荷默认不可读 ──
def test_deleted_graph_history_and_payload_need_explicit_deleted_view(qa_http):
    service, oid = activate(qa_http)
    client = qa_http[0]
    vid = service.resolve_legacy('built_scope')['version_id']
    payload = f'/api/ontology-assets/{oid}/versions/{vid}/data-payload'
    assert client.get('/api/build/history/built_scope').status_code == 200
    assert client.get(payload).status_code == 200
    assert request(client, oid).status_code == 200
    for url in ('/api/build/history/built_scope', payload):
        response = client.get(url)
        assert response.status_code == 409 and response.json['error']['code'] == 'ontology_deleted', url
        assert client.get(url + '?state=deleted').status_code == 200, url


# ── 10. 生命周期开关与机器人开关同一配置来源 ──
@pytest.mark.parametrize('config,env,expected', [
    ({'DATAMIND_ROBOTICS_ENABLED': False}, {}, False),
    ({'DATAMIND_ROBOTICS_ENABLED': False, 'DATAMIND_LIFECYCLE_ENABLED': '1'}, {}, True),
    ({}, {'DATAMIND_LIFECYCLE_ENABLED': '0'}, False),
    ({'DATAMIND_LIFECYCLE_ENABLED': True}, {'DATAMIND_LIFECYCLE_ENABLED': '0'}, True),
    ({}, {'DATAMIND_ROBOTICS_ENABLED': 'off'}, False),
    ({}, {}, True),
])
def test_lifecycle_default_uses_app_config_like_robotics(monkeypatch, config, env, expected):
    for key in ('DATAMIND_ROBOTICS_ENABLED', 'DATAMIND_LIFECYCLE_ENABLED'):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delitem(server.app.config, key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    for key, value in config.items():
        monkeypatch.setitem(server.app.config, key, value)
    assert server._lifecycle_enabled() is expected


# ── 11. 子进程发出结果后退出,不能误判为崩溃 ──
def test_result_sent_just_before_child_exit_is_not_dropped(tmp_path):
    from domain_runs.executor import BuildExecutor

    class Receiver:
        def __init__(self):
            self.polls = []

        def poll(self, timeout=0):
            self.polls.append(timeout)
            return len(self.polls) > 1

        def recv(self):
            return {'result': {'status': 'ok'}, 'ontology': None}

        def close(self):
            pass

    class Sender:
        def close(self):
            pass

    class Child:
        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

        def close(self):
            pass

    receiver = Receiver()

    class Context:
        def Pipe(self, duplex=False):
            return receiver, Sender()

        def Process(self, **kwargs):
            return Child()
    executor = BuildExecutor(tmp_path / 'root', poll_interval=0.01)
    executor._context = Context()
    assert executor._compute('run_x') == {'result': {'status': 'ok'}, 'ontology': None}


# ── 12. 被拦截的 SQL 不覆盖已作答 SQL 的检查报告 ──
@pytest.mark.parametrize('route', ['/api/chat', '/api/chat/stream'])
def test_blocked_later_sql_does_not_replace_answer_report(qa_http, route, monkeypatch):
    monkeypatch.setattr(server, '_match_qa_skill', lambda q: {'analyses': [
        {'sql': 'SELECT COUNT(*) AS 记录数 FROM customers'}, {'sql': 'SELECT * FROM outsiders'}]})
    answer, _, _ = ask(qa_http, route)
    assert len(answer['results']) == 1
    value = report(answer)
    assert value['execution_status'] == 'ready'
    assert not any(i['code'] == 'data.sql_rejected' for i in value['issues'])


# ── 13. 设计运行在阶段之间检查总时限 ──
def test_design_run_past_deadline_fails_before_commit(tmp_path, monkeypatch):
    from domain_runs import robotics_design_service as module
    from tests.robotics_design_support import Harness, proposals, reply
    h = Harness(tmp_path / 'store')
    h.runtime.responses.append(reply(proposals()[0]))
    state = h.submit(description='整理技能及调用参数')
    clock = iter(range(0, 100000, 1000))
    # 只替换该模块看到的时钟,不影响其它线程的 time.monotonic。
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: float(next(clock))), raising=False)
    h.service.run_timeout_seconds = 10
    h.run()
    current = h.get(state)
    assert current['status'] == 'failed' and current['error']['code'] == 'design_timeout'
    assert current['revision'] == state['revision']


# ── 14. 空白图谱项沿用旧行为被忽略;非法键仍 422 ──
@pytest.mark.parametrize('route', ['/api/chat', '/api/chat/stream'])
def test_blank_graph_entries_are_ignored(qa_http, route):
    answer, _, _ = ask(qa_http, route, graphs=['built_scope', '', '  ', None])
    assert answer['results'][0]['data']['rows'] == [{'记录数': 3}]


@pytest.mark.parametrize('graphs', [['../etc'], 'built_scope', [7]])
def test_invalid_graph_scope_still_rejected(qa_http, graphs):
    response = qa_http[0].post('/api/chat', json={'q': '客户有多少条记录？', 'graphs': graphs})
    assert response.status_code == 422 and response.json['code'] == 'invalid_scope'

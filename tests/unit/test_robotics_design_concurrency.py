"""Real SQLite contention and stale worker publication — DR-070 / T06 RED."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier, Event

import pytest

from domain_runs import RunStore
from ontology_assets import AssetStore
from srv_context import _RDF_LOCK
from tests.build_pipeline_support import db_rows
from tests.robotics_design_support import Harness, expect_error, no_network, proposals, reply, service_api

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_concurrent_identical_submissions_create_one_session_and_source_pair(tmp_path):
    h = Harness(tmp_path / 'store')
    barrier = Barrier(4)

    def submit(_):
        barrier.wait(timeout=5)
        return h.submit(description='要求')

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert all(row == results[0] for row in results)
    assert len(db_rows(h.root)['sources']) == 2
    assert len(RunStore(h.root).list_runs()) == 1 and h.runtime.calls == []


def test_two_writers_on_same_revision_have_one_winner(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    barrier = Barrier(2)

    def submit(n):
        barrier.wait(timeout=5)
        try:
            return h.service.message(state['session_id'], text='要求', expected_revision=state['revision'],
                expected_stage='classes', request_id=f'message-{n}')
        except ValueError as exc:
            return {'error': getattr(exc, 'code', None)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert len([r for r in results if r.get('status') == 'queued']) == 1
    assert [r['error'] for r in results if 'error' in r] == ['session_busy']
    assert len(RunStore(h.root).list_runs()) == 2 and len(h.runtime.calls) == 1


def test_confirmation_and_message_race_cannot_both_mutate_same_revision(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    barrier = Barrier(2)

    def submit(confirm):
        barrier.wait(timeout=5)
        try:
            if confirm:
                return h.service.confirm_classes(state['session_id'], request_id='confirm',
                    expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
            return h.message(state)
        except ValueError as exc:
            return {'error': getattr(exc, 'code', None)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (False, True)))
    assert len([r for r in results if 'error' not in r]) == 1
    assert len([r for r in results if r.get('error') in ('session_busy', 'revision_conflict')]) == 1
    current = h.get(state)
    assert (current['stage'], current['revision'], current['status']) in (
        ('classes', 1, 'queued'), ('properties', 2, 'awaiting_input'))
    assert len(h.runtime.calls) == 1


def test_atomic_worker_claim_never_delivers_same_run_twice(tmp_path):
    h = Harness(tmp_path / 'store')
    queued = [h.submit(description='要求', request_id=str(i)) for i in range(3)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: h.service.claim_next(), range(4)))
    assert sum(row is None for row in claims) == 1
    ids = [r['run_id'] for r in claims if r is not None]
    assert len(set(ids)) == 3 and set(ids) == {r['run_id'] for r in queued}
    assert all(r['status'] == 'running' for r in claims if r)
    assert h.runtime.calls == []


def test_queue_capacity_is_atomic_and_waiting_confirmation_uses_no_slot(tmp_path):
    h = Harness(tmp_path / 'store', max_pending=1)
    first = h.submit(description='要求')
    before = db_rows(h.root)
    expect_error('queue_full', lambda: h.submit(description='要求', request_id='second'), 429)
    assert db_rows(h.root) == before
    waiting = h.submit(request_id='needs-description')
    assert waiting['run_id'] is None
    h.runtime.responses.append(reply(proposals()[0]))
    h.run()
    assert h.get(first)['status'] == 'awaiting_confirmation'
    assert h.submit(description='要求', request_id='second')['status'] == 'queued'


def test_model_wait_holds_neither_sqlite_write_transaction_nor_rdf_lock(tmp_path, monkeypatch):
    h = Harness(tmp_path / 'store')
    state = h.submit(description='要求')
    checked = []

    def other_thread():
        with sqlite3.connect(h.root / 'domain.sqlite3', timeout=0.3) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        assert _RDF_LOCK.acquire(timeout=0.3), 'Model wait must release the global RDF lock'
        _RDF_LOCK.release()
        AssetStore(h.root).create_asset(name='independent-write', domain='data')
        return True

    def request(*args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            checked.append(pool.submit(other_thread).result(timeout=3))
        return reply(proposals()[0])

    monkeypatch.setattr(h.runtime, 'run_turn', request)
    assert h.run()['status'] == 'completed'
    assert checked == [True] and h.get(state)['revision'] == 1


def test_recovered_worker_late_response_cannot_publish_over_retry(tmp_path, monkeypatch):
    h = Harness(tmp_path / 'store')
    state = h.submit(description='要求')
    old = h.service.claim_next()
    entered, release = Event(), Event()

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=8)
        return reply(proposals()[0])

    monkeypatch.setattr(h.runtime, 'run_turn', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(h.service.execute, old['run_id'])
        try:
            assert entered.wait(timeout=5)
            reopened = service_api().DesignService(h.root, route_resolver=h.resolve)
            assert reopened.recover_interrupted() == [old['run_id']]
            retried = reopened.retry(state['session_id'], request_id='retry', expected_revision=0,
                failed_run_id=old['run_id'])
        finally:
            release.set()
        obsolete = future.result(timeout=5)
    assert obsolete['status'] == 'interrupted'
    current = h.get(state)
    assert current['revision'] == 0 and current['run_id'] == retried['run_id']
    assert RunStore(h.root).artifacts(old['run_id']) == []
    assert len(RunStore(h.root).list_runs()) == 2

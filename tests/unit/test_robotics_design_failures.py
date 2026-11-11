"""Injected disk, commit and source-integrity failures — DR-070 / T06 RED."""
from contextlib import contextmanager
import sqlite3

import pytest

from domain_runs import RunStore
from ontology_assets.storage import Storage
from tests.build_pipeline_support import blob_bytes, db_rows
from tests.robotics_design_support import (
    Harness, assert_no_candidate, expect_error, history_bytes, no_network, proposals, reply,
)

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def execute_failure(h):
    try:
        h.run()
    except OSError:
        pass  # A failed disk can also prevent writing the failure record.
    except ValueError as exc:
        assert getattr(exc, 'code', None) == 'save_failed'


@pytest.mark.parametrize('write_number', [1, 2])
def test_admission_source_write_failure_rolls_back_pair_session_and_request(tmp_path, monkeypatch, write_number):
    h = Harness(tmp_path / 'store')
    before, files = db_rows(h.root), blob_bytes(h.root)
    original, writes = Storage.write_bytes, []

    def fail(self, relative, content):
        if relative.startswith('sources/'):
            writes.append(relative)
            if len(writes) == write_number:
                raise OSError('injected disk full')
        return original(self, relative, content)

    monkeypatch.setattr(Storage, 'write_bytes', fail)
    with pytest.raises((OSError, ValueError)):
        h.submit(description='要求')
    assert len(writes) == write_number
    assert db_rows(h.root) == before and blob_bytes(h.root) == files and h.runtime.calls == []
    monkeypatch.setattr(Storage, 'write_bytes', original)
    assert h.submit(description='要求')['status'] == 'queued'


@pytest.mark.parametrize('suffix', ['design.json', 'tbox.owl', 'manifest.json'])
def test_failed_tbox_commit_keeps_last_valid_revision(tmp_path, monkeypatch, suffix):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    snapshots, files = history_bytes(h, state), blob_bytes(h.root)
    h.runtime.responses.append(reply(proposals()[0]))
    receipt = h.message(state)
    original, writes = Storage.write_bytes, []

    def fail(self, relative, content):
        if receipt['run_id'] in relative and relative.endswith('/' + suffix):
            writes.append(relative)
            raise OSError('injected immutable artifact failure')
        return original(self, relative, content)

    monkeypatch.setattr(Storage, 'write_bytes', fail)
    execute_failure(h)
    assert writes, 'Failure must exercise the intended file-write boundary'
    assert history_bytes(h, state) == snapshots
    current = h.get(state)
    assert current['revision'] == state['revision'] and current['result'] == state['result']
    assert RunStore(h.root).get_run(receipt['run_id'])['status'] != 'completed'
    assert all(blob_bytes(h.root)[path] == raw for path, raw in files.items())
    assert_no_candidate(h)


@pytest.mark.parametrize('target', ['version', 'design.json', 'candidate.owl', 'manifest.json'])
def test_failed_abox_commit_exposes_no_asset_or_success(tmp_path, monkeypatch, target):
    h = Harness(tmp_path / 'store')
    state = h.properties()
    queued = h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    snapshots, files = history_bytes(h, queued), blob_bytes(h.root)
    original, attempted = Storage.write_bytes, []

    def fail(self, relative, content):
        match = relative.startswith('versions/') if target == 'version' else relative.endswith('/' + target)
        if match:
            attempted.append(relative)
            raise OSError('injected final save failure')
        return original(self, relative, content)

    monkeypatch.setattr(Storage, 'write_bytes', fail)
    execute_failure(h)
    assert attempted
    current = h.get(state)
    assert current['revision'] == queued['revision'] and current['output_asset_ref'] is None
    assert history_bytes(h, state) == snapshots
    assert RunStore(h.root).get_run(queued['run_id'])['status'] != 'completed'
    assert_no_candidate(h)
    assert all(blob_bytes(h.root)[path] == raw for path, raw in files.items())
    # Rollback removes any newly written, now-unregistered success bytes.
    with RunStore(h.root).storage.transaction() as conn:
        registered = {r[0] for r in conn.execute('SELECT path FROM run_artifacts')}
    assert all(path in registered for path in set(blob_bytes(h.root)) - set(files))
    monkeypatch.setattr(Storage, 'write_bytes', original)
    h.service.recover_interrupted()
    h.service.retry(state['session_id'], request_id='retry-abox', expected_revision=current['revision'],
        failed_run_id=queued['run_id'])
    assert h.run()['status'] == 'completed'
    assert len(db_rows(h.root)['assets']) == 1 and len(db_rows(h.root)['versions']) == 1
    assert len(h.runtime.calls) == 2, 'Retrying deterministic extraction must not invoke a model'


def test_database_commit_failure_rolls_back_candidate_and_all_success_artifacts(tmp_path, monkeypatch):
    h = Harness(tmp_path / 'store')
    state = h.properties()
    queued = h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    claimed = h.service.claim_next()
    before, files = db_rows(h.root), blob_bytes(h.root)
    original, attempted = Storage.transaction, []

    @contextmanager
    def fail(self, *, write=False):
        with original(self, write=write) as conn:
            yield conn
            if write and conn.execute('SELECT COUNT(*) FROM versions').fetchone()[0]:
                attempted.append(True)
                raise sqlite3.OperationalError('injected candidate commit failure')

    monkeypatch.setattr(Storage, 'transaction', fail)
    try:
        h.service.execute(claimed['run_id'])
    except (sqlite3.OperationalError, OSError):
        pass
    except ValueError as exc:
        assert getattr(exc, 'code', None) == 'save_failed'
    assert attempted and h.get(state)['output_asset_ref'] is None
    assert h.get(state)['revision'] == queued['revision']
    assert db_rows(h.root)['versions'] == before['versions'] and db_rows(h.root)['assets'] == before['assets']
    assert_no_candidate(h)
    assert all(blob_bytes(h.root)[path] == raw for path, raw in files.items())


@pytest.mark.parametrize('when', ['before_model', 'during_model', 'before_abox'])
def test_source_damage_never_advances_revision_or_publishes(tmp_path, monkeypatch, when):
    h = Harness(tmp_path / 'store')
    state = h.properties() if when == 'before_abox' else h.submit(description='要求')
    if when == 'before_abox':
        queued = h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    else:
        queued = state
    calls = len(h.runtime.calls)
    source = db_rows(h.root)['sources'][0]

    def corrupt():
        (h.root / source['path']).write_bytes(b'<tampered/>')

    if when == 'during_model':
        def request(*args, **kwargs):
            h.runtime.calls.append({'synthetic': True})
            corrupt()
            return reply(proposals()[0])
        monkeypatch.setattr(h.runtime, 'run_turn', request)
    else:
        corrupt()
    result = h.run()
    assert result['status'] == 'failed' and result['result']['error']['code'] == 'source_integrity'
    assert h.get(state)['revision'] == queued['revision']
    assert len(h.runtime.calls) == calls + (when == 'during_model')
    assert_no_candidate(h)
    before = db_rows(h.root)
    expect_error('source_integrity', lambda: h.service.retry(state['session_id'], request_id='bad-retry',
        expected_revision=queued['revision'], failed_run_id=result['run_id']), 409)
    assert db_rows(h.root) == before


def test_unsupported_abox_source_keeps_confirmed_schema_and_located_failure(tmp_path):
    from tests.robotics_design_support import inputs
    h = Harness(tmp_path / 'store')
    uploaded = inputs()['sources']
    uploaded['behavior_tree']['content'] = uploaded['behavior_tree']['content'].replace(b'Sequence', b'Fallback')
    state = h.submit(description='整理技能', sources=uploaded)
    h.runtime.responses.append(reply(proposals()[0]))
    h.run()
    state = h.get(state)
    state = h.service.confirm_classes(state['session_id'], request_id='classes',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    h.runtime.responses.append(reply(proposals()[1]))
    h.message(state)
    h.run()
    state = h.get(state)
    queued = h.service.confirm_schema(state['session_id'], request_id='schema',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    result = h.run()
    assert result['status'] == 'failed' and result['result']['error']['code'] == 'unsupported_source'
    issues = result['result']['error']['issues']
    assert issues and any(i['source_role'] == 'behavior_tree' and i['location'] for i in issues)
    current = h.get(state)
    assert current['revision'] == queued['revision'] and current['schema_confirmation'] == queued['schema_confirmation']
    assert len(h.runtime.calls) == 2
    assert_no_candidate(h)

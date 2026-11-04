"""T02 RED: source-input runs and atomic candidate completion — DR-059 / DR-060 / DR-064 / DR-065."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3
import threading

import pytest

from domain_runs import RunStore
from ontology_assets.storage import Storage
from tests.build_pipeline_support import (begin, check_complete, compute, db_rows, failure_result,
                                          setup, sha)
from tests.run_support import outputs, result, setup_run


def test_build_run_exists_before_any_ontology_version_and_pins_sources(tmp_path):
    root, assets, runs, sources, args = setup(tmp_path)
    expected = deepcopy(args)
    run = runs.create_build_run(**args)
    args['source_asset_ids'].clear()
    args['components']['compiler_version'] = 'caller mutation'
    assert assets.list_assets() == []
    assert run['status'] == 'queued' and run['result'] is None
    assert run['operation'] == 'build_ontology' and run['mode'] == 'real'
    assert run['ontology_id'] is None and run['version_id'] is None
    assert run['ontology_snapshot'] is None and run['output_asset_ref'] is None
    assert run['components'] == expected['components'] and run['session_ref'] == expected['session_ref']
    fixed = run['build_input']
    assert {k: fixed[k] for k in ('name', 'recipe', 'profile_id')} == {
        k: expected[k] for k in ('name', 'recipe', 'profile_id')}
    assert fixed['sources'] == [{k: s[k] for k in ('source_asset_id', 'role', 'sha256', 'byte_size')}
                                for s in sources]
    assert str(root) not in json.dumps(fixed)
    run['build_input']['sources'][0]['sha256'] = 'caller mutation'
    saved = RunStore(root).get_run(run['run_id'])
    assert saved['build_input']['sources'][0]['sha256'] == sources[0]['sha256']
    assert runs.events(run['run_id'])[0]['payload']['status'] == 'queued'


@pytest.mark.parametrize('bad', ['missing_role', 'duplicate_id', 'duplicate_role', 'extra_source',
                                'missing_id', 'wrong_media', 'empty_bytes', 'recipe', 'profile',
                                'empty_name', 'session', 'secret'])
def test_invalid_build_inputs_cannot_create_run_or_output(tmp_path, bad):
    root, assets, runs, sources, args = setup(tmp_path)
    create = runs.create_build_run
    if bad == 'missing_role':
        args['source_asset_ids'].pop()
    elif bad == 'duplicate_id':
        args['source_asset_ids'][1] = args['source_asset_ids'][0]
    elif bad in ('duplicate_role', 'wrong_media', 'empty_bytes'):
        source = assets.put_source(b'' if bad == 'empty_bytes' else b'<root/>',
            role='behavior_tree' if bad == 'duplicate_role' else 'node_models',
            media_type='text/csv' if bad == 'wrong_media' else 'application/xml')
        args['source_asset_ids'][1] = source['source_asset_id']
    elif bad == 'extra_source':
        extra = assets.put_source(b'{}', role='demo_supplement', media_type='application/json')
        args['source_asset_ids'].append(extra['source_asset_id'])
    elif bad == 'missing_id':
        args['source_asset_ids'][1] = 'src_missing'
    elif bad in ('recipe', 'profile'):
        args['recipe' if bad == 'recipe' else 'profile_id'] = 'unsupported'
    elif bad == 'empty_name':
        args['name'] = '  '
    elif bad == 'session':
        args['session_ref'] = {'id': 'no namespace'}
    else:
        args['components']['model']['api_key'] = 'SYNTHETIC-NOT-A-REAL-KEY'
    before = db_rows(root)
    with pytest.raises((ValueError, KeyError)) as caught:
        create(**args)
    assert 'SYNTHETIC-NOT-A-REAL-KEY' not in str(caught.value)
    assert db_rows(root) == before
    assert runs.list_runs() == [] and assets.list_assets() == []
    assert all(b'SYNTHETIC-NOT-A-REAL-KEY' not in p.read_bytes() for p in root.rglob('*') if p.is_file())


@pytest.mark.parametrize('damage', ['changed', 'missing', 'symlink'])
def test_source_integrity_is_checked_before_run_creation(tmp_path, damage):
    root, assets, runs, sources, args = setup(tmp_path)
    create = runs.create_build_run
    path = root / sources[0]['path']
    if damage == 'changed':
        path.write_bytes(b'<root>changed</root>')
    elif damage == 'missing':
        path.unlink()
    else:
        outside = tmp_path / 'outside.xml'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
    before = db_rows(root)
    with pytest.raises((ValueError, FileNotFoundError)):
        create(**args)
    assert db_rows(root) == before
    assert assets.list_assets() == []


def test_start_rechecks_fixed_source_and_preserves_queued_record_on_damage(tmp_path):
    root, _, runs, sources, args = setup(tmp_path)
    run = runs.create_build_run(**args)
    before = db_rows(root)
    (root / sources[1]['path']).write_bytes(b'damaged')
    with pytest.raises(ValueError):
        runs.start(run['run_id'])
    assert db_rows(root) == before


def test_source_run_events_reopen_and_retry_preserve_original_inputs(tmp_path):
    root, assets, runs, _, run = begin(tmp_path)
    queued = runs.create_build_run(name='another', recipe='bt_xml', profile_id='ur_pick_place',
        source_asset_ids=[s['source_asset_id'] for s in run['build_input']['sources']],
        components=run['components'], session_ref=run['session_ref'])
    rid = run['run_id']
    event = runs.append_event(rid, kind='stage', payload={'stage': 'parsing'})
    assert runs.events(rid, after=event['seq'] - 1) == [event]
    reopened = RunStore(root)
    assert reopened.get_run(rid)['status'] == 'running'
    # Explicit recovery only; owner arbitration is a T04 executor responsibility.
    assert reopened.recover_interrupted() == [rid]
    before = deepcopy(reopened.get_run(rid)), deepcopy(reopened.events(rid))
    retry = reopened.retry(rid)
    assert retry['run_id'] != rid and retry['retry_of'] == rid and retry['status'] == 'queued'
    assert retry['build_input'] == run['build_input'] and retry['output_asset_ref'] is None
    assert (reopened.get_run(rid), reopened.events(rid)) == before
    assert reopened.get_run(queued['run_id'])['status'] == 'queued'
    assert reopened.recover_interrupted() == []
    assert assets.list_assets() == []


def test_completion_binds_candidate_output_without_mutating_input(tmp_path):
    call = compute()
    root, assets, runs, _, before = begin(tmp_path)
    product = call(root, before['run_id'])
    saved = runs.finish_build(before['run_id'], **product)
    check_complete(root, before, saved)
    assert len(assets.list_assets()) == 1
    with pytest.raises(ValueError):
        assets.load_robotics(**saved['output_asset_ref'], mode='real')


def test_failed_build_has_report_and_manifest_but_no_output_version(tmp_path):
    root, assets, runs, _, before = begin(tmp_path)
    error = failure_result()
    saved = runs.finish_build(before['run_id'], result=error, ontology=None)
    assert saved['status'] == 'failed' and saved['result'] == error
    assert saved['output_asset_ref'] is None and saved['build_input'] == before['build_input']
    assert assets.list_assets() == []
    assert sorted(a['kind'] for a in runs.artifacts(saved['run_id'])) == ['manifest', 'validation']
    report = next(a for a in runs.artifacts(saved['run_id']) if a['kind'] == 'validation')
    assert json.loads(runs.read_artifact(saved['run_id'], report['artifact_id'])) == error
    assert runs.manifest(saved['run_id'])['output_asset_ref'] is None
    retry = runs.retry(saved['run_id'])
    assert retry['retry_of'] == saved['run_id'] and retry['build_input'] == saved['build_input']


@pytest.mark.parametrize('bad', ['no_ontology', 'wrong_digest', 'invalid_owl', 'error_with_owl', 'verified_execution',
                                'foreign_source_owl', 'forged_validation'])
def test_inconsistent_build_completion_has_no_visible_partial_output(tmp_path, bad):
    call = compute()
    root, _, runs, _, run = begin(tmp_path)
    product = call(root, run['run_id'])
    if bad == 'no_ontology':
        product['ontology'] = None
    elif bad == 'wrong_digest':
        product['result']['validation']['ontology_sha256'] = '0' * 64
    elif bad == 'invalid_owl':
        product['ontology'] = b'<rdf>invalid</rdf>'
        product['result']['validation']['ontology_sha256'] = sha(product['ontology'])
    elif bad == 'error_with_owl':
        product['result'] = failure_result()
    elif bad == 'verified_execution':
        product['result']['execution_readiness'] = 'verified'
    elif bad == 'foreign_source_owl':
        # A valid ontology/report pair from other source bytes cannot satisfy this run.
        from tests.robotics_support import SOURCE
        changed = SOURCE.read_bytes().replace(b'capture pose', b'another capture pose', 1)
        assert changed != SOURCE.read_bytes()
        other_root, _, _, _, other = begin(tmp_path / 'other', source=changed)
        product = call(other_root, other['run_id'])
    else:
        product['result']['validation']['shacl_conforms'] = False
    before = db_rows(root)
    with pytest.raises(ValueError):
        runs.finish_build(run['run_id'], **product)
    assert db_rows(root) == before
    assert runs.artifacts(run['run_id']) == []


def test_source_modified_after_compute_blocks_successful_commit(tmp_path):
    call = compute()
    root, _, runs, sources, run = begin(tmp_path)
    product = call(root, run['run_id'])
    before = db_rows(root)
    (root / sources[0]['path']).write_bytes(b'changed after compute')
    with pytest.raises(ValueError):
        runs.finish_build(run['run_id'], **product)
    assert db_rows(root) == before


@pytest.mark.parametrize('fault', ['write', 'readback', 'commit'])
def test_durable_fault_rolls_back_asset_version_run_events_and_artifact_indexes(tmp_path, monkeypatch, fault):
    call = compute()
    root, _, runs, _, run = begin(tmp_path)
    product = call(root, run['run_id'])
    finish = runs.finish_build
    before = db_rows(root)
    injected = []
    if fault == 'write':
        original = Storage.write_bytes
        calls = []

        def fail_write(self, relative, content):
            calls.append(relative)
            if len(calls) == 2:
                injected.append('write')
                raise OSError('injected file write failure')
            return original(self, relative, content)

        monkeypatch.setattr(Storage, 'write_bytes', fail_write)
    elif fault == 'readback':
        original_read = Storage.read_bytes

        def fail_read(self, relative, expected):
            if relative.startswith(('versions/', 'artifacts/')):
                injected.append('readback')
                raise ValueError('injected output digest mismatch')
            return original_read(self, relative, expected)

        monkeypatch.setattr(Storage, 'read_bytes', fail_read)
    else:
        transaction = Storage.transaction

        @contextmanager
        def fail_commit(self, *, write=False):
            with transaction(self, write=write) as conn:
                yield conn
                if write and conn.execute('SELECT COUNT(*) FROM versions').fetchone()[0] > len(before['versions']):
                    # Fail only after the success rows have been staged, not constructor/schema setup.
                    injected.append('commit')
                    assert conn.execute('SELECT status FROM runs WHERE run_id=?', (run['run_id'],)).fetchone()[0] == 'completed'
                    assert conn.execute('SELECT COUNT(*) FROM run_artifacts WHERE run_id=?', (run['run_id'],)).fetchone()[0] == 3
                    raise sqlite3.OperationalError('injected commit failure')

        monkeypatch.setattr(Storage, 'transaction', fail_commit)
    with pytest.raises((OSError, ValueError, sqlite3.OperationalError)):
        finish(run['run_id'], **product)
    assert injected == [fault]
    assert db_rows(root) == before
    assert runs.artifacts(run['run_id']) == []
    # Unindexed files may be left for cleanup; they must never be advertised as successful output.


def test_concurrent_completion_has_one_winner_and_terminal_record_is_immutable(tmp_path):
    call = compute()
    root, assets, runs, _, run = begin(tmp_path)
    product = call(root, run['run_id'])
    finish = runs.finish_build
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait(timeout=10)
        try:
            return finish(run['run_id'], **deepcopy(product))
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(lambda _: attempt(), range(2)))
    winners = [row for row in saved if row is not None]
    assert len(winners) == 1
    check_complete(root, run, winners[0])
    assert len(assets.list_assets()) == 1
    assert len(assets.list_versions(winners[0]['output_asset_ref']['ontology_id'])) == 1
    before = db_rows(root)
    with pytest.raises(ValueError):
        finish(run['run_id'], result=failure_result(), ontology=None)
    assert db_rows(root) == before


def test_build_run_cannot_complete_through_generation_contract(tmp_path):
    root, _, runs, _, run = begin(tmp_path)
    before = db_rows(root)
    with pytest.raises(ValueError):
        runs.finish(run['run_id'], result=result(), artifacts=outputs())
    assert db_rows(root) == before


def test_version_input_run_cannot_complete_as_build(tmp_path):
    root, _, runs, run, _ = setup_run(tmp_path)
    finish = runs.finish_build
    runs.start(run['run_id'])
    before = db_rows(root)
    with pytest.raises(ValueError):
        finish(run['run_id'], result=failure_result(), ontology=None)
    assert db_rows(root) == before


def test_old_generated_result_still_requires_candidate_xml(tmp_path):
    root, _, runs, run, _ = setup_run(tmp_path)
    runs.start(run['run_id'])
    before = db_rows(root)
    # Adding built must not globally weaken the existing generated/candidate requirement.
    with pytest.raises(ValueError):
        runs.finish(run['run_id'], result=result(), artifacts=[outputs()[1]])
    with pytest.raises(ValueError):
        runs.finish(run['run_id'], result={**result(), 'status': 'built'}, artifacts=outputs())
    assert db_rows(root) == before

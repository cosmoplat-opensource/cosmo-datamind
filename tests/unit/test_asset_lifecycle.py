"""IR-029 T02 RED: reversible asset state, stable previews and history — DR-075."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import importlib.util
import json
import sqlite3

import pytest

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.payloads import json_text


DATA = b'{"objects":[{"id":"part","table":"parts"}],"links":[]}'
RDF = b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
COMPONENTS = {'profile_id': None, 'compiler_version': None, 'rule_digest': None,
              'prompt_version': None, 'model': {'invoked': False}}


def lifecycle(root, **kwargs):
    # A missing implementation is an explicit RED assertion, not a collection error.
    assert importlib.util.find_spec('ontology_assets.lifecycle') is not None, 'DR-075 lifecycle service is not implemented'
    module = importlib.import_module('ontology_assets.lifecycle')
    return module.LifecycleService(root, **kwargs)


@pytest.fixture
def sample(tmp_path):
    root = tmp_path / 'assets'
    assets = AssetStore(root)
    source = assets.put_source(b'shared original source', role='reference', media_type='text/plain')
    refs = []
    for domain, raw, kind in [('data', DATA, 'datamind_ir'), ('robotics', RDF, 'robotics_rdf')]:
        asset = assets.create_asset(name=domain + ' asset', domain=domain)
        first = assets.add_version(asset['ontology_id'], raw, payload_kind=kind,
                                   source_asset_ids=[source['source_asset_id']])
        second = assets.add_version(asset['ontology_id'], raw, payload_kind=kind,
                                    parent_version_id=first['version_id'],
                                    source_asset_ids=[source['source_asset_id']])
        refs.append({'asset': asset, 'versions': [first, second], 'raw': raw})
    return {'root': root, 'assets': assets, 'source': source, 'refs': refs}


def rows(root, table):
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        return conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()


def metadata(root):
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: conn.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}


def blobs(root):
    return {str(p.relative_to(root)): p.read_bytes() for folder in ['sources', 'versions', 'artifacts']
            for p in (root / folder).rglob('*') if p.is_file()}


def apply(service, oid, operation='delete', request_id='delete-1', **overrides):
    preview = service.preview(oid, operation=operation)
    body = {'expected_revision': preview['revision'], 'impact_sha256': preview['impact_sha256'],
            'request_id': request_id, 'confirmed': True, **overrides}
    return getattr(service, operation)(oid, **body)


def rejected(code, action):
    with pytest.raises(ValueError) as exc:
        action()
    assert getattr(exc.value, 'code', None) == code


def make_run(sample, ref):
    runs = RunStore(sample['root'])
    run = runs.create_run(ontology_id=ref['asset']['ontology_id'], version_id=ref['versions'][0]['version_id'],
                          operation='generate_behavior_tree', task={}, mode='real',
                          session_ref={'namespace': 'lifecycle_test', 'id': 'history'}, components=COMPONENTS)
    return runs, run


@pytest.mark.parametrize('index', [0, 1], ids=['data', 'robotics'])
def test_delete_and_restore_preserve_identity_versions_and_immutable_bytes(sample, index):
    service = lifecycle(sample['root']); ref = sample['refs'][index]; oid = ref['asset']['ontology_id']
    before = blobs(sample['root']), rows(sample['root'], 'versions'), rows(sample['root'], 'sources')
    assert service.get(oid)['state'] == 'active' and service.get(oid)['revision'] == 0
    deleted = apply(service, oid)
    assert deleted['lifecycle']['state'] == 'deleted' and deleted['lifecycle']['revision'] == 1
    assert oid not in [a['ontology_id'] for a in service.list_assets()]
    assert oid in [a['ontology_id'] for a in service.list_assets(state='deleted')]
    assert oid in [a['ontology_id'] for a in service.list_assets(state=None)]
    for version in ref['versions']:
        assert sample['assets'].read_payload(oid, version['version_id']) == ref['raw']
    restored = apply(service, oid, 'restore', 'restore-1')
    assert restored['lifecycle']['state'] == 'active' and restored['lifecycle']['revision'] == 2
    assert oid in [a['ontology_id'] for a in service.list_assets()]
    assert before == (blobs(sample['root']), rows(sample['root'], 'versions'), rows(sample['root'], 'sources'))


def test_preview_is_stable_readonly_and_counts_shared_sources_by_asset(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    before = metadata(sample['root']), blobs(sample['root'])
    first = service.preview(oid); second = service.preview(oid)
    assert first == second and first['can_apply'] is True
    assert first['impact']['version_ids'] == [v['version_id'] for v in sample['refs'][0]['versions']]
    assert first['impact']['sources'] == [{'source_asset_id': sample['source']['source_asset_id'], 'other_asset_count': 1}]
    assert len(first['impact_sha256']) == 64
    assert before == (metadata(sample['root']), blobs(sample['root']))
    assert service.events(oid) == []


def test_delete_does_not_delete_shared_source_or_other_asset(sample):
    service = lifecycle(sample['root']); first, other = sample['refs']
    apply(service, first['asset']['ontology_id'])
    assert service.get(other['asset']['ontology_id'])['state'] == 'active'
    assert sample['assets'].read_source(sample['source']['source_asset_id']) == b'shared original source'
    assert sample['assets'].read_payload(other['asset']['ontology_id'], other['versions'][0]['version_id']) == RDF


@pytest.mark.parametrize('confirmed', [False, None, 1, 'true'])
def test_confirmation_is_required_and_truthy_values_do_not_authorize_delete(sample, confirmed):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    before = metadata(sample['root']), blobs(sample['root'])
    rejected('confirmation_required', lambda: apply(service, oid, confirmed=confirmed))
    assert before == (metadata(sample['root']), blobs(sample['root']))


@pytest.mark.parametrize('revision', [True, -1, 0.5, '0'])
def test_expected_revision_requires_a_nonnegative_integer(sample, revision):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    rejected('invalid_request', lambda: apply(service, oid, expected_revision=revision))
    assert service.get(oid)['revision'] == 0 and service.events(oid) == []


@pytest.mark.parametrize('digest', ['', 'bad', 'g' * 64, None])
def test_invalid_impact_digest_cannot_change_state(sample, digest):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    rejected('invalid_request', lambda: apply(service, oid, impact_sha256=digest))
    assert service.get(oid)['state'] == 'active'


@pytest.mark.parametrize('request_id', ['', '../request', 'bad\0request'])
def test_invalid_request_identity_is_rejected_before_changes(sample, request_id):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    rejected('invalid_request', lambda: apply(service, oid, request_id=request_id))
    assert service.events(oid) == []


def test_delete_and_restore_requests_are_idempotent_across_reopen(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    preview = service.preview(oid)
    args = {'expected_revision': 0, 'impact_sha256': preview['impact_sha256'], 'confirmed': True, 'request_id': 'same'}
    first = service.delete(oid, **args)
    again = lifecycle(sample['root']).delete(oid, **args)
    assert again['replayed'] is True and again['operation'] == first['operation']
    assert again['lifecycle']['revision'] == 1 and len(service.events(oid)) == 1
    restore_preview = service.preview(oid, operation='restore')
    restore_args = {'expected_revision': 1, 'impact_sha256': restore_preview['impact_sha256'],
                    'confirmed': True, 'request_id': 'restore'}
    restored = service.restore(oid, **restore_args)
    replay = lifecycle(sample['root']).restore(oid, **restore_args)
    assert replay['replayed'] is True and replay['operation'] == restored['operation']
    assert replay['lifecycle']['revision'] == 2 and len(service.events(oid)) == 2


def test_old_delete_replay_after_restore_does_not_delete_again(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    p = service.preview(oid)
    args = {'expected_revision': 0, 'impact_sha256': p['impact_sha256'], 'confirmed': True, 'request_id': 'original'}
    first = service.delete(oid, **args)
    apply(service, oid, 'restore', 'restore')
    replay = service.delete(oid, **args)
    assert replay['operation'] == first['operation'] and replay['replayed'] is True
    assert replay['lifecycle']['state'] == 'active' and replay['lifecycle']['revision'] == 2
    assert len(service.events(oid)) == 2


def test_request_id_changed_input_conflicts_before_reapplying_state(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']; p = service.preview(oid)
    args = {'expected_revision': 0, 'impact_sha256': p['impact_sha256'], 'confirmed': True, 'request_id': 'same'}
    service.delete(oid, **args, reason='first')
    rejected('request_conflict', lambda: service.delete(oid, **args, reason='different'))
    assert service.get(oid)['revision'] == 1 and len(service.events(oid)) == 1


def test_request_identity_is_scoped_to_asset(sample):
    service = lifecycle(sample['root'])
    for ref in sample['refs']:
        assert apply(service, ref['asset']['ontology_id'], request_id='same')['lifecycle']['state'] == 'deleted'


def test_stale_revision_and_valid_but_wrong_digest_leave_no_changes(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    rejected('revision_conflict', lambda: apply(service, oid, expected_revision=10))
    wrong = '0' * 64 if service.preview(oid)['impact_sha256'] != '0' * 64 else '1' * 64
    rejected('impact_changed', lambda: apply(service, oid, impact_sha256=wrong))
    assert service.get(oid)['revision'] == 0 and service.events(oid) == []


@pytest.mark.parametrize('change', ['version', 'shared_owner'])
def test_reference_changes_invalidate_preview_without_changing_lifecycle_revision(sample, change):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']; p = service.preview(oid)
    assets = sample['assets']
    if change == 'version':
        assets.add_version(oid, DATA, payload_kind='datamind_ir', source_asset_ids=[sample['source']['source_asset_id']])
    else:
        third = assets.create_asset(name='new owner', domain='data')
        assets.add_version(third['ontology_id'], DATA, payload_kind='datamind_ir',
                           source_asset_ids=[sample['source']['source_asset_id']])
    rejected('impact_changed', lambda: service.delete(oid, expected_revision=0, impact_sha256=p['impact_sha256'],
                                                      request_id='stale-impact', confirmed=True))
    assert service.get(oid)['revision'] == 0 and service.events(oid) == []


@pytest.mark.parametrize('index', [0, 1], ids=['data', 'robotics'])
def test_completed_history_and_download_bytes_survive_delete_restore(sample, index):
    service = lifecycle(sample['root']); ref = sample['refs'][index]; oid = ref['asset']['ontology_id']
    runs, run = make_run(sample, ref); runs.start(run['run_id'])
    runs.finish(run['run_id'], result={'status': 'needs_information'}, artifacts=[{
        'name': 'validation.json', 'kind': 'validation', 'media_type': 'application/json', 'content': b'{"issues":[]}'}])
    before = runs.get_run(run['run_id']), runs.events(run['run_id']), runs.manifest(run['run_id'])
    files = {a['artifact_id']: runs.read_artifact(run['run_id'], a['artifact_id']) for a in runs.artifacts(run['run_id'])}
    preview = service.preview(oid)
    assert {'run_id': run['run_id'], 'status': 'completed'} in preview['impact']['runs']
    apply(service, oid)
    assert before == (runs.get_run(run['run_id']), runs.events(run['run_id']), runs.manifest(run['run_id']))
    assert files == {a['artifact_id']: runs.read_artifact(run['run_id'], a['artifact_id']) for a in runs.artifacts(run['run_id'])}
    apply(service, oid, 'restore', 'restore')
    assert before == (runs.get_run(run['run_id']), runs.events(run['run_id']), runs.manifest(run['run_id']))


def test_audit_records_one_event_per_successful_transition(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    first = apply(service, oid, reason='keep history'); second = apply(service, oid, 'restore', 'restore')
    events = lifecycle(sample['root']).events(oid)
    assert len(events) == 2
    assert [(e['from_state'], e['to_state'], e['revision']) for e in events] == [('active', 'deleted', 1), ('deleted', 'active', 2)]
    assert [e['operation_id'] for e in events] == [first['operation']['operation_id'], second['operation']['operation_id']]
    assert events[0]['reason'] == 'keep history'


def test_fresh_restore_of_active_and_delete_of_deleted_are_conflicts(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    rejected('state_conflict', lambda: apply(service, oid, 'restore', 'never-deleted'))
    apply(service, oid)
    rejected('state_conflict', lambda: apply(service, oid, request_id='different-delete'))
    assert len(service.events(oid)) == 1


def test_server_registered_builtin_protection_persists_after_reopen(sample):
    oid = sample['refs'][1]['asset']['ontology_id']
    service = lifecycle(sample['root'], protected_asset_ids=[oid])
    assert service.preview(oid)['protected'] is True and service.preview(oid)['can_apply'] is False
    rejected('protected_asset', lambda: apply(service, oid))
    reopened = lifecycle(sample['root'])
    assert reopened.get(oid)['protected'] is True
    rejected('protected_asset', lambda: apply(reopened, oid))


def test_user_asset_name_and_demo_metadata_do_not_create_builtin_protection(sample):
    asset = sample['assets'].create_asset(name='demo', domain='data')
    sample['assets'].add_version(asset['ontology_id'], DATA, payload_kind='datamind_ir', metadata={'mode': 'demo'})
    service = lifecycle(sample['root'])
    assert service.get(asset['ontology_id'])['protected'] is False
    assert apply(service, asset['ontology_id'])['lifecycle']['state'] == 'deleted'


def test_storage_commit_failure_rolls_back_state_request_and_audit(sample, monkeypatch):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    before = metadata(sample['root']), blobs(sample['root']); original = service.storage.transaction
    @contextmanager
    def fail_commit(*, write=False):
        with original(write=write) as conn:
            yield conn
            if write:
                raise sqlite3.OperationalError('private simulated commit failure')
    with monkeypatch.context() as patch:
        patch.setattr(service.storage, 'transaction', fail_commit)
        rejected('storage_unavailable', lambda: apply(service, oid))
    assert before == (metadata(sample['root']), blobs(sample['root']))


def test_readonly_service_can_preview_but_cannot_mutate(sample):
    lifecycle(sample['root'])
    service = lifecycle(sample['root'], read_only=True); oid = sample['refs'][0]['asset']['ontology_id']
    before = metadata(sample['root']), blobs(sample['root'])
    assert service.preview(oid)['state'] == 'active'
    rejected('read_only', lambda: apply(service, oid))
    assert before == (metadata(sample['root']), blobs(sample['root']))


def test_readonly_missing_root_never_creates_storage(tmp_path):
    root = tmp_path / 'absent'
    rejected('storage_unavailable', lambda: lifecycle(root, read_only=True))
    assert not root.exists()


def legacy_callbacks(snapshot):
    return {'name': 'legacy fixture', 'load_effective': lambda key: deepcopy(snapshot),
            'provenance': lambda key: {'source_sha256': 'a' * 64, 'edits_sha256': 'b' * 64}}


def test_legacy_binding_captures_effective_edits_and_reuses_existing_asset_mapping(sample):
    from ontology_assets.legacy import register_legacy
    snapshot = json.loads(DATA); snapshot['objects'][0]['aliases'] = ['effective edits']
    callbacks = legacy_callbacks(snapshot)
    old = register_legacy(sample['assets'], enumerate_graphs=lambda: [{'key': 'built_keep', 'name': callbacks['name']}],
                          load_effective=callbacks['load_effective'], provenance=callbacks['provenance'])[0]
    service = lifecycle(sample['root']); binding = service.bind_legacy('built_keep', **callbacks)
    assert binding['ontology_id'] == old['ontology_id'] and binding['version_id'] == old['version_id']
    assert sample['assets'].load_data_ir(binding['ontology_id'], binding['version_id']) == snapshot
    snapshot['objects'][0]['aliases'].append('later mutation')
    assert sample['assets'].load_data_ir(binding['ontology_id'], binding['version_id']) != snapshot
    assert service.resolve_legacy('built_keep')['ontology_id'] == old['ontology_id']


def test_deleted_legacy_key_cannot_be_resurrected_by_changed_or_reappearing_files(sample):
    service = lifecycle(sample['root']); snapshot = json.loads(DATA)
    first = service.bind_legacy('built_old', **legacy_callbacks(snapshot)); oid = first['ontology_id']
    apply(service, oid)
    def must_not_read(key):
        raise AssertionError('Deleted legacy aliases must resolve before any file callback')
    again = service.bind_legacy('built_old', name='changed', load_effective=must_not_read, provenance=must_not_read)
    assert again['ontology_id'] == oid and again['version_id'] == first['version_id']
    assert again['lifecycle']['state'] == 'deleted' and len(sample['assets'].list_versions(oid)) == 1
    apply(service, oid, 'restore', 'restore')
    assert service.resolve_legacy('built_old')['version_id'] == first['version_id']
    assert sample['assets'].load_data_ir(oid, first['version_id']) == snapshot


def test_imported_old_deletion_with_snapshot_is_restorable(sample):
    service = lifecycle(sample['root'])
    binding = service.bind_legacy('built_previously_deleted', **legacy_callbacks(json.loads(DATA)), deleted=True)
    assert binding['lifecycle']['state'] == 'deleted' and binding['restore_available'] is True
    assert apply(service, binding['ontology_id'], 'restore', 'restore')['lifecycle']['state'] == 'active'


def test_previously_physically_deleted_legacy_key_keeps_durable_tombstone_without_fake_payload(sample):
    service = lifecycle(sample['root']); assets_before = rows(sample['root'], 'assets'); versions_before = rows(sample['root'], 'versions')
    def missing(key):
        raise FileNotFoundError('no historical bytes')
    binding = service.bind_legacy('built_missing', name='old deletion', load_effective=missing,
                                   provenance=lambda key: {}, deleted=True)
    assert binding['ontology_id'] is None and binding['version_id'] is None
    assert binding['lifecycle']['state'] == 'deleted' and binding['restore_available'] is False
    assert lifecycle(sample['root']).resolve_legacy('built_missing') == binding
    assert assets_before == rows(sample['root'], 'assets') and versions_before == rows(sample['root'], 'versions')


@pytest.mark.parametrize('key', ['demo', 'app'])
def test_builtin_legacy_identity_is_protected(sample, key):
    service = lifecycle(sample['root'])
    binding = service.bind_legacy(key, **legacy_callbacks(json.loads(DATA)))
    assert service.get(binding['ontology_id'])['protected'] is True
    rejected('protected_asset', lambda: apply(service, binding['ontology_id']))


def test_legacy_changed_during_snapshot_rolls_back_all_registration(sample):
    service = lifecycle(sample['root']); before = metadata(sample['root']), blobs(sample['root']); calls = []
    def changing(key):
        calls.append(key); return {'revision': len(calls)}
    rejected('legacy_changed', lambda: service.bind_legacy('built_unstable', name='unstable',
                load_effective=lambda key: json.loads(DATA), provenance=changing))
    assert before == (metadata(sample['root']), blobs(sample['root']))


def test_unknown_or_invalid_legacy_key_never_falls_back_to_demo(sample):
    service = lifecycle(sample['root'])
    rejected('not_found', lambda: service.resolve_legacy('built_unknown'))
    rejected('invalid_request', lambda: service.bind_legacy('../escape', **legacy_callbacks(json.loads(DATA))))


def test_restore_revision_stays_different_from_predelete_basis(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']
    old = service.get(oid)['revision']; apply(service, oid); apply(service, oid, 'restore', 'restore')
    assert service.get(oid)['revision'] > old
    assert service.preview(oid)['revision'] == 2


def test_legacy_canonical_snapshot_does_not_rewrite_sources_or_callback_values(sample):
    service = lifecycle(sample['root']); snapshot = json.loads(DATA); before = deepcopy(snapshot)
    binding = service.bind_legacy('built_canonical', **legacy_callbacks(snapshot))
    assert sample['assets'].read_payload(binding['ontology_id'], binding['version_id']) == json_text(before).encode()
    assert snapshot == before and sample['assets'].read_source(sample['source']['source_asset_id']) == b'shared original source'


def test_simultaneous_identical_requests_commit_only_one_transition(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']; p = service.preview(oid)
    args = {'expected_revision': 0, 'impact_sha256': p['impact_sha256'], 'confirmed': True, 'request_id': 'concurrent'}
    def submit(_):
        return lifecycle(sample['root']).delete(oid, **args)
    with ThreadPoolExecutor(max_workers=4) as workers:
        replies = list(workers.map(submit, range(8)))
    assert len({r['operation']['operation_id'] for r in replies}) == 1
    assert sum(not r['replayed'] for r in replies) == 1
    assert service.get(oid)['revision'] == 1 and len(service.events(oid)) == 1


def test_simultaneous_distinct_requests_cannot_both_consume_same_revision(sample):
    service = lifecycle(sample['root']); oid = sample['refs'][0]['asset']['ontology_id']; p = service.preview(oid)
    def submit(rid):
        try:
            result = lifecycle(sample['root']).delete(oid, expected_revision=0, impact_sha256=p['impact_sha256'],
                                                       request_id=rid, confirmed=True)
            return 'success', result
        except ValueError as error:
            return getattr(error, 'code', None), None
    with ThreadPoolExecutor(max_workers=2) as workers:
        replies = list(workers.map(submit, ['first', 'second']))
    assert sum(kind == 'success' for kind, _ in replies) == 1
    assert all(kind in ('success', 'revision_conflict', 'state_conflict') for kind, _ in replies)
    assert service.get(oid)['revision'] == 1 and len(service.events(oid)) == 1


@pytest.mark.parametrize('oid', ['', '../outside', 'bad\0id', None])
def test_invalid_asset_identifiers_do_not_touch_storage(sample, oid):
    service = lifecycle(sample['root']); before = metadata(sample['root']), blobs(sample['root'])
    rejected('invalid_request', lambda: service.get(oid))
    assert before == (metadata(sample['root']), blobs(sample['root']))


def test_unknown_asset_is_not_found_without_creating_a_lifecycle_record(sample):
    service = lifecycle(sample['root']); before = metadata(sample['root'])
    rejected('not_found', lambda: service.get('ont_unknown'))
    assert before == metadata(sample['root'])

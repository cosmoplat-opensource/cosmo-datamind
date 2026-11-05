"""T02 RED: immutable governance transactions, reviews and publication — DR-059 / DR-061 / DR-064 / DR-065."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
import sqlite3

import pytest
from rdflib import Graph, RDF, RDFS, URIRef

from domain_runs import RunStore
from ontology_assets import AssetStore
from ontology_assets.storage import Storage
from tests.asset_support import add_data
from tests.build_pipeline_support import blob_bytes, db_rows, sha
from tests.robotics_governance_support import (POLICY, TASK, evidence_spec, field, module, publish_args,
                                               review, save_args, setup)
from tests.robotics_support import S
from tests.storage_pipeline_support import check_saved, generate, pipeline, setup_assets


def test_save_creates_immutable_candidate_and_read_only_head_does_not_write(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    before, files = db_rows(root), blob_bytes(root)
    assert service.head(oid) == {'version_id': base['version_id'], 'revision': 0}
    assert db_rows(root) == before and blob_bytes(root) == files
    version = service.save(oid, **save_args(base))
    assert version['version_id'] != base['version_id'] and version['parent_version_id'] == base['version_id']
    assert version['publication_state'] == 'candidate' and version['validation'] is None
    assert version['source_asset_ids'] == base['source_asset_ids']
    assert service.head(oid) == {'version_id': version['version_id'], 'revision': 1}
    current = Graph().parse(data=assets.read_payload(oid, version['version_id']), format='xml')
    assert str(current.value(S.PartType_A, RDFS.label)) == '人工零件类型'
    assert str(current.value(URIRef('https://example.org/ur-skill'), S.evidencePolicy)) == POLICY
    assert not list(current.subjects(RDF.type, S.ReadinessEvidence))
    assert assets.get_version(oid, base['version_id']) == base
    for path, content in files.items(): assert (root / path).read_bytes() == content
    history = service.history(oid)
    assert len(history) == 1
    assert history[0]['actor'] == save_args(base)['actor'] and history[0]['reason'] == save_args(base)['reason']
    assert history[0]['version_id'] == version['version_id']
    assert api.GovernanceService(root, read_only=True).head(oid) == service.head(oid)


def test_save_retry_across_reopen_is_idempotent_but_changed_input_conflicts(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    args = save_args(base)
    version = api.GovernanceService(root).save(oid, **args)
    before = db_rows(root)
    service = api.GovernanceService(root)
    assert service.save(oid, **args) == version
    assert db_rows(root) == before
    args['operations'] = [field(value='different')]
    with pytest.raises(api.GovernanceError) as caught: service.save(oid, **args)
    assert caught.value.code == 'request_conflict'
    assert db_rows(root) == before and len(assets.list_versions(oid)) == 2


@pytest.mark.parametrize('bad', ['head', 'revision', 'digest', 'cross_asset', 'data_domain', 'actor', 'reason'])
def test_invalid_save_is_rejected_without_visible_partial_records(tmp_path, bad):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    args = save_args(base)
    if bad == 'head': args['expected_head_version_id'] = 'ver_missing'
    elif bad == 'revision': args['expected_revision'] = 7
    elif bad == 'digest': args['base_payload_sha256'] = '0' * 64
    elif bad == 'actor': args['actor'] = ''
    elif bad == 'reason': args['reason'] = ''
    elif bad == 'cross_asset': oid = assets.create_asset(name='other', domain='robotics')['ontology_id']
    else:
        asset, version = add_data(assets)
        oid, args = asset['ontology_id'], save_args(version)
    before, files = db_rows(root), blob_bytes(root)
    with pytest.raises(api.GovernanceError): service.save(oid, **args)
    assert db_rows(root) == before and blob_bytes(root) == files


def test_two_editors_cannot_both_replace_same_head(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    api.GovernanceService(root)
    def submit(number):
        try:
            return api.GovernanceService(root).save(oid, **save_args(base, request_id=f'editor-{number}',
                operations=[field(value=f'编辑者{number}')]))
        except api.GovernanceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [1, 2]))
    assert len([r for r in results if isinstance(r, dict)]) == 1
    assert results.count('head_conflict') == 1
    assert len(assets.list_versions(oid)) == 2
    assert len(api.GovernanceService(root).history(oid)) == 1


def test_explicit_old_base_requires_current_head_but_preserves_selected_parent(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    first = service.save(oid, **save_args(base))
    second = service.save(oid, **save_args(base, head=first['version_id'], revision=1, request_id='branch-old',
        operations=[field(S.Station_B, value='旧基线分支')]))
    assert second['parent_version_id'] == base['version_id']
    data = Graph().parse(data=assets.read_payload(oid, second['version_id']), format='xml')
    assert str(data.value(S.PartType_A, RDFS.label)) == '示例零件类型A'
    assert service.head(oid) == {'version_id': second['version_id'], 'revision': 2}


def test_commit_failure_rolls_back_version_head_history_and_retry_key(tmp_path, monkeypatch):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    before = db_rows(root)
    original = Storage.transaction
    writes = []
    @contextmanager
    def fail_commit(self, *, write=False):
        with original(self, write=write) as conn:
            yield conn
            if write:
                writes.append(conn.execute('SELECT COUNT(*) FROM versions').fetchone()[0])
                raise sqlite3.OperationalError('synthetic commit failure')
    monkeypatch.setattr(Storage, 'transaction', fail_commit)
    with pytest.raises(sqlite3.OperationalError): service.save(oid, **save_args(base))
    assert writes and max(writes) > len(before['versions'])
    assert db_rows(root) == before
    monkeypatch.setattr(Storage, 'transaction', original)
    saved = service.save(oid, **save_args(base))
    assert len(assets.list_versions(oid)) == 2 and saved['publication_state'] == 'candidate'


@pytest.mark.parametrize('case', ['unreviewed', 'rejected', 'other_version', 'wrong_digest', 'stale_head', 'tampered_bytes'])
def test_publication_rejects_missing_stale_or_mismatched_review(tmp_path, case):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    version = service.save(oid, **save_args(base))
    reviewed = {'review_id': 'review_missing'} if case == 'unreviewed' else review(service, oid, version,
        decision='rejected' if case == 'rejected' else 'accepted')
    args = publish_args(version, reviewed)
    if case in ('other_version', 'stale_head'):
        newer = service.save(oid, **save_args(version, revision=1, request_id='save-2'))
        if case == 'other_version': args = publish_args(newer, reviewed, revision=2)
    elif case == 'wrong_digest': args['payload_sha256'] = '0' * 64
    elif case == 'tampered_bytes': (root / version['path']).write_bytes(b'<changed/>')
    before = db_rows(root)
    with pytest.raises(api.GovernanceError): service.publish(oid, **args)
    assert db_rows(root) == before
    assert assets.get_version(oid, version['version_id'])['publication_state'] == 'candidate'


def test_review_requires_reason_and_cannot_be_reused_after_new_save(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    version = service.save(oid, **save_args(base))
    before = db_rows(root)
    with pytest.raises(api.GovernanceError):
        service.review(oid, version['version_id'], payload_sha256=version['payload_sha256'],
            decision='accepted', actor='tester', reason='', request_id='bad-review')
    assert db_rows(root) == before
    first = review(service, oid, version)
    assert first['version_id'] == version['version_id'] and first['payload_sha256'] == version['payload_sha256']
    assert len(first['report_sha256']) == 64
    assert review(service, oid, version) == first
    newer = service.save(oid, **save_args(version, revision=1, request_id='new-save'))
    with pytest.raises(api.GovernanceError): service.publish(oid, **publish_args(newer, first, revision=2))
    assert assets.get_version(oid, newer['version_id'])['publication_state'] == 'candidate'


def test_review_revalidates_structure_even_when_stored_digest_matches_bad_payload(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    version = service.save(oid, **save_args(base))
    changed = Graph().parse(data=assets.read_payload(oid, version['version_id']), format='xml')
    changed.remove((S.grasp_Sequence_1, S.stepIndex, None))
    raw = changed.serialize(format='xml', encoding='utf-8')
    (root / version['path']).write_bytes(raw)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        conn.execute('UPDATE versions SET payload_sha256=?,byte_size=? WHERE version_id=?', (sha(raw),len(raw),version['version_id']))
    corrupted = assets.get_version(oid, version['version_id'])
    before = db_rows(root)
    with pytest.raises(api.GovernanceError): review(service, oid, corrupted)
    assert db_rows(root) == before


def test_structural_publication_retains_gaps_and_strict_generation_is_blocked(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    version = service.save(oid, **save_args(base))
    before = assets.read_payload(oid, version['version_id'])
    reviewed = review(service, oid, version)
    published = service.publish(oid, **publish_args(version, reviewed))
    assert published['publication_state'] == 'published' and published['publication_mode'] == 'real'
    assert published['validation']['execution_readiness'] == 'not_verified'
    assert assets.read_payload(oid, version['version_id']) == before
    rows = db_rows(root)
    assert service.publish(oid, **publish_args(version, reviewed)) == published
    assert db_rows(root) == rows
    run = generate(pipeline(), root, {'ontology_id': oid}, published, deepcopy(TASK), mode='real')
    assert run['result']['status'] == 'needs_information'
    check_saved(root, run, 'needs_information')
    assert not any(a['kind'] == 'candidate' for a in RunStore(root).artifacts(run['run_id']))
    graph = Graph().parse(data=before, format='xml')
    assert not any(str(value) == 'confirmed' for value in graph.objects(None, S.confirmationStatus))


def test_version_artifacts_belong_to_edited_bytes_not_origin_build(tmp_path):
    api = module('ontology_assets.governance')
    from tests.robotics_governance_support import actual_build
    root = tmp_path / 'artifacts'
    base, run = actual_build(root)
    oid = base['ontology_id']
    service = api.GovernanceService(root)
    version = service.save(oid, **save_args(base))
    reviewed = review(service, oid, version)
    service.publish(oid, **publish_args(version, reviewed))
    assets = AssetStore(root)
    records = service.artifacts(oid, version['version_id'])
    assert sorted(a['kind'] for a in records) == ['manifest', 'ontology', 'validation']
    for record in records:
        assert record['version_id'] == version['version_id']
        raw = service.read_artifact(oid, version['version_id'], record['artifact_id'])
        assert sha(raw) == record['sha256'] and len(raw) == record['byte_size']
        if record['kind'] == 'ontology':
            assert raw == assets.read_payload(oid, version['version_id'])
            assert raw != assets.read_payload(oid, base['version_id'])
        else:
            report = json.loads(raw)
            assert report['version_id'] == version['version_id'] and report['payload_sha256'] == version['payload_sha256']
        with pytest.raises((ValueError, KeyError)):
            service.read_artifact(oid, base['version_id'], record['artifact_id'])
    assert RunStore(root).get_run(run['run_id']) == run


def test_publish_artifact_failure_cannot_leave_published_state(tmp_path, monkeypatch):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    version = service.save(oid, **save_args(base))
    reviewed = review(service, oid, version)
    before = db_rows(root)
    attempted = []
    def fail(self, relative, content):
        attempted.append(relative)
        raise OSError('synthetic full disk')
    monkeypatch.setattr(Storage, 'write_bytes', fail)
    with pytest.raises((OSError, api.GovernanceError)): service.publish(oid, **publish_args(version, reviewed))
    assert attempted and db_rows(root) == before
    assert assets.get_version(oid, version['version_id'])['publication_state'] == 'candidate'


def test_evidence_review_is_a_new_version_and_revocation_blocks_platform_generation(tmp_path):
    api = module('ontology_assets.governance')
    # Explicit DEMO positive fixture; this never claims real-world applicability.
    root, assets, asset, base, _ = setup_assets(tmp_path, mode='demo')
    oid = asset['ontology_id']
    service = api.GovernanceService(root)
    version, revision = base, 0
    for index, (kind, implementation, slot) in enumerate([
        ('grasp_compatibility', S.grasp_Subtree_Impl, None),
        ('placement_compatibility', S.Place_Subtree_Impl, S.Slot_B_01),
    ]):
        args = save_args(version, revision=revision, request_id=f'evidence-{index}')
        del args['operations']
        spec = evidence_spec(iri=str(S[f'GovernedEvidence{index}']), kind=kind, implementation=str(implementation),
            slot=str(slot) if slot else None, reference='demo-only:synthetic reviewer input',
            valid_until='2999-01-01T00:00:00+00:00')
        pending = service.register_evidence(oid, evidence=spec, **args)
        pending_bytes = assets.read_payload(oid, pending['version_id'])
        revision += 1
        args = save_args(pending, revision=revision, request_id=f'evidence-review-{index}')
        del args['operations']
        version = service.review_evidence(oid, evidence_iri=spec['iri'], decision='accepted', **args)
        revision += 1
        assert version['parent_version_id'] == pending['version_id']
        assert assets.read_payload(oid, pending['version_id']) == pending_bytes
    reviewed = review(service, oid, version)
    published = service.publish(oid, **publish_args(version, reviewed, revision=revision))
    from tests.robotics_support import FIXTURES
    task = json.loads((FIXTURES / 'demo_supplement.json').read_text())['task']
    first = generate(pipeline(), root, asset, published, task, mode='demo')
    check_saved(root, first, 'generated')
    runs = RunStore(root)
    old_manifest = runs.manifest(first['run_id'])
    old_files = {a['artifact_id']: runs.read_artifact(first['run_id'], a['artifact_id']) for a in runs.artifacts(first['run_id'])}
    published_bytes = assets.read_payload(oid, version['version_id'])
    revoke = dict(evidence_iri=str(S.GovernedEvidence0), version_id=version['version_id'],
                  actor='demo reviewer', reason='synthetic revocation', request_id='revoke-1')
    event = service.revoke_evidence(oid, **revoke)
    assert service.revoke_evidence(oid, **revoke) == event
    assert assets.read_payload(oid, version['version_id']) == published_bytes
    second = generate(pipeline(), root, asset, published, task, mode='demo')
    check_saved(root, second, 'needs_information')
    assert any(i['code'] == 'evidence_revoked' for i in second['result']['issues'])
    assert runs.manifest(first['run_id']) == old_manifest
    for aid, raw in old_files.items(): assert runs.read_artifact(first['run_id'], aid) == raw


def test_simultaneous_identical_requests_create_only_one_version(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    api.GovernanceService(root)
    with ThreadPoolExecutor(max_workers=2) as pool:
        versions = list(pool.map(lambda _: api.GovernanceService(root).save(oid, **save_args(base)), range(2)))
    assert versions[0] == versions[1]
    assert len(assets.list_versions(oid)) == 2
    assert len(api.GovernanceService(root).history(oid)) == 1


def test_read_only_governance_denies_mutation_and_retains_head(tmp_path):
    api = module('ontology_assets.governance')
    root, _, oid, base = setup(tmp_path)
    api.GovernanceService(root)
    service = api.GovernanceService(root, read_only=True)
    before, files = db_rows(root), blob_bytes(root)
    with pytest.raises((ValueError, PermissionError)): service.save(oid, **save_args(base))
    assert db_rows(root) == before and blob_bytes(root) == files
    assert service.head(oid)['version_id'] == base['version_id']


def test_explicit_knowledge_review_only_confirms_selected_objects_in_new_candidate(tmp_path):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    edited = service.save(oid, **save_args(base))
    before = assets.read_payload(oid, edited['version_id'])
    args = save_args(edited, revision=1, request_id='knowledge-review')
    del args['operations']
    confirmed = service.review_knowledge(oid, entity_iris=[str(S.PartType_A)], decision='accepted', **args)
    assert confirmed['parent_version_id'] == edited['version_id']
    assert confirmed['publication_state'] == 'candidate'
    g = Graph().parse(data=assets.read_payload(oid, confirmed['version_id']), format='xml')
    assert str(g.value(S.PartType_A, S.confirmationStatus)) == 'confirmed'
    assert str(g.value(S.Station_B, S.confirmationStatus)) == 'illustrative'
    assert not list(g.subjects(RDF.type, S.ReadinessEvidence))
    assert assets.read_payload(oid, edited['version_id']) == before
    assert service.head(oid) == {'version_id': confirmed['version_id'], 'revision': 2}


def test_corrupt_version_artifact_is_rejected_without_success_bytes(tmp_path):
    api = module('ontology_assets.governance')
    root, _, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    version = service.save(oid, **save_args(base))
    reviewed = review(service, oid, version)
    service.publish(oid, **publish_args(version, reviewed))
    item = next(r for r in service.artifacts(oid, version['version_id']) if r['kind'] == 'ontology')
    (root / item['path']).write_bytes(b'corrupted')
    with pytest.raises(ValueError): service.read_artifact(oid, version['version_id'], item['artifact_id'])


@pytest.mark.parametrize('case', ['registered', 'missing_id', 'server_path', 'damaged_file'])
def test_evidence_attachment_uses_registered_bytes_and_review_rechecks_integrity(tmp_path, case):
    api = module('ontology_assets.governance')
    root, assets, oid, base = setup(tmp_path)
    service = api.GovernanceService(root)
    proof = assets.put_source(b'SYNTHETIC evidence, not real measurement', role='evidence', media_type='text/plain')
    args = save_args(base, request_id='register-proof')
    del args['operations']
    sid = 'src_missing' if case == 'missing_id' else '/etc/passwd' if case == 'server_path' else proof['source_asset_id']
    spec = evidence_spec(source_asset_ids=[sid], valid_until='2999-01-01T00:00:00+00:00')
    before = db_rows(root)
    if case in ('missing_id', 'server_path'):
        with pytest.raises((api.GovernanceError, KeyError)): service.register_evidence(oid, evidence=spec, **args)
        assert db_rows(root) == before
        return
    pending = service.register_evidence(oid, evidence=spec, **args)
    assert proof['source_asset_id'] in pending['source_asset_ids']
    pending_bytes = assets.read_payload(oid, pending['version_id'])
    if case == 'damaged_file': (root / proof['path']).write_bytes(b'tampered')
    review_args = save_args(pending, revision=1, request_id='review-proof')
    del review_args['operations']
    before = db_rows(root)
    if case == 'damaged_file':
        with pytest.raises(api.GovernanceError):
            service.review_evidence(oid, evidence_iri=spec['iri'], decision='accepted', **review_args)
        assert db_rows(root) == before
    else:
        approved = service.review_evidence(oid, evidence_iri=spec['iri'], decision='accepted', **review_args)
        assert approved['version_id'] != pending['version_id']
        assert assets.read_source(proof['source_asset_id']) == b'SYNTHETIC evidence, not real measurement'
    assert assets.read_payload(oid, pending['version_id']) == pending_bytes

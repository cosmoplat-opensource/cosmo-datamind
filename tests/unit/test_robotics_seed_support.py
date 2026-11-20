"""Test seed reuse must preserve independent storage and real consumer validation."""
from importlib import import_module
import sqlite3

import pytest
from rdflib import Graph, RDF, URIRef

from ontology_assets import AssetStore
from tests.build_pipeline_support import blob_bytes, db_rows


def support():
    return import_module('tests.robotics_seed_support')


def test_design_browser_seed_reuses_preconditions_without_sharing_mutations(tmp_path):
    from tests.robotics_design_browser_support import DesignSite
    from tests.robotics_design_support import Harness
    cache = support().SeedCache()
    builds = []
    snapshots = []

    def prepare(root):
        builds.append(1)
        state = Harness(root).candidate()
        snapshots.append((db_rows(root), blob_bytes(root)))
        return state

    first = DesignSite(tmp_path / 'first', tmp_path / 'first-output', prepare=prepare, prepare_cache=cache)
    try:
        original = first.prepared
        changed = first.post_json('/api/robotics/design-sessions/' + original['session_id'] + '/edit-classes',
            {'expected_revision': original['revision'], 'request_id': 'isolated-edit'})[1]
        assert changed['revision'] > original['revision'] and changed['stage'] == 'classes'
        first.prepared['result'] = {'changed': True}
    finally:
        first.close()
    second = DesignSite(tmp_path / 'second', tmp_path / 'second-output', prepare=prepare, prepare_cache=cache)
    try:
        assert builds == [1]
        # Server startup also imports its own demo assets; compare every original seed row/file.
        rows, files = snapshots[0]
        restored_rows, restored_files = db_rows(second.root), blob_bytes(second.root)
        for table, entries in rows.items():
            assert all(row in restored_rows[table] for row in entries), table
        assert all(restored_files[name] == raw for name, raw in files.items())
        assert second.prepared['result'] != {'changed': True}
        original = second.prepared
        assert second.get('/api/robotics/design-sessions/' + original['session_id'])['revision'] == original['revision']
        # Real operations still run in each restored storage; stale revisions must fail.
        changed = second.post_json('/api/robotics/design-sessions/' + original['session_id'] + '/edit-classes',
            {'expected_revision': original['revision'], 'request_id': 'another-isolated-edit'})[1]
        assert changed['stage'] == 'classes' and changed['revision'] > original['revision']
        from urllib.error import HTTPError
        with pytest.raises(HTTPError) as caught:
            second.post_json('/api/robotics/design-sessions/' + original['session_id'] + '/edit-classes',
                {'expected_revision': original['revision'], 'request_id': 'stale-edit'})
        assert caught.value.code == 409
    finally:
        second.close()


def test_seed_clones_database_files_and_detaches_return_values(tmp_path):
    cache = support().SeedCache()
    calls = []

    def build(root):
        calls.append(root)
        root.mkdir()
        with sqlite3.connect(root / 'domain.sqlite3') as conn:
            conn.execute('CREATE TABLE sample(value TEXT)')
            conn.execute("INSERT INTO sample VALUES ('initial')")
        (root / 'payload').write_bytes(b'initial')
        return {'nested': ['initial']}

    first, second = tmp_path / 'first', tmp_path / 'second'
    one = cache.restore('seed', first, build)
    one['nested'].clear()
    (first / 'payload').write_bytes(b'changed')
    with sqlite3.connect(first / 'domain.sqlite3') as conn:
        conn.execute("UPDATE sample SET value='changed'")
    two = cache.restore('seed', second, build)
    assert len(calls) == 1
    assert two == {'nested': ['initial']}
    assert (second / 'payload').read_bytes() == b'initial'
    assert (first / 'payload').stat().st_ino != (second / 'payload').stat().st_ino
    with sqlite3.connect(second / 'domain.sqlite3') as conn:
        assert conn.execute('SELECT value FROM sample').fetchone() == ('initial',)


def test_seed_failure_is_not_cached_and_existing_directory_is_not_overwritten(tmp_path):
    cache = support().SeedCache()
    attempts = []

    def fail(root):
        attempts.append(1)
        root.mkdir()
        (root / 'partial').write_bytes(b'partial')
        raise ValueError('synthetic build failure')

    for _ in range(2):
        with pytest.raises(ValueError, match='synthetic build failure'):
            cache.restore('failed', tmp_path / 'destination', fail)
    assert len(attempts) == 2
    assert not (tmp_path / 'destination').exists()
    existing = tmp_path / 'existing'; existing.mkdir()
    (existing / 'keep').write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        cache.restore('existing', existing, fail)
    assert (existing / 'keep').read_bytes() == b'keep'
    assert len(attempts) == 2


def test_task_fixture_reuses_real_build_only_for_preconditions(tmp_path, monkeypatch):
    module = support()
    monkeypatch.setattr(module, '_BUILD_SEEDS', module.SeedCache())
    original = module.actual_build
    builds = []

    def observed(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'actual_build', observed)
    from tests.robotics_task_support import fixture
    a, version, run = fixture(tmp_path / 'a')
    b, other, saved = fixture(tmp_path / 'b')
    assert len(builds) == 1
    assert version['ontology_id'] != other['ontology_id']
    assert version['version_id'] != other['version_id']
    assert run['run_id'] != saved['run_id']
    assert run['result'] == saved['result'] and run['result']['status'] == 'built'
    assert a.read_payload(version['ontology_id'], version['version_id']) == b.read_payload(other['ontology_id'], other['version_id'])
    before = db_rows(b.storage.root), blob_bytes(b.storage.root)
    (a.storage.root / version['path']).write_bytes(b'changed')
    assert before == (db_rows(b.storage.root), blob_bytes(b.storage.root))
    # Direct build verification remains uncached and executes actual production computation.
    module.actual_build(tmp_path / 'direct')
    assert len(builds) == 2


def test_warm_seed_keeps_full_semantic_validation_and_rejects_corruption(tmp_path, monkeypatch):
    from tests.robotics_task_support import fixture
    fixture(tmp_path / 'warm')
    from domains.robotics import consumer
    checks = []
    original = consumer.graph_validation

    def observed(graph):
        checks.append(1)
        return original(graph)

    monkeypatch.setattr(consumer, 'graph_validation', observed)
    assets, version, _ = fixture(tmp_path / 'test')
    assert len(checks) == 1, 'saving a reused build must revalidate its actual bytes'
    for _ in range(2):
        result = assets.load_robotics_application(version['ontology_id'], version['version_id'])
        assert result['validation']['shacl_conforms'] is True
    assert len(checks) == 3
    graph = Graph().parse(data=assets.read_payload(version['ontology_id'], version['version_id']), format='xml')
    # Valid XML with invalid ontology semantics must fail the unchanged consumer gate.
    graph.remove((None, RDF.type, URIRef('https://example.org/ur-skill#SkillInvocation')))
    damaged = assets.add_version(version['ontology_id'], graph.serialize(format='xml', encoding='utf-8'),
                                 payload_kind='robotics_rdf')
    with pytest.raises(ValueError):
        assets.load_robotics_application(damaged['ontology_id'], damaged['version_id'])
    assert len(checks) == 4


def test_ready_mutations_are_isolated_and_existing_roots_keep_real_build(tmp_path, monkeypatch):
    module = support()
    from tests.robotics_task_support import fixture
    from tests.robotics_support import S
    first, version, _ = fixture(tmp_path / 'one', ready=True,
                                mutate=lambda graph: graph.remove((S.Part_A_001, None, None)))
    other, baseline, _ = fixture(tmp_path / 'two', ready=True)
    one = Graph().parse(data=first.read_payload(version['ontology_id'], version['version_id']), format='xml')
    two = Graph().parse(data=other.read_payload(baseline['ontology_id'], baseline['version_id']), format='xml')
    assert not list(one.triples((S.Part_A_001, None, None)))
    assert list(two.triples((S.Part_A_001, None, None)))
    root = tmp_path / 'existing'
    before = AssetStore(root).create_asset(name='preserve', domain='data')
    original = module.actual_build; builds = []

    def observed(*args, **kwargs):
        builds.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'actual_build', observed)
    fixture(root)
    assert builds == [1]
    assert AssetStore(root).get_asset(before['ontology_id']) == before

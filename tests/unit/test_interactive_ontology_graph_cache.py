"""Fixed-version graph revalidation and streamed provenance checks — DR-191."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from tests.build_pipeline_support import blob_bytes, db_rows
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_task_support import fixture


def graph_path(version, query=''):
    return (f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}"
            f"/views/interactive-ontology-graph{query}")


def get(client, path, etag=None):
    return client.get(path, base_url=BASE_URL, headers={'If-None-Match': etag} if etag else {})


def forbidden(*_args, **_kwargs):
    raise AssertionError('revalidation must not read registered files or project the graph')


def test_graph_view_sends_validator_and_revalidates_without_reading_files(tmp_path, monkeypatch):
    from ontology_assets import interactive_ontology_graph_view, storage
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        path = graph_path(version)
        first = get(client, path)
        assert first.status_code == 200, first.json
        etag = first.headers['ETag']
        assert etag.startswith('"') and etag.endswith('"') and len(etag) == 66
        assert first.headers['Cache-Control'] == 'private, no-cache'
        before = db_rows(root), blob_bytes(root)
        with monkeypatch.context() as patch:
            patch.setattr(storage.Storage, 'read_bytes', forbidden)
            patch.setattr(storage.Storage, 'verify_file', forbidden)
            patch.setattr(interactive_ontology_graph_view, 'project_version', forbidden)
            again = get(client, path, etag)
            assert again.status_code == 304 and again.get_data() == b''
            assert again.headers['ETag'] == etag and again.headers['Cache-Control'] == 'private, no-cache'
            listed = get(client, path, 'W/"unrelated", ' + etag)
            assert listed.status_code == 304
        stale = get(client, path, '"' + '0' * 64 + '"')
        assert stale.status_code == 200 and stale.headers['ETag'] == etag and stale.json == first.json
        assert (db_rows(root), blob_bytes(root)) == before


def test_graph_validator_follows_registered_identity_and_projection_code(tmp_path, monkeypatch):
    from ontology_assets import interactive_ontology_graph_view
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        path = graph_path(version)
        original = get(client, path).headers['ETag']
        with sqlite3.connect(root / 'domain.sqlite3') as conn:
            conn.execute('UPDATE assets SET name=? WHERE ontology_id=?', ('renamed fixture', version['ontology_id']))
        renamed = get(client, path, original)
        assert renamed.status_code == 200 and renamed.json['name'] == 'renamed fixture'
        assert renamed.headers['ETag'] != original
        with monkeypatch.context() as patch:
            patch.setattr(interactive_ontology_graph_view, 'PROJECTION_DIGEST', 'f' * 64)
            changed = get(client, path, renamed.headers['ETag'])
            assert changed.status_code == 200 and changed.headers['ETag'] != renamed.headers['ETag']


def test_revalidation_keeps_domain_version_and_recycle_guards(tmp_path):
    from ontology_assets.lifecycle import LifecycleService
    from tests.unit.test_asset_lifecycle import apply
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        path = graph_path(version)
        etag = get(client, path).headers['ETag']
        assert get(client, path.replace(version['version_id'], 'missing'), etag).status_code == 404
        other = assets.create_asset(name='other', domain='robotics')
        assert get(client, path.replace(version['ontology_id'], other['ontology_id']), etag).status_code == 404
        apply(LifecycleService(root), version['ontology_id'])
        deleted = get(client, path, etag)
        assert deleted.status_code == 409 and deleted.json['error']['code'] == 'ontology_deleted'
        history = get(client, graph_path(version, '?state=deleted'), etag)
        assert history.status_code == 200 and history.headers['ETag'] != etag
        assert get(client, graph_path(version, '?state=deleted'), history.headers['ETag']).status_code == 304


def test_graph_view_streams_source_and_artifact_verification(tmp_path, monkeypatch):
    from ontology_assets import storage
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        sources = {assets.get_source(sid)['path'] for sid in version['source_asset_ids']}
        assert sources
        whole = []
        original = storage.Storage.read_bytes

        def recorded(self, relative, expected):
            whole.append(relative)
            return original(self, relative, expected)
        with monkeypatch.context() as patch:
            patch.setattr(storage.Storage, 'read_bytes', recorded)
            response = get(client, graph_path(version))
        assert response.status_code == 200, response.json
        assert version['path'] in whole, 'the RDF payload itself is parsed'
        assert not sources & set(whole), 'registered sources are hashed in chunks, not loaded whole'
        target = root / sorted(sources)[0]
        content = bytearray(target.read_bytes())
        content[len(content) // 2] ^= 1
        target.write_bytes(bytes(content))
        tampered = get(client, graph_path(version))
        assert tampered.status_code == 409 and tampered.json['error']['code'] == 'artifact_integrity'
        target.write_bytes(bytes(content[:-1]))
        assert get(client, graph_path(version)).status_code == 409


def test_verify_file_hashes_in_bounded_chunks(tmp_path, monkeypatch):
    from ontology_assets.storage import Storage
    store = Storage(tmp_path / 'store')
    content = bytes(range(256)) * 41
    sha = store.write_bytes('blobs/sample.bin', content)
    reads = []
    real_open = Path.open

    def tracking_open(self, *args, **kwargs):
        stream = real_open(self, *args, **kwargs)
        read = stream.read

        def bounded(size=-1):
            assert 0 < size <= 7, 'verification must read in bounded chunks'
            data = read(size)
            reads.append(len(data))
            return data
        stream.read = bounded
        return stream
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', tracking_open)
        assert store.verify_file('blobs/sample.bin', sha, len(content), chunk_size=7) == len(content)
    assert sum(reads) == len(content) and max(reads) <= 7
    with pytest.raises(ValueError):
        store.verify_file('blobs/sample.bin', hashlib.sha256(b'other').hexdigest(), len(content))
    with pytest.raises(ValueError):
        store.verify_file('blobs/sample.bin', sha, len(content) + 1)
    with pytest.raises(ValueError):
        store.verify_file('../outside.bin', sha, len(content))

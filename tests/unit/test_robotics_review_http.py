"""T04 RED: knowledge HTTP, exact-version review and publication — DR-059 / DR-061 / DR-063 / DR-064 / DR-065."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from rdflib import Graph, RDFS

from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService
from ontology_assets.storage import Storage
from tests.asset_support import add_data, data_bytes
from tests.build_pipeline_support import blob_bytes, db_rows, sha
from tests.build_runtime_support import BASE_URL, ROOT, http_app
from tests.robotics_governance_support import (POLICY, actual_build, evidence_spec, field,
                                               publish_args, restore_v4, review, save_args)
from tests.robotics_review_http_support import (attachment, candidate, edit_args, endpoint, error, get,
                                               ok, post, published, seed, version_endpoint)
from tests.robotics_support import S, SOURCE, MODELS
from tests.unit.test_robotics_build_http import assert_public


WRITE_SUFFIXES = ['drafts', 'merge-previews', 'merges', 'evidence-sources', 'evidence',
                  'evidence-reviews', 'knowledge-reviews', 'evidence-revocations',
                  'versions/ver_missing/reviews', 'versions/ver_missing/publish']


def test_knowledge_projection_is_pinned_read_only_and_exposes_gaps(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        before, files = db_rows(root), blob_bytes(root)
        body = ok(get(client, version_endpoint(oid, base, 'knowledge')))
        assert body['ontology_id'] == oid and body['version_id'] == base['version_id']
        assert body['payload_sha256'] == base['payload_sha256']
        assert body['head'] == {'version_id': base['version_id'], 'revision': 0}
        projection = body['knowledge']
        assert {'Workstation', 'Carrier', 'PartType'} <= set(projection['schema']['editable_types'])
        assert str(S.PartType_A) in {item['iri'] for item in projection['entities']}
        assert any(item['code'] == 'unknown_value' for item in projection['issues'])
        assert body['evidence_policy'] == 'legacy' and body['reviews'] == [] and body['history'] == []
        assert body['task_readiness'] == 'not_assessed' and body['execution_readiness'] == 'not_verified'
        assert_public(body, root)
        assert db_rows(root) == before and blob_bytes(root) == files
        assert assets.get_version(oid, base['version_id']) == base


def test_save_and_replay_preserve_old_version_and_show_new_head(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        original = assets.read_payload(oid, base['version_id'])
        args = save_args(base)
        saved = ok(post(client, endpoint(oid, 'drafts'), args), 201)
        assert saved['parent_version_id'] == base['version_id'] and saved['publication_state'] == 'candidate'
        assert saved['validation'] is None and saved['source_asset_ids'] == base['source_asset_ids']
        rows = db_rows(root)
        assert ok(post(client, endpoint(oid, 'drafts'), args), 201) == saved
        assert db_rows(root) == rows
        reopened = GovernanceService(root, read_only=True)
        assert reopened.head(oid) == {'version_id': saved['version_id'], 'revision': 1}
        old = ok(get(client, version_endpoint(oid, base, 'knowledge')))
        current = ok(get(client, version_endpoint(oid, saved, 'knowledge')))
        assert old['version_id'] == base['version_id'] and old['head'] == current['head']
        assert current['evidence_policy'] == POLICY and len(current['history']) == 1
        assert current['history'][0]['actor'] == args['actor']
        graph = Graph().parse(data=assets.read_payload(oid, saved['version_id']), format='xml')
        assert str(graph.value(S.PartType_A, RDFS.label)) == '人工零件类型'
        assert assets.read_payload(oid, base['version_id']) == original


@pytest.mark.parametrize('case', ['revision', 'digest', 'request_reuse'])
def test_save_conflicts_leave_no_partial_version(tmp_path, case):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        args = save_args(base)
        code = 'head_conflict'
        if case == 'revision': args['expected_revision'] = 99
        elif case == 'digest':
            args['base_payload_sha256'] = '0' * 64
            code = 'integrity_conflict'
        else:
            candidate(root, oid, base)
            args['operations'] = [field(value='different')]
            code = 'request_conflict'
        before, files = db_rows(root), blob_bytes(root)
        error(post(client, endpoint(oid, 'drafts'), args), 409, code)
        assert db_rows(root) == before and blob_bytes(root) == files


def test_two_http_clients_cannot_both_replace_the_same_head(tmp_path):
    with http_app(tmp_path) as (app, _, _, root):
        assets, oid, base = seed(root)
        def submit(number):
            with app.test_client() as client:
                return post(client, endpoint(oid, 'drafts'), save_args(base, request_id=f'editor-{number}'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit, [1, 2]))
        assert sorted(r.status_code for r in responses) == [201, 409]
        error(next(r for r in responses if r.status_code == 409), 409, 'head_conflict')
        assert len(assets.list_versions(oid)) == 2
        assert len(GovernanceService(root).history(oid)) == 1


@pytest.mark.parametrize('case', ['missing_digest', 'extra_path', 'bool_revision', 'string_revision',
                                 'empty_actor', 'empty_reason', 'nonlist_operations'])
def test_request_shape_errors_are_400_without_writes(tmp_path, case):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        args = save_args(base)
        if case == 'missing_digest': args.pop('base_payload_sha256')
        elif case == 'extra_path': args['path'] = str(root / 'private.owl')
        elif case == 'bool_revision': args['expected_revision'] = True
        elif case == 'string_revision': args['expected_revision'] = '0'
        elif case == 'empty_actor': args['actor'] = ''
        elif case == 'empty_reason': args['reason'] = '  '
        elif case == 'nonlist_operations': args['operations'] = {}
        before = db_rows(root)
        response = client.post(endpoint(oid, 'drafts'), data=json.dumps(args), content_type='application/json', base_url=BASE_URL)
        error(response, 400, 'invalid_request')
        assert db_rows(root) == before


@pytest.mark.parametrize('operation', [field(S.Perception_Current, 'threshold', 'NaN'),
    field(S.grasp_Subtree_Impl, 'label', 'forged skill'),
    {'op': 'set_relation', 'iri': str(S.PartType_A), 'relation': 'hasPerceptionProfile',
     'targets': [str(S.DoesNotExist)], 'basis': 'invalid mapping'},
    {**field(), 'basis': ''}])
def test_semantically_invalid_edits_are_422_and_cannot_change_rdf(tmp_path, operation):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        before, files = db_rows(root), blob_bytes(root)
        error(post(client, endpoint(oid, 'drafts'), save_args(base, operations=[operation])), 422, 'domain_validation')
        assert db_rows(root) == before and blob_bytes(root) == files


def test_unknown_parameter_can_be_saved_but_does_not_become_confirmed(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = ok(post(client, endpoint(oid, 'drafts'), save_args(base,
            operations=[field(S.Perception_Current, 'threshold', None)])), 201)
        body = ok(get(client, version_endpoint(oid, saved, 'knowledge')))
        assert any(i['iri'] == str(S.Perception_Current) and i['field'] == 'threshold'
                   for i in body['knowledge']['issues'])
        assert body['task_readiness'] == 'not_assessed' and body['execution_readiness'] == 'not_verified'


def test_semantic_diff_has_fixed_endpoints_and_no_side_effects(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = candidate(root, oid, base)
        before, files = db_rows(root), blob_bytes(root)
        body = ok(get(client, endpoint(oid, 'diff'), query_string={'base': base['version_id'], 'target': saved['version_id']}))
        assert body['base']['payload_sha256'] == base['payload_sha256']
        assert body['target']['payload_sha256'] == saved['payload_sha256']
        labels = [c for c in body['changes'] if c['subject'] == str(S.PartType_A) and c['predicate'] == str(RDFS.label)]
        assert len(labels) == 1 and labels[0]['before'] != labels[0]['after']
        assert db_rows(root) == before and blob_bytes(root) == files


@pytest.mark.parametrize('query', ['base=x', 'base=x&base=y&target=z', 'base=x&target=y&path=/etc/passwd'])
def test_diff_rejects_missing_duplicate_and_unknown_query_fields(tmp_path, query):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, _ = seed(root)
        error(get(client, endpoint(oid, 'diff') + '?' + query), 400, 'invalid_request')


def test_attachment_upload_registers_bytes_without_trusting_the_filename(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, _ = seed(root)
        content = b'synthetic observation only'
        body = ok(client.post(endpoint(oid, 'evidence-sources'), data=attachment(content, '../../evil.txt'), base_url=BASE_URL), 201)
        assert body['role'] == 'evidence' and body['sha256'] == sha(content) and body['byte_size'] == len(content)
        assert assets.read_source(body['source_asset_id']) == content
        assert body['media_type'] == 'text/plain'
        assert not (tmp_path / 'evil.txt').exists()
        assert_public(body, root)
        assert GovernanceService(root).history(oid) == []


@pytest.mark.parametrize('case', ['empty', 'duplicate', 'extra_field', 'url', 'server_path', 'wrong_type', 'limit'])
def test_attachment_bad_inputs_never_register_a_source(tmp_path, case):
    with http_app(tmp_path, DATAMIND_ROBOTICS_FILE_LIMIT_BYTES=32) as (_, client, _, root):
        _, oid, _ = seed(root)
        data = attachment(b'x')
        kwargs = {'data': data}
        status, code = 400, 'invalid_request'
        if case == 'empty': kwargs['data'] = attachment(b'')
        elif case == 'duplicate': data.add('file', (io.BytesIO(b'x'), 'other.txt'))
        elif case == 'extra_field': data.add('role', 'behavior_tree')
        elif case == 'url': kwargs = {'json': {'url': 'http://127.0.0.1/private'}}
        elif case == 'server_path': kwargs = {'json': {'path': str(SOURCE)}}
        elif case == 'wrong_type': kwargs['data'] = {'file': 'not bytes'}
        elif case == 'limit':
            kwargs['data'] = attachment(b'x' * 33)
            status, code = 413, 'file_too_large'
        before, files = db_rows(root), blob_bytes(root)
        error(client.post(endpoint(oid, 'evidence-sources'), base_url=BASE_URL, **kwargs), status, code)
        assert db_rows(root) == before and blob_bytes(root) == files


def test_evidence_registration_review_and_revocation_are_distinct_audited_actions(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        source = assets.put_source(b'synthetic evidence', role='evidence', media_type='text/plain')
        args = edit_args(base, request_id='evidence-register')
        args['evidence'] = evidence_spec(source_asset_ids=[source['source_asset_id']], valid_until='2999-01-01T00:00:00+00:00')
        registered = ok(post(client, endpoint(oid, 'evidence'), args), 201)
        assert source['source_asset_id'] in registered['source_asset_ids']
        pending = ok(get(client, version_endpoint(oid, registered, 'knowledge')))
        assert any(e['evidence_iri'] == str(S.TestEvidence) and not e['valid'] for e in pending['evidence'])
        args = {**edit_args(registered, revision=1, request_id='evidence-review'),
                'evidence_iri': str(S.TestEvidence), 'decision': 'accepted'}
        confirmed = ok(post(client, endpoint(oid, 'evidence-reviews'), args), 201)
        body = ok(get(client, version_endpoint(oid, confirmed, 'knowledge')))
        assert any(e['evidence_iri'] == str(S.TestEvidence) and e['valid'] for e in body['evidence'])
        before = assets.read_payload(oid, confirmed['version_id'])
        args = {'evidence_iri': str(S.TestEvidence), 'version_id': confirmed['version_id'], 'kind': 'revoked',
                'actor': 'test reviewer', 'reason': 'synthetic withdrawal', 'request_id': 'revoke-1'}
        revoked = ok(post(client, endpoint(oid, 'evidence-revocations'), args), 201)
        rows = db_rows(root)
        assert ok(post(client, endpoint(oid, 'evidence-revocations'), args), 201) == revoked
        assert db_rows(root) == rows
        body = ok(get(client, version_endpoint(oid, confirmed, 'knowledge')))
        assert revoked in body['revocations']
        assert any(not e['valid'] for e in body['evidence'])
        assert assets.read_payload(oid, confirmed['version_id']) == before
        assert body['execution_readiness'] == 'not_verified'


@pytest.mark.parametrize('case', ['demo_reference', 'wrong_source_role', 'missing_source', 'wrong_scope'])
def test_evidence_cannot_turn_invalid_or_demo_material_into_real_confirmation(tmp_path, case):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        spec = evidence_spec(valid_until='2999-01-01T00:00:00+00:00')
        status, code = 422, 'domain_validation'
        if case == 'demo_reference': spec['reference'] = 'demo-only'
        elif case == 'wrong_source_role': spec['source_asset_ids'] = [base['source_asset_ids'][0]]
        elif case == 'missing_source':
            spec['source_asset_ids'] = ['src_' + '0' * 32]
            status, code = 404, 'not_found'
        else: spec['part_type'] = str(S.Station_B)
        before = db_rows(root)
        error(post(client, endpoint(oid, 'evidence'), {**edit_args(base), 'evidence': spec}), status, code)
        assert db_rows(root) == before


def test_knowledge_review_only_confirms_the_selected_object_in_a_new_version(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        saved = candidate(root, oid, base)
        old = assets.read_payload(oid, saved['version_id'])
        reviewed = ok(post(client, endpoint(oid, 'knowledge-reviews'), {
            **edit_args(saved, revision=1, request_id='knowledge-review'),
            'entity_iris': [str(S.PartType_A)], 'decision': 'accepted'}), 201)
        body = ok(get(client, version_endpoint(oid, reviewed, 'knowledge')))
        selected = next(e for e in body['knowledge']['entities'] if e['iri'] == str(S.PartType_A))
        assert selected['confirmation_status'] == 'confirmed'
        assert reviewed['parent_version_id'] == saved['version_id'] and reviewed['publication_state'] == 'candidate'
        assert body['evidence'] == [] and body['execution_readiness'] == 'not_verified'
        assert assets.read_payload(oid, saved['version_id']) == old


def test_review_and_publish_bind_exact_bytes_and_return_current_version_artifacts(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        saved = candidate(root, oid, base)
        args = {'payload_sha256': saved['payload_sha256'], 'decision': 'accepted',
                'actor': 'declared reviewer', 'reason': 'structure only', 'request_id': 'review-http'}
        reviewed = ok(post(client, version_endpoint(oid, saved, 'reviews'), args), 201)
        assert reviewed['version_id'] == saved['version_id'] and reviewed['payload_sha256'] == saved['payload_sha256']
        assert reviewed['report']['task_readiness'] == 'not_assessed'
        assert reviewed['report']['execution_readiness'] == 'not_verified'
        rows = db_rows(root)
        assert ok(post(client, version_endpoint(oid, saved, 'reviews'), args), 201) == reviewed
        assert db_rows(root) == rows
        args = publish_args(saved, reviewed)
        args.pop('version_id')
        result = ok(post(client, version_endpoint(oid, saved, 'publish'), args))
        assert result['publication_state'] == 'published'
        rows = db_rows(root)
        assert ok(post(client, version_endpoint(oid, saved, 'publish'), args)) == result
        assert db_rows(root) == rows
        detail = ok(get(client, version_endpoint(oid, saved)))
        assert {a['kind'] for a in detail['artifacts']} == {'ontology', 'validation', 'manifest'}
        assert_public(detail, root)
        contents = {}
        for item in detail['artifacts']:
            assert f'/versions/{saved["version_id"]}/artifacts/' in item['download_url']
            response = get(client, item['download_url'])
            assert response.status_code == 200 and 'attachment;' in response.headers['Content-Disposition']
            assert len(response.data) == item['byte_size'] and sha(response.data) == item['sha256']
            contents[item['kind']] = response.data
        assert contents['ontology'] == assets.read_payload(oid, saved['version_id'])
        assert contents['ontology'] != assets.read_payload(oid, base['version_id'])
        for kind in ('validation', 'manifest'):
            report = json.loads(contents[kind])
            assert report['version_id'] == saved['version_id'] and report['payload_sha256'] == saved['payload_sha256']
            assert report['review_id'] == reviewed['review_id'] and report['execution_readiness'] == 'not_verified'
            assert str(root) not in contents[kind].decode()
        body = ok(get(client, version_endpoint(oid, saved, 'knowledge')))
        assert reviewed in body['reviews']


@pytest.mark.parametrize('case', ['no_review', 'rejected', 'other_version', 'wrong_digest', 'stale_head', 'damaged_payload'])
def test_publish_rejects_invalid_review_or_changed_version_without_artifacts(tmp_path, case):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        saved = candidate(root, oid, base)
        service = GovernanceService(root)
        reviewed = review(service, oid, saved, decision='rejected' if case == 'rejected' else 'accepted')
        args = publish_args(saved, reviewed)
        args.pop('version_id')
        code = 'review_conflict'
        if case == 'no_review': args['review_id'] = 'review_missing'
        elif case == 'other_version': args['review_id'] = review(service, oid, base, request_id='old-review')['review_id']
        elif case == 'wrong_digest':
            args['payload_sha256'] = '0' * 64
            code = 'integrity_conflict'
        elif case == 'stale_head':
            service.save(oid, **save_args(saved, request_id='new-save', revision=1))
            code = 'head_conflict'
        elif case == 'damaged_payload':
            with assets.storage.transaction() as conn:
                row = conn.execute('SELECT path FROM versions WHERE version_id=?', (saved['version_id'],)).fetchone()
            (root / row[0]).write_bytes(b'tampered')
            code = 'integrity_conflict'
        before = db_rows(root)
        error(post(client, version_endpoint(oid, saved, 'publish'), args), 409, code)
        assert db_rows(root) == before and service.artifacts(oid, saved['version_id']) == []


def test_candidate_payload_download_never_reuses_origin_build_owl(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        base, run = actual_build(root, name='original-download')
        oid = base['ontology_id']
        saved = candidate(root, oid, base)
        detail = ok(get(client, version_endpoint(oid, saved)))
        assert detail['artifacts'] == []
        response = get(client, detail['payload_url'])
        assert response.status_code == 200 and sha(response.data) == saved['payload_sha256']
        assert response.data == AssetStore(root).read_payload(oid, saved['version_id'])
        original = ok(get(client, '/api/domain-runs/' + run['run_id']))
        owl = next(a for a in original['artifacts'] if a['kind'] == 'ontology')
        old = get(client, owl['download_url'])
        assert old.status_code == 200 and sha(old.data) == owl['sha256']
        assert old.data != response.data


@pytest.mark.parametrize('damage', ['missing', 'bytes', 'symlink', 'size'])
def test_version_download_refuses_corrupted_registered_file(tmp_path, damage):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved, _ = published(root, oid, base)
        item = next(a for a in GovernanceService(root).artifacts(oid, saved['version_id']) if a['kind'] == 'ontology')
        path = root / item['path']
        original = path.read_bytes()
        if damage == 'missing': path.unlink()
        elif damage == 'bytes': path.write_bytes(b'corrupt')
        elif damage == 'symlink':
            outside = tmp_path / 'external.owl'
            outside.write_bytes(original)
            path.unlink()
            path.symlink_to(outside)
        else:
            item['byte_size'] += 1
            with sqlite3.connect(root / 'domain.sqlite3') as conn:
                conn.execute('UPDATE version_artifacts SET record=? WHERE artifact_id=?', (json.dumps(item), item['artifact_id']))
        response = get(client, version_endpoint(oid, saved, 'artifacts/' + item['artifact_id']))
        error(response, 409, 'artifact_integrity')
        assert 'Content-Disposition' not in response.headers and original not in response.data


def test_wrong_asset_or_version_never_exposes_another_versions_bytes(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        saved, _ = published(root, oid, base)
        other = assets.create_asset(name='other', domain='robotics')['ontology_id']
        item = GovernanceService(root).artifacts(oid, saved['version_id'])[0]
        for url in [version_endpoint(other, saved, 'knowledge'), version_endpoint(other, saved, 'payload'),
                    version_endpoint(other, saved, 'artifacts/' + item['artifact_id']),
                    version_endpoint(oid, base, 'artifacts/' + item['artifact_id'])]:
            error(get(client, url), 404, 'not_found')


@pytest.mark.parametrize('suffix', WRITE_SUFFIXES)
def test_feature_off_denies_each_new_post_without_initializing_storage(tmp_path, suffix):
    with http_app(tmp_path, enabled=False) as (_, client, runtime, root):
        error(post(client, endpoint('ont_missing', suffix), {}), 503, 'feature_disabled')
        assert not root.exists() and runtime.executor is None


def test_feature_off_keeps_knowledge_and_version_downloads_readable(tmp_path):
    with http_app(tmp_path, enabled=False) as (_, client, _, root):
        _, oid, base = seed(root)
        saved, _ = published(root, oid, base)
        before, files = db_rows(root), blob_bytes(root)
        ok(get(client, version_endpoint(oid, saved, 'knowledge')))
        detail = ok(get(client, version_endpoint(oid, saved)))
        assert len(detail['artifacts']) == 3
        assert all(get(client, a['download_url']).status_code == 200 for a in detail['artifacts'])
        assert db_rows(root) == before and blob_bytes(root) == files


@pytest.mark.parametrize('legacy', [False, True])
def test_read_never_creates_missing_storage_or_migrates_schema4(tmp_path, legacy):
    root = tmp_path / 'assets'
    if legacy: restore_v4(root)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with http_app(tmp_path, enabled=False) as (_, client, _, _):
        response = get(client, endpoint('ont_missing', 'versions/ver_missing/knowledge'))
        error(response, 503 if legacy else 404, 'storage_unavailable' if legacy else 'not_found')
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert after == before
    if not legacy: assert not root.exists()


def test_data_assets_are_not_admitted_to_robotics_editor(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        asset, version = add_data(AssetStore(root))
        before = db_rows(root)
        error(post(client, endpoint(asset['ontology_id'], 'drafts'), save_args(version)), 400, 'invalid_request')
        assert db_rows(root) == before


@pytest.mark.parametrize('guard', ['origin', 'host', 'json_array', 'json_null', 'malformed_json', 'request_size'])
def test_application_guards_protect_new_routes(tmp_path, guard):
    with http_app(tmp_path) as (app, client, _, root):
        _, oid, base = seed(root)
        kwargs = {'json': save_args(base), 'base_url': BASE_URL}
        expected = 400
        if guard == 'origin':
            kwargs['headers'] = {'Origin': 'https://hostile.example'}
            expected = 403
        elif guard == 'host': kwargs['base_url'] = 'http://hostile.example'
        elif guard == 'json_array': kwargs['json'] = []
        elif guard == 'json_null': kwargs = {'data': 'null', 'content_type': 'application/json', 'base_url': BASE_URL}
        elif guard == 'malformed_json': kwargs = {'data': '{', 'content_type': 'application/json', 'base_url': BASE_URL}
        else:
            app.config['MAX_CONTENT_LENGTH'] = 32
            expected = 413
        before = db_rows(root)
        response = client.post(endpoint(oid, 'drafts'), **kwargs)
        assert response.status_code == expected
        assert db_rows(root) == before


def test_io_failure_is_sanitized_and_rolls_back_visible_state(tmp_path, monkeypatch):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        before = db_rows(root)
        def fail(*args, **kwargs):
            raise OSError(str(root / 'private-secret-directory'))
        monkeypatch.setattr(Storage, 'write_bytes', fail)
        response = post(client, endpoint(oid, 'drafts'), save_args(base))
        # The service translates an I/O integrity failure into a controlled conflict.
        body = error(response, 409, 'integrity_conflict')
        assert_public(body, root)
        assert 'private-secret-directory' not in response.get_data(as_text=True)
        assert db_rows(root) == before


def test_http_merge_preview_conflicts_explicit_resolution_and_stale_rejection(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        base, _ = actual_build(root, name='merge-base')
        oid = base['ontology_id']
        current = GovernanceService(root).save(oid, **save_args(base,
            operations=[field(S.Perception_Current, 'threshold', '0.71')]))
        tree = ET.fromstring(SOURCE.read_bytes())
        tree.find("BehaviorTree[@ID='main']/Sequence")[4].set('threshold', '0.73')
        incoming, _ = actual_build(root, name='merge-incoming', source=ET.tostring(tree))
        ref = lambda v: {k: v[k] for k in ('ontology_id', 'version_id', 'payload_sha256')}
        inputs = {'base': ref(base), 'current': ref(current), 'incoming': ref(incoming),
                  'expected_head_version_id': current['version_id'], 'expected_revision': 1}
        before, files = db_rows(root), blob_bytes(root)
        preview = ok(post(client, endpoint(oid, 'merge-previews'), inputs))
        assert len(preview['conflicts']) == 1 and preview['conflicts'][0]['kind'] == 'field'
        assert db_rows(root) == before and blob_bytes(root) == files
        args = {'merge_token': preview['merge_token'], 'inputs': inputs, 'decisions': {},
                'request_id': 'merge-http', 'actor': 'test editor', 'reason': 'explicit source rebuild'}
        error(post(client, endpoint(oid, 'merges'), args), 409, 'merge_conflict')
        assert db_rows(root) == before
        args['decisions'] = {preview['conflicts'][0]['id']: {'choice': 'current'}}
        saved = ok(post(client, endpoint(oid, 'merges'), args), 201)
        assert saved['parent_version_id'] == current['version_id']
        assert saved['source_asset_ids'] == incoming['source_asset_ids']
        graph = Graph().parse(data=AssetStore(root).read_payload(oid, saved['version_id']), format='xml')
        assert str(graph.value(S.Perception_Current, S.threshold)) == '0.71'
        rows = db_rows(root)
        assert ok(post(client, endpoint(oid, 'merges'), args), 201) == saved
        assert db_rows(root) == rows
        args['request_id'] = 'stale-merge'
        error(post(client, endpoint(oid, 'merges'), args), 409, 'head_conflict')
        assert db_rows(root) == rows
        for name, content in files.items(): assert (root / name).read_bytes() == content


def test_merge_rejects_rdf_copy_without_actual_source_build_lineage(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        current = candidate(root, oid, base)
        ref = lambda v: {k: v[k] for k in ('ontology_id', 'version_id', 'payload_sha256')}
        inputs = {'base': ref(base), 'current': ref(current), 'incoming': ref(base),
                  'expected_head_version_id': current['version_id'], 'expected_revision': 1}
        before = db_rows(root)
        error(post(client, endpoint(oid, 'merge-previews'), inputs), 409, 'merge_conflict')
        assert db_rows(root) == before


@pytest.mark.parametrize('entrypoint', ['server', 'wsgi'])
def test_real_application_mounts_governance_and_reopens_saved_version(tmp_path, entrypoint):
    root = tmp_path / 'assets'
    _, oid, base = seed(root)
    work = tmp_path / 'app-work'
    work.mkdir()
    (work / 'demo_ir.json').write_bytes(data_bytes())
    env = {k: v for k, v in os.environ.items() if not k.startswith('DATAMIND_')}
    env.update(DATAMIND_WORKDIR=str(work), DATAMIND_DB=str(ROOT / 'examples/demo_metrics.db'),
        DATAMIND_ROBOTICS_ENABLED='1', DATAMIND_DOMAIN_STORAGE_ROOT=str(root),
        DATAMIND_ENGINE_DIR=str(tmp_path / 'no-engine'), DATAMIND_OUTPUTS_DIR=str(tmp_path / 'no-outputs'))
    code = '''import importlib, json, sys
from tests.robotics_governance_support import save_args
from tests.robotics_review_http_support import endpoint, get, ok, post, version_endpoint
entrypoint, oid, base, phase = json.loads(sys.argv[1])
module = importlib.import_module(entrypoint)
app = module.app if entrypoint == 'server' else module.application
try:
    with app.test_client() as client:
        if phase == 'write':
            saved = ok(post(client, endpoint(oid, 'drafts'), save_args(base)), 201)
        else:
            body = ok(get(client, version_endpoint(oid, base, 'knowledge')))
            saved = {'version_id': body['head']['version_id']}
            assert saved['version_id'] != base['version_id'] and len(body['history']) == 1
        for url in ('/api/jobs', '/api/graphs', '/api/build/defaults'):
            assert get(client, url).status_code == 200
        print(json.dumps(saved))
finally:
    app.extensions['robotics'].close()
'''
    results = []
    for phase in ('write', 'read'):
        result = subprocess.run([sys.executable, '-c', code, json.dumps([entrypoint, oid, base, phase])],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        results.append(json.loads(result.stdout.splitlines()[-1]))
    assert results[0]['version_id'] == results[1]['version_id']


def test_business_mapping_http_save_keeps_complete_iri_and_basis(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        operation = {'op': 'set_relation', 'iri': str(S.PartType_A), 'relation': 'hasPerceptionProfile',
                     'targets': [str(S.Perception_Current)], 'basis': '合成工艺卡 TEST-001'}
        saved = ok(post(client, endpoint(oid, 'drafts'), save_args(base, operations=[operation])), 201)
        body = ok(get(client, version_endpoint(oid, saved, 'knowledge')))
        entity = next(e for e in body['knowledge']['entities'] if e['iri'] == str(S.PartType_A))
        assert entity['relations']['hasPerceptionProfile'] == [str(S.Perception_Current)]
        assert any(item['basis'] == operation['basis'] for item in body['knowledge']['provenance'])
        graph = Graph().parse(data=assets.read_payload(oid, saved['version_id']), format='xml')
        assert (S.PartType_A, S.hasPerceptionProfile, S.Perception_Current) in graph
        assert body['evidence'] == []


def test_review_revalidates_actual_structure_despite_matching_database_digest(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, oid, base = seed(root)
        saved = candidate(root, oid, base)
        graph = Graph().parse(data=assets.read_payload(oid, saved['version_id']), format='xml')
        graph.remove((S.grasp_Sequence_1, S.stepIndex, None))
        raw = graph.serialize(format='xml', encoding='utf-8')
        (root / saved['path']).write_bytes(raw)
        with sqlite3.connect(root / 'domain.sqlite3') as conn:
            conn.execute('UPDATE versions SET payload_sha256=?, byte_size=? WHERE version_id=?',
                         (sha(raw), len(raw), saved['version_id']))
        before = db_rows(root)
        error(post(client, version_endpoint(oid, saved, 'reviews'), {'payload_sha256': sha(raw),
            'decision': 'accepted', 'actor': 'test', 'reason': 'must revalidate', 'request_id': 'bad-structure'}),
            422, 'domain_validation')
        assert db_rows(root) == before


def test_http_publication_does_not_bypass_strict_generation_gaps(tmp_path):
    from tests.robotics_governance_support import TASK
    from tests.storage_pipeline_support import check_saved, generate, pipeline
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = candidate(root, oid, base)
        reviewed = review(GovernanceService(root), oid, saved)
        args = publish_args(saved, reviewed)
        args.pop('version_id')
        version = ok(post(client, version_endpoint(oid, saved, 'publish'), args))
        assert version['validation']['execution_readiness'] == 'not_verified'
        run = generate(pipeline(), root, {'ontology_id': oid}, version, TASK, mode='real')
        _, candidates = check_saved(root, run, 'needs_information')
        assert candidates == [] and run['result']['evidence_check']['policy'] == POLICY


@pytest.mark.parametrize('case', ['blank_reason', 'forged_report', 'route_version_override'])
def test_review_body_cannot_supply_trusted_report_or_override_route(tmp_path, case):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        body = {'payload_sha256': base['payload_sha256'], 'decision': 'accepted',
                'actor': 'test', 'reason': 'structure', 'request_id': 'bad-review'}
        if case == 'blank_reason': body['reason'] = ''
        elif case == 'forged_report': body['report'] = {'shacl_conforms': True, 'execution_readiness': 'verified'}
        else: body['version_id'] = 'ver_other'
        before = db_rows(root)
        error(post(client, version_endpoint(oid, base, 'reviews'), body), 400, 'invalid_request')
        assert db_rows(root) == before


@pytest.mark.parametrize('damage', ['bytes', 'symlink'])
def test_candidate_payload_download_checks_integrity_before_sending_bytes(tmp_path, damage):
    with http_app(tmp_path) as (_, client, _, root):
        _, oid, base = seed(root)
        saved = candidate(root, oid, base)
        path = root / saved['path']
        original = path.read_bytes()
        if damage == 'bytes': path.write_bytes(b'tampered')
        else:
            outside = tmp_path / 'external.owl'
            outside.write_bytes(original)
            path.unlink()
            path.symlink_to(outside)
        response = get(client, version_endpoint(oid, saved, 'payload'))
        error(response, 409, 'artifact_integrity')
        assert 'Content-Disposition' not in response.headers and original not in response.data


def test_t03_fixture_control_reopens_real_rdf_and_preserves_source_snapshots(tmp_path):
    # Control only: validates our actual fixture/services, not the absent HTTP functionality.
    assets, oid, base = seed(tmp_path / 'assets')
    root = tmp_path / 'assets'
    original = assets.read_payload(oid, base['version_id'])
    saved = candidate(root, oid, base)
    assert GovernanceService(root, read_only=True).head(oid) == {'version_id': saved['version_id'], 'revision': 1}
    assert assets.read_payload(oid, base['version_id']) == original
    assert sha(assets.read_payload(oid, saved['version_id'])) == saved['payload_sha256']
    assert saved['source_asset_ids'] == base['source_asset_ids']
    assert [assets.read_source(s) for s in saved['source_asset_ids']] == [SOURCE.read_bytes(), MODELS.read_bytes()]

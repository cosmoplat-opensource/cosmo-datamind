"""Knowledge governance HTTP transport and version downloads — DR-059 / DR-061 / DR-063 / DR-064 / DR-065."""
import io
from pathlib import Path
import re
import sqlite3

from flask import Blueprint, jsonify, request, send_file, url_for
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from domain_runs.build_service import BuildRequestError
from ontology_assets.governance import GovernanceError, GovernanceService

IDENTITY = {'request_id', 'actor', 'reason'}
CONDITION = {'base_version_id', 'base_payload_sha256', 'expected_head_version_id', 'expected_revision'} | IDENTITY
MERGE_INPUT = {'base', 'current', 'incoming', 'expected_head_version_id', 'expected_revision'}
REFERENCE = {'ontology_id', 'version_id', 'payload_sha256'}
EVIDENCE = {'iri', 'kind', 'implementation', 'part_type', 'slot', 'reference', 'source_asset_ids', 'valid_until'}


def invalid():
    return BuildRequestError('invalid_request', '请求字段或格式不符合契约。', 400)


def text_field(value, limit=4096, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise invalid()


def string_list(value, limit=128):
    if not isinstance(value, list) or len(value) > limit:
        raise invalid()
    for item in value:
        text_field(item)


def shape(body, fields):
    if not isinstance(body, dict) or set(body) != fields:
        raise invalid()
    for key, value in body.items():
        if key == 'expected_revision':
            if type(value) is not int or value < 0:
                raise invalid()
        elif key.endswith('sha256') or key == 'merge_token':
            if not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', value):
                raise invalid()
        elif key in {'operations', 'entity_iris', 'evidence', 'inputs', 'decisions', 'base', 'current', 'incoming'}:
            continue
        else:
            text_field(value, 256 if key in {'actor', 'request_id'} else 4096)
    return body


def operations(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        raise invalid()
    kinds = {'create_entity': {'entity_type', 'label'}, 'set_field': {'field', 'value'},
             'set_relation': {'relation', 'targets'}, 'delete_entity': set()}
    for op in value:
        if not isinstance(op, dict) or not isinstance(op.get('op'), str) or op['op'] not in kinds:
            raise invalid()
        if set(op) != {'op', 'iri', 'basis'} | kinds[op['op']]:
            raise invalid()
        for key, item in op.items():
            if key == 'targets': string_list(item)
            elif key == 'value' and item is None: continue
            else: text_field(item, empty=key in {'basis', 'value', 'label'})


def edit_body(extra):
    body = shape(request.get_json() if request.is_json else None, CONDITION | extra)
    if 'operations' in body: operations(body['operations'])
    if 'entity_iris' in body: string_list(body['entity_iris'])
    if 'evidence' in body:
        item = body['evidence']
        if not isinstance(item, dict) or set(item) != EVIDENCE:
            raise invalid()
        for key, value in item.items():
            if key == 'slot' and value is None: continue
            if key == 'source_asset_ids': string_list(value, 32)
            else: text_field(value, empty=key == 'reference')
    return body


def merge_inputs(body):
    shape(body, MERGE_INPUT)
    for key in ('base', 'current', 'incoming'):
        shape(body[key], REFERENCE)
    return body


def get_service(runtime, *, write=False):
    if write:
        runtime.writer()  # Feature gate precedes body parsing and storage access.
    if not (runtime.root / 'domain.sqlite3').exists():
        raise KeyError('missing storage')
    try:
        return GovernanceService(runtime.root, read_only=not write)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise BuildRequestError('storage_unavailable', '存储暂不可用，请稍后重试。', 503) from exc


def require_robotics(service, oid):
    if service.assets.get_asset(oid)['domain'] != 'robotics':
        raise invalid()


def checked_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Invalid registered path')
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Linked registered file')


def version_artifacts(runtime, oid, vid):
    service = get_service(runtime)
    return [{**runtime.public(item), 'download_url': url_for('robotics_review.artifact',
             ontology_id=oid, version_id=vid, artifact_id=item['artifact_id'])}
            for item in service.artifacts(oid, vid)]


def init_app(app, runtime):
    bp = Blueprint('robotics_review', __name__)
    prefix = '/api/ontology-assets/<ontology_id>'

    @bp.errorhandler(BuildRequestError)
    def request_error(exc):
        return jsonify(error={'code': exc.code, 'message': str(exc)}), exc.status_code

    @bp.errorhandler(GovernanceError)
    def governance_error(exc):
        if exc.code == 'not_found': return not_found(exc)
        if exc.code == 'invalid_request':
            return request_error(BuildRequestError('domain_validation', '知识、证据或本体结构不符合领域约束。', 422))
        messages = {'head_conflict': '工作头已变化，请保留输入并比较新版本。',
                    'request_conflict': '此请求 ID 已用于其他输入。',
                    'review_conflict': '审核与当前版本或验证结论不一致，请重新审核。',
                    'merge_conflict': '来源基线、合并预览或冲突选择不满足提交条件。',
                    'integrity_conflict': '登记内容缺失、损坏或与固定摘要不一致。'}
        return request_error(BuildRequestError(exc.code, messages.get(exc.code, '治理请求发生冲突。'), 409))

    @bp.errorhandler(KeyError)
    def not_found(exc):
        return request_error(BuildRequestError('not_found', '指定资产、版本或记录不存在。', 404))

    @bp.errorhandler(OSError)
    @bp.errorhandler(sqlite3.Error)
    def unavailable(exc):
        return request_error(BuildRequestError('storage_unavailable', '存储暂不可用，请稍后重试。', 503))

    @bp.errorhandler(BadRequest)
    def malformed(exc):
        return request_error(invalid())

    @bp.errorhandler(RequestEntityTooLarge)
    def too_large(exc):
        return request_error(BuildRequestError('file_too_large', '上传请求超过大小限制。', 413))

    def service(oid, *, write=False):
        result = get_service(runtime, write=write)
        require_robotics(result, oid)
        return result

    def response(value, status=200):
        return jsonify(runtime.public(value)), status

    def body(fields):
        return shape(request.get_json() if request.is_json else None, fields)

    @bp.get(prefix + '/versions/<version_id>/knowledge')
    def knowledge(ontology_id, version_id):
        if request.args: raise invalid()
        return response(service(ontology_id).inspect(ontology_id, version_id))

    @bp.post(prefix + '/drafts')
    def save(ontology_id):
        store = service(ontology_id, write=True)
        return response(store.save(ontology_id, **edit_body({'operations'})), 201)

    @bp.get(prefix + '/diff')
    def diff(ontology_id):
        if set(request.args) != {'base', 'target'} or any(len(request.args.getlist(k)) != 1 for k in request.args):
            raise invalid()
        for value in request.args.values(): text_field(value)
        return response(service(ontology_id).diff(ontology_id, request.args['base'], request.args['target']))

    @bp.post(prefix + '/evidence-sources')
    def upload(ontology_id):
        store = service(ontology_id, write=True)
        if (request.mimetype != 'multipart/form-data' or request.form or set(request.files) != {'file'}
                or len(request.files.getlist('file')) != 1):
            raise invalid()
        uploaded = request.files['file']
        content = uploaded.stream.read(runtime.limits['file_bytes'] + 1)
        if not content: raise invalid()
        if len(content) > runtime.limits['file_bytes']:
            raise BuildRequestError('file_too_large', '附件超过单文件大小限制。', 413)
        return response(store.register_source(ontology_id, content, uploaded.mimetype or 'application/octet-stream'), 201)

    @bp.post(prefix + '/evidence')
    def register_evidence(ontology_id):
        store = service(ontology_id, write=True)
        return response(store.register_evidence(ontology_id, **edit_body({'evidence'})), 201)

    @bp.post(prefix + '/evidence-reviews')
    def review_evidence(ontology_id):
        store = service(ontology_id, write=True)
        return response(store.review_evidence(ontology_id, **edit_body({'evidence_iri', 'decision'})), 201)

    @bp.post(prefix + '/knowledge-reviews')
    def review_knowledge(ontology_id):
        store = service(ontology_id, write=True)
        return response(store.review_knowledge(ontology_id, **edit_body({'entity_iris', 'decision'})), 201)

    @bp.post(prefix + '/evidence-revocations')
    def revoke(ontology_id):
        store = service(ontology_id, write=True)
        return response(store.revoke_evidence(ontology_id,
            **body(IDENTITY | {'evidence_iri', 'version_id', 'kind'})), 201)

    @bp.post(prefix + '/versions/<version_id>/reviews')
    def review(ontology_id, version_id):
        store = service(ontology_id, write=True)
        return response(store.review(ontology_id, version_id, **body(IDENTITY | {'payload_sha256', 'decision'})), 201)

    @bp.post(prefix + '/versions/<version_id>/publish')
    def publish(ontology_id, version_id):
        store = service(ontology_id, write=True)
        return response(store.publish(ontology_id, version_id=version_id,
            **body(IDENTITY | {'payload_sha256', 'review_id', 'expected_head_version_id', 'expected_revision'})))

    @bp.post(prefix + '/merge-previews')
    def preview(ontology_id):
        runtime.writer()  # A POST even though preview is read-only.
        inputs = merge_inputs(body(MERGE_INPUT))
        return response(service(ontology_id).preview_merge(ontology_id, **inputs))

    @bp.post(prefix + '/merges')
    def merge(ontology_id):
        store = service(ontology_id, write=True)
        args = body(IDENTITY | {'merge_token', 'inputs', 'decisions'})
        merge_inputs(args['inputs'])
        if not isinstance(args['decisions'], dict) or len(args['decisions']) > 256: raise invalid()
        for key, decision in args['decisions'].items():
            text_field(key)
            if not isinstance(decision, dict) or set(decision) not in ({'choice'}, {'choice', 'value'}):
                raise invalid()
            text_field(decision['choice'])
        return response(store.commit_merge(ontology_id, **args), 201)

    def download(ontology_id, version_id, artifact_id=None):
        store = service(ontology_id)
        version = store.assets.get_version(ontology_id, version_id)
        item = version
        if artifact_id is not None:
            records = [a for a in store.artifacts(ontology_id, version_id) if a['artifact_id'] == artifact_id]
            if not records: raise KeyError(artifact_id)
            item = records[0]
        try:
            checked_path(runtime.root, item['path'])
            content = (store.read_payload(ontology_id, version_id) if artifact_id is None else
                       store.read_artifact(ontology_id, version_id, artifact_id))
            if len(content) != item['byte_size']: raise ValueError('Size mismatch')
        except (ValueError, OSError) as exc:
            raise BuildRequestError('artifact_integrity', '登记文件缺失、损坏或路径不可信。', 409) from exc
        kind = item.get('kind', 'ontology')
        return send_file(io.BytesIO(content), mimetype=item.get('media_type', 'application/rdf+xml'),
                         as_attachment=True, download_name=version_id + '-' + kind + ('.owl' if kind == 'ontology' else '.json'),
                         max_age=0)

    @bp.get(prefix + '/versions/<version_id>/payload')
    def payload(ontology_id, version_id):
        return download(ontology_id, version_id)

    @bp.get(prefix + '/versions/<version_id>/artifacts/<artifact_id>')
    def artifact(ontology_id, version_id, artifact_id):
        return download(ontology_id, version_id, artifact_id)

    app.register_blueprint(bp)

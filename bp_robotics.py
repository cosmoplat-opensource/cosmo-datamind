"""Robot build HTTP composition, catalog and registered downloads — DR-060 / DR-063 / DR-064."""
import atexit
import io
import math
import os
from pathlib import Path
import sqlite3

from flask import Blueprint, jsonify, request, send_file, url_for
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from domain_runs import RunStore
from domain_runs.build_service import BuildRequestError, BuildService, ROLES
from domain_runs.executor import BuildExecutor
from domain_runs.store import _decode
from ontology_assets import AssetStore
import bp_robotics_review


class Runtime:
    def __init__(self, root, enabled, limits, *, start_executor):
        self.root, self.enabled, self.limits = root, enabled, limits
        self.service = self.executor = None
        if enabled:
            self.service = BuildService(root, max_pending=limits['pending'], file_limit_bytes=limits['file_bytes'])
            if start_executor:
                self.executor = BuildExecutor(root, timeout_seconds=limits['timeout_seconds'])
                self.executor.start()
                atexit.register(self.close)

    def close(self):
        if self.executor is not None:
            self.executor.stop()
        atexit.unregister(self.close)

    def writer(self):
        if not self.enabled:
            raise BuildRequestError('feature_disabled', '机器人构建功能未启用。', 503)
        return self.service

    def runs(self):
        if not (self.root / 'domain.sqlite3').exists():
            raise KeyError('尚无运行记录。')
        return self._reader(RunStore)

    def assets(self):
        return self._reader(AssetStore)

    def _reader(self, factory):
        try:
            return factory(self.root, read_only=True)
        except ValueError as exc:
            raise BuildRequestError('storage_unavailable', '存储格式暂不可读；读取请求不会执行迁移。', 503) from exc

    def public(self, value):
        if isinstance(value, dict):
            return {k: self.public(v) for k, v in value.items()
                    if k not in {'path', 'storage_root', 'database_path', 'command'}}
        if isinstance(value, list):
            return [self.public(v) for v in value]
        if isinstance(value, str):
            return value.replace(str(self.root), '[存储目录]')
        return value


def _config(app, workdir):
    def setting(key, default):
        return app.config[key] if key in app.config else os.environ.get(key, default)

    raw = setting('DATAMIND_ROBOTICS_ENABLED', False)
    if isinstance(raw, bool):
        enabled = raw
    elif str(raw).lower() in ('1', 'true', 'yes', 'on', '0', 'false', 'no', 'off'):
        enabled = str(raw).lower() in ('1', 'true', 'yes', 'on')
    else:
        raise ValueError('DATAMIND_ROBOTICS_ENABLED 必须为明确的布尔值。')
    root = Path(setting('DATAMIND_DOMAIN_STORAGE_ROOT', None) or Path(workdir) / 'domain_assets')
    if not root.is_absolute():
        raise ValueError('DATAMIND_DOMAIN_STORAGE_ROOT 必须是绝对路径。')

    def positive_int(key, default, maximum=None):
        value = setting(key, default)
        if isinstance(value, bool) or not str(value).isdigit():
            raise ValueError(f'{key} 必须为正整数。')
        number = int(value)
        if number < 1 or (maximum is not None and number > maximum):
            raise ValueError(f'{key} 超出范围。')
        return number

    file_bytes = positive_int('DATAMIND_ROBOTICS_FILE_LIMIT_BYTES', 10 * 1024 * 1024)
    pending = positive_int('DATAMIND_ROBOTICS_MAX_PENDING', 4, 64)
    budget = setting('DATAMIND_ROBOTICS_TIMEOUT_SECONDS', 300)
    if isinstance(budget, bool):
        raise ValueError('构建超时不能为布尔值。')
    budget = float(budget)
    if not math.isfinite(budget) or not 0 < budget <= 3600:
        raise ValueError('构建超时必须为有限正数且不超过 3600 秒。')
    return root.resolve(), enabled, {'file_bytes': file_bytes, 'pending': pending, 'timeout_seconds': budget}


def _invalid(message='请求字段或格式不符合契约。'):
    return BuildRequestError('invalid_request', message)


def _integer_arg(name, default, low, high=None):
    raw = request.args.get(name, str(default))
    if not raw.isascii() or not raw.isdigit() or len(raw) > 10:
        raise _invalid()
    value = int(raw)
    if value < low or (high is not None and value > high):
        raise _invalid()
    return value


def _query_fields(allowed):
    if set(request.args) - allowed or any(len(request.args.getlist(k)) != 1 for k in request.args):
        raise _invalid()


def init_app(app, *, workdir, legacy_catalog, start_executor=True):
    """No server import: the host injects legacy enumeration and owns runtime shutdown."""
    if 'robotics' in app.extensions:
        return app.extensions['robotics']
    root, enabled, limits = _config(app, workdir)
    runtime = Runtime(root, enabled, limits, start_executor=start_executor)
    bp = Blueprint('robotics', __name__)

    @bp.errorhandler(BuildRequestError)
    def request_error(exc):
        return jsonify(error={'code': exc.code, 'message': str(exc)}), exc.status_code

    @bp.errorhandler(KeyError)
    def not_found(exc):
        return jsonify(error={'code': 'not_found', 'message': '指定的资产、版本、运行或工件不存在。'}), 404

    @bp.errorhandler(OSError)
    @bp.errorhandler(sqlite3.Error)
    def storage_error(exc):
        return jsonify(error={'code': 'storage_unavailable', 'message': '存储暂不可用，请稍后重试。'}), 503

    @bp.errorhandler(BadRequest)
    def malformed(exc):
        return request_error(_invalid())

    @bp.errorhandler(RequestEntityTooLarge)
    def too_large(exc):
        return request_error(BuildRequestError('file_too_large', '上传请求超过大小限制。', 413))

    @bp.get('/api/robotics/capabilities')
    def capabilities():
        return jsonify(enabled=enabled, recipes=[{'id': 'bt_xml', 'profiles': ['ur_pick_place'],
                        'input_roles': list(ROLES)}], limits=limits)

    @bp.post('/api/robotics/builds')
    def submit():
        service = runtime.writer()
        fields = {'name', 'recipe', 'profile', 'request_id'}
        if (request.mimetype != 'multipart/form-data' or set(request.form) != fields
                or set(request.files) != set(ROLES)
                or any(len(request.form.getlist(k)) != 1 for k in fields)
                or any(len(request.files.getlist(k)) != 1 for k in ROLES)):
            raise _invalid()
        sources = {role: {'filename': request.files[role].filename,
                         'content': request.files[role].stream.read(limits['file_bytes'] + 1)} for role in ROLES}
        row = service.submit(name=request.form['name'], recipe=request.form['recipe'],
            profile_id=request.form['profile'], request_id=request.form['request_id'], sources=sources)
        return jsonify(runtime.public(row)), 202

    def run_row(runs, run_id):
        row = runs.get_run(run_id)
        if row['operation'] != 'build_ontology':
            raise KeyError(run_id)
        return row

    def artifact_items(runs, run_id):
        return [{**runtime.public(a), 'download_url': url_for('robotics.download', run_id=run_id,
                  artifact_id=a['artifact_id'])} for a in runs.artifacts(run_id)]

    @bp.get('/api/domain-runs')
    def history():
        _query_fields({'operation', 'limit', 'offset'})
        operation = request.args.get('operation', 'build_ontology')
        if operation != 'build_ontology':
            raise _invalid('此入口仅查询本体构建运行。')
        limit, offset = _integer_arg('limit', 20, 1, 100), _integer_arg('offset', 0, 0)
        if not (root / 'domain.sqlite3').exists():
            return jsonify(items=[])
        runs = runtime.runs()
        with runs.storage.transaction() as conn:
            rows = [_decode(r) for r in conn.execute(
                'SELECT * FROM runs WHERE operation=? ORDER BY rowid DESC LIMIT ? OFFSET ?', (operation, limit, offset))]
        return jsonify(items=runtime.public(rows))

    @bp.get('/api/domain-runs/<run_id>')
    def detail(run_id):
        runs = runtime.runs()
        row = run_row(runs, run_id)
        return jsonify(runtime.public({**row, 'artifacts': artifact_items(runs, run_id)}))

    @bp.get('/api/domain-runs/<run_id>/events')
    def events(run_id):
        _query_fields({'after'})
        after = _integer_arg('after', 0, 0)
        runs = runtime.runs()
        run_row(runs, run_id)
        return jsonify(items=runtime.public(runs.events(run_id, after=after)))

    @bp.post('/api/domain-runs/<run_id>/retry')
    def retry(run_id):
        service = runtime.writer()
        if not request.is_json:
            raise _invalid()
        body = request.get_json()
        if not isinstance(body, dict) or set(body) != {'request_id'}:
            raise _invalid()
        return jsonify(runtime.public(service.retry(run_id, request_id=body['request_id']))), 202

    @bp.get('/api/domain-runs/<run_id>/artifacts/<artifact_id>')
    def download(run_id, artifact_id):
        runs = runtime.runs()
        run_row(runs, run_id)
        records = [a for a in runs.artifacts(run_id) if a['artifact_id'] == artifact_id]
        if not records:
            raise KeyError(artifact_id)
        record = records[0]
        try:
            # Refuse symlink redirection even when its target has the same digest.
            relative = Path(record['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Invalid artifact path')
            current = root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError('Linked artifact')
            content = runs.read_artifact(run_id, artifact_id)
            if len(content) != record['byte_size']:
                raise ValueError('Wrong artifact length')
        except (ValueError, OSError) as exc:
            raise BuildRequestError('artifact_integrity', '登记文件缺失、损坏或路径不可信。', 409) from exc
        return send_file(io.BytesIO(content), mimetype=record['media_type'], as_attachment=True,
                         download_name=record['name'], max_age=0)

    @bp.get('/api/ontology-assets')
    def catalog():
        _query_fields({'domain'})
        domain = request.args.get('domain')
        if domain not in (None, 'data', 'robotics'):
            raise _invalid()
        items, bound = [], set()
        if (root / 'domain.sqlite3').exists():
            assets = runtime.assets()
            with assets.storage.transaction() as conn:
                bindings = {r['ontology_id']: r['legacy_key'] for r in conn.execute('SELECT * FROM legacy_assets')}
            bound = set(bindings.values())
            for asset in assets.list_assets():
                versions = assets.list_versions(asset['ontology_id'])
                latest = versions[-1] if versions else None
                item = {**asset, 'entry_kind': 'asset', 'version_id': latest['version_id'] if latest else None,
                        'publication_state': latest['publication_state'] if latest else None,
                        'versions': versions}
                if asset['ontology_id'] in bindings:
                    item['legacy_key'] = bindings[asset['ontology_id']]
                items.append(item)
        for old in legacy_catalog():
            if old['key'] not in bound:
                items.append({'entry_kind': 'legacy_reference', 'domain': 'data', 'legacy_key': old['key'],
                              'name': old['name'], 'publication_state': 'unregistered'})
        return jsonify(items=runtime.public([r for r in items if domain is None or r['domain'] == domain]))

    @bp.get('/api/ontology-assets/<ontology_id>/versions/<version_id>')
    def version(ontology_id, version_id):
        if not (root / 'domain.sqlite3').exists():
            raise KeyError(version_id)
        assets = runtime.assets()
        row = assets.get_version(ontology_id, version_id)
        rid = row['metadata'].get('build_run_id')
        result = {**row, 'asset': assets.get_asset(ontology_id), 'build_run_id': rid,
                  'sources': [assets.get_source(sid) for sid in row['source_asset_ids']]}
        if rid:
            runs = runtime.runs()
            build = run_row(runs, rid)
            result['artifacts'] = artifact_items(runs, rid)
            result['result'] = build['result']
        if result['asset']['domain'] == 'robotics':
            result['payload_url'] = url_for('robotics_review.payload', ontology_id=ontology_id, version_id=version_id)
            current_artifacts = bp_robotics_review.version_artifacts(runtime, ontology_id, version_id)
            if current_artifacts or not rid:
                result['artifacts'] = current_artifacts
        return jsonify(runtime.public(result))

    bp_robotics_review.init_app(app, runtime)
    app.register_blueprint(bp)
    app.extensions['robotics'] = runtime
    return runtime

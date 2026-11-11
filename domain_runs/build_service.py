"""Atomic, bounded source-build admission — DR-060 / DR-064 / DR-065 / DR-070."""
from copy import deepcopy
import json
from pathlib import Path

from domains.robotics import importer
from domains.robotics.artifact_contract import VALIDATION_SHAPES_SHA256
from ontology_assets import AssetStore
from ontology_assets.payloads import json_text
from ontology_assets.storage import digest

from . import contracts
from .store import RunStore, _run

ROLES = ('behavior_tree', 'node_models')


class BuildRequestError(ValueError):
    def __init__(self, code, message, status_code=400, *, issues=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.issues = deepcopy(issues)


def valid_text(value, field, limit=256):
    try:
        contracts.text(value, field)
        if len(value) > limit:
            raise ValueError('Text too long')
    except ValueError as exc:
        raise BuildRequestError('invalid_request', f'{field} 必须是长度合理的非空文本。') from exc
    return value


def submission_fingerprint(*, name, recipe, profile_id, request_id, sources, description, file_limit_bytes):
    valid_text(name, 'name')
    valid_text(request_id, 'request_id')
    if recipe != 'bt_xml' or profile_id != 'ur_pick_place':
        raise BuildRequestError('invalid_request', '当前只支持 bt_xml / ur_pick_place。')
    if not isinstance(sources, dict) or set(sources) != set(ROLES):
        raise BuildRequestError('invalid_request', '必须同时上传行为树和节点模型 XML。')
    fingerprints = {}
    for role in ROLES:
        item = sources[role]
        if not isinstance(item, dict) or set(item) != {'filename', 'content'}:
            raise BuildRequestError('invalid_request', '来源必须包含文件名和文件字节。')
        valid_text(item['filename'], 'filename', 1024)
        if not isinstance(item['content'], bytes) or not item['content']:
            raise BuildRequestError('invalid_request', '上传文件不能为空。')
        if len(item['content']) > file_limit_bytes:
            raise BuildRequestError('file_too_large', '上传文件超过单文件上限。', 413)
        fingerprints[role] = {'filename': item['filename'], 'sha256': digest(item['content'])}
    if (not isinstance(description, str) or len(description) > 20000
            or any(ord(c) < 32 and c not in '\n\r\t' for c in description)):
        raise BuildRequestError('invalid_request', '建模要求必须是最多 20000 字符的文本。')
    identity = {'operation': 'submit', 'name': name, 'recipe': recipe,
                'profile_id': profile_id, 'sources': fingerprints}
    # Preserve old request fingerprints when the new optional input is absent.
    if description:
        identity['description'] = description
    fingerprint = digest(json_text(identity).encode())
    return fingerprint


def existing_admission(conn, request_id, fingerprint):
    for table in ('build_requests', 'design_admissions'):
        row = conn.execute(f'SELECT * FROM {table} WHERE request_id=?', (request_id,)).fetchone()
        if row is not None:
            if row['fingerprint'] != fingerprint:
                raise BuildRequestError('request_conflict', '此请求编号已经用于不同输入。', 409)
            return _run(conn, row['run_id']) if table == 'build_requests' else json.loads(row['response'])
    return None


class BuildService:
    def __init__(self, root, *, max_pending=4, file_limit_bytes=10 * 1024 * 1024):
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ValueError('等待任务上限必须为 1～64。')
        if type(file_limit_bytes) is not int or file_limit_bytes <= 0:
            raise ValueError('文件上限必须为正整数。')
        self.max_pending, self.file_limit_bytes = max_pending, file_limit_bytes
        self.runs, self.assets = RunStore(root), AssetStore(root)

    def _existing(self, conn, request_id, fingerprint):
        return existing_admission(conn, request_id, fingerprint)

    def _capacity(self, conn):
        count = conn.execute("SELECT COUNT(*) FROM runs WHERE operation='build_ontology' AND status='queued'").fetchone()[0]
        if count >= self.max_pending:
            raise BuildRequestError('queue_full', '构建等待队列已满，请稍后重试。', 429)

    def _register(self, conn, request_id, fingerprint, run):
        conn.execute('INSERT INTO build_requests VALUES (?,?,?)', (request_id, fingerprint, run['run_id']))
        return run

    def submit(self, *, name, recipe, profile_id, request_id, sources, description='', route_resolver=None):
        """Internal legacy callers retain the late-validation worker contract.

        Public admission supplies a resolver and first runs DR-070 shared checks.
        The resolver is trusted host composition, never a client-supplied option.
        """
        sources = deepcopy(sources)
        fingerprint = submission_fingerprint(name=name, recipe=recipe, profile_id=profile_id,
            request_id=request_id, sources=sources, description=description, file_limit_bytes=self.file_limit_bytes)
        if route_resolver is not None:
            # Replay must precede model configuration checks, even after settings change.
            with self.runs.storage.transaction() as conn:
                existing = self._existing(conn, request_id, fingerprint)
            if existing is not None:
                return existing
            from domains.robotics.source_admission import validate_source
            from domains.robotics.source_validation import SourceValidationError
            try:
                for role in ROLES:
                    validate_source(sources[role]['content'], role=role)
            except SourceValidationError as exc:
                raise BuildRequestError('invalid_source', 'XML 来源未通过检查。', issues=exc.issues) from None
            route = route_resolver()
            if route.public()['method'] != 'legacy_xml':
                from .robotics_design_service import DesignService
                service = DesignService(self.runs.storage.root, route_resolver=route_resolver,
                    max_pending=self.max_pending, file_limit_bytes=self.file_limit_bytes)
                return service._admit(name=name, recipe=recipe, profile_id=profile_id, request_id=request_id,
                    sources=sources, description=description, fingerprint=fingerprint, route=route)
        components = {'profile_id': profile_id, 'compiler_version': 'sha256:' + digest(Path(importer.__file__).read_bytes()),
                      'rule_digest': VALIDATION_SHAPES_SHA256, 'prompt_version': None, 'model': {'invoked': False}}
        # BEGIN IMMEDIATE serializes capacity/identity checks with all registration.
        with self.runs.storage.transaction(write=True) as conn:
            existing = self._existing(conn, request_id, fingerprint)
            if existing is not None:
                return existing
            self._capacity(conn)
            registered = [self.assets._put_source(conn, sources[role]['content'], role=role, media_type='application/xml')
                          for role in ROLES]
            run = self.runs._create_build_run(conn, name=name, recipe=recipe, profile_id=profile_id,
                source_asset_ids=[s['source_asset_id'] for s in registered],
                session_ref={'namespace': 'robotics_build', 'id': request_id}, components=components)
            return self._register(conn, request_id, fingerprint, run)

    def retry(self, run_id, *, request_id):
        valid_text(request_id, 'request_id')
        fingerprint = digest(json_text({'operation': 'retry', 'run_id': run_id}).encode())
        with self.runs.storage.transaction(write=True) as conn:
            existing = self._existing(conn, request_id, fingerprint)
            if existing is not None:
                return existing
            old = _run(conn, run_id)
            if old['operation'] != 'build_ontology' or old['status'] not in ('failed', 'interrupted'):
                raise BuildRequestError('retry_conflict', '仅支持失败或中断的构建重试。', 409)
            self._capacity(conn)
            try:
                self.runs._verify_snapshot(conn, old)
            except (ValueError, KeyError, OSError) as exc:
                raise BuildRequestError('source_integrity', '原来源缺失或损坏，无法重试。', 409) from exc
            inputs = {k: old[k] for k in ('ontology_id', 'version_id', 'operation', 'task', 'mode',
                                          'session_ref', 'components', 'build_input')}
            run = self.runs._insert(conn, inputs, None, retry_of=run_id)
            return self._register(conn, request_id, fingerprint, run)

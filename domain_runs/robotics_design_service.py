"""Persistent, revision-bound robot design; model waits hold no write locks — DR-070."""
from copy import deepcopy
import json
import sqlite3
import uuid

from domains.robotics.modeling import abox, evidence, export, tbox, vocabulary
from domains.robotics.modeling.common import ModelingError, digest as json_digest
from domains.robotics.modeling.prompts import PROMPT_VERSION
from ontology_assets import AssetStore
from ontology_assets.payloads import json_text
from ontology_assets.storage import digest

from .build_service import BuildRequestError, existing_admission, submission_fingerprint, valid_text
from .robotics_build_routing import resolve_route, require_same_route
from . import robotics_design_contracts as contracts
from .store import RunStore, _run, _now


def _error(code, message, status=409, *, issues=None):
    return BuildRequestError(code, message, status, issues=issues)


def _bytes(value):
    return json_text(value).encode('utf-8')


def _public(record):
    state = {k: v for k, v in record['state'].items() if not k.startswith('_') and k not in ('evidence', 'vocabulary')}
    result = {**state, **{k: record[k] for k in ('session_id', 'session_ref', 'name', 'method', 'status',
        'run_id', 'output_asset_ref', 'created_at', 'updated_at')}}
    if record['error'] is not None:
        result['error'] = record['error']
    return deepcopy(result)


class DesignService:
    def __init__(self, root, *, route_resolver=None, max_pending=4, read_only=False,
                 file_limit_bytes=10 * 1024 * 1024):
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ValueError('等待任务上限必须为 1～64。')
        if type(file_limit_bytes) is not int or file_limit_bytes <= 0:
            raise ValueError('文件上限必须为正整数。')
        self.runs = RunStore(root, read_only=read_only)
        self.assets = AssetStore(root, read_only=read_only)
        self.storage = self.runs.storage
        self.route_resolver = resolve_route if route_resolver is None else route_resolver
        self.max_pending, self.file_limit_bytes = max_pending, file_limit_bytes

    def _get(self, conn, session_id):
        valid_text(session_id, 'session_id')
        row = conn.execute('SELECT record FROM design_sessions WHERE session_id=?', (session_id,)).fetchone()
        if row is None:
            raise _error('not_found', '设计会话不存在。', 404)
        return json.loads(row[0])

    def _put(self, conn, record):
        conn.execute('INSERT INTO design_sessions VALUES (?,?) ON CONFLICT(session_id) '
            'DO UPDATE SET record=excluded.record', (record['session_id'], json_text(record)))

    def get(self, session_id):
        with self.storage.transaction() as conn:
            return _public(self._get(conn, session_id))

    def _revision_files(self, conn, session_id, revision):
        self._get(conn, session_id)
        if type(revision) is not int or revision < 0:
            raise _error('invalid_request', '修订号必须是非负整数。', 400)
        row = conn.execute('SELECT record FROM design_revisions WHERE session_id=? AND revision=?',
                           (session_id, revision)).fetchone()
        if row is None:
            raise _error('not_found', '设计修订不存在。', 404)
        return json.loads(row[0])

    def list_revisions(self, session_id):
        with self.storage.transaction() as conn:
            self._get(conn, session_id)
            return [{'revision': r[0]} for r in conn.execute(
                'SELECT revision FROM design_revisions WHERE session_id=? ORDER BY revision', (session_id,))]

    def get_revision(self, session_id, revision):
        return json.loads(self.export_revision(session_id, revision, format='json'))

    def export_revision(self, session_id, revision, *, format):
        if format not in ('json', 'owl'):
            raise _error('invalid_request', '仅支持 JSON 或 OWL 导出。', 400)
        with self.storage.transaction() as conn:
            files = self._revision_files(conn, session_id, revision)
            if format not in files:
                raise _error('not_found', '此修订尚未形成可导出的本体定义。', 404)
            record = files[format]
            return self.storage.read_bytes(record['path'], record['sha256'])

    def _save_revision(self, conn, record, *, run=None):
        document = contracts.envelope(record, run=run)
        content = _bytes(document)
        schema = contracts.schema_for_export(record['state'])
        owl = export.tbox_owl(schema, schema['namespace']) if schema else None
        if run is not None:
            artifacts = [contracts.artifact('design.json', 'design', content)]
            if owl is not None:
                artifacts.append(contracts.artifact('tbox.owl', 'ontology', owl, 'application/rdf+xml'))
            outcome = {'status': 'designed_tbox', 'revision': record['state']['revision'],
                       'execution_readiness': 'not_verified'}
            self.runs._complete(conn, run, outcome, artifacts)
            records = [dict(row) for row in conn.execute('SELECT * FROM run_artifacts WHERE run_id=?', (run['run_id'],))]
            files = {'json': next(r for r in records if r['name'] == 'design.json')}
            if owl is not None:
                files['owl'] = next(r for r in records if r['name'] == 'tbox.owl')
        else:
            files = {}
            # A process crash can leave unregistered files; never reuse their publication paths.
            snapshot_id = uuid.uuid4().hex
            for format, raw in [('json', content), ('owl', owl)]:
                if raw is None:
                    continue
                relative = f"designs/{record['session_id']}/{record['state']['revision']}/{snapshot_id}/design.{format}"
                self.storage.write_bytes(relative, raw)
                files[format] = {'path': relative, 'sha256': digest(raw), 'byte_size': len(raw)}
        conn.execute('INSERT INTO design_revisions VALUES (?,?,?)',
            (record['session_id'], record['state']['revision'], json_text(files)))

    def _capacity(self, conn):
        count = conn.execute("SELECT COUNT(*) FROM runs WHERE operation IN ('design_tbox','extract_abox') "
                             "AND status='queued'").fetchone()[0]
        if count >= self.max_pending:
            raise _error('queue_full', '设计等待队列已满，请稍后重试。', 429)

    def _route(self, record):
        route = self.route_resolver()
        require_same_route({'method': record['method'], 'model': record['state']['model']}, route.public())
        return route

    def _fixed(self, record, operation, text):
        state = record['state']
        return {'sources': record['sources'], 'source_sha256': state['source_sha256'],
            'vocabulary_sha256': state['vocabulary']['sha256'], 'state_sha256': json_digest(state),
            'revision': state['revision'], 'stage': state['stage'], 'text': text,
            'model': state['model'], 'prompt_version': state['prompt_version'],
            'confirmation_sha256': state['schema_confirmation']['sha256'] if operation == 'extract_abox' else None,
            'mapping_sha256': contracts.mapping_digest() if operation == 'extract_abox' else None}

    def _enqueue(self, conn, record, operation, text='', *, retry_of=None, fixed=None):
        self._capacity(conn)
        model = {'invoked': operation == 'design_tbox', **record['state']['model']}
        components = {'profile_id': record['profile_id'], 'compiler_version': 'robotics-design-v1',
            'rule_digest': contracts.mapping_digest(), 'prompt_version': PROMPT_VERSION, 'model': model}
        run = self.runs._insert(conn, {'ontology_id': None, 'version_id': None, 'operation': operation,
            'task': {}, 'mode': 'real', 'session_ref': record['session_ref'], 'components': components,
            'design_input': fixed or self._fixed(record, operation, text)}, None, retry_of=retry_of)
        record.update(run_id=run['run_id'], status='queued', error=None, updated_at=_now())
        return run

    def submit(self, *, name, recipe, profile_id, request_id, sources, description=''):
        sources = deepcopy(sources)
        fingerprint = submission_fingerprint(name=name, recipe=recipe, profile_id=profile_id,
            request_id=request_id, sources=sources, description=description, file_limit_bytes=self.file_limit_bytes)
        with self.storage.transaction() as conn:
            old = existing_admission(conn, request_id, fingerprint)
        if old is not None:
            return old
        return self._admit(name=name, recipe=recipe, profile_id=profile_id, request_id=request_id,
            sources=sources, description=description, fingerprint=fingerprint, route=self.route_resolver())

    def _admit(self, *, name, recipe, profile_id, request_id, sources, description, fingerprint, route):
        public = route.public()
        if public.get('method') != 'llm_tbox_abox':
            raise _error('model_not_available', '设计会话需要模型配置，请从构建入口使用离线构建。', 503)
        try:
            bundle = evidence.ingest_sources({role: item['content'] for role, item in sources.items()})
        except ValueError as exc:
            raise _error('invalid_source', 'XML 来源未通过检查。', 400, issues=getattr(exc, 'issues', [])) from None
        state = tbox.new_design(bundle, vocabulary.load_vocabulary())
        state['model'] = deepcopy(public['model'])
        now, sid = _now(), 'design_' + uuid.uuid4().hex
        record = {'session_id': sid, 'session_ref': {'namespace': 'robotics_design', 'id': sid},
            'name': name, 'recipe': recipe, 'profile_id': profile_id, 'method': 'llm_tbox_abox', 'state': state,
            'status': 'needs_description', 'run_id': None, 'output_asset_ref': None, 'last_asset_ref': None,
            'created_at': now, 'updated_at': now, 'error': None}
        with self.storage.transaction(write=True) as conn:
            old = existing_admission(conn, request_id, fingerprint)
            if old is not None:
                return old
            if description.strip():
                self._capacity(conn)
            registered = [self.assets._put_source(conn, sources[role]['content'], role=role, media_type='application/xml')
                          for role in ('behavior_tree', 'node_models')]
            record['sources'] = [{k: row[k] for k in ('source_asset_id', 'role', 'sha256', 'byte_size')} for row in registered]
            self._put(conn, record)
            self._save_revision(conn, record)
            if description.strip():
                self._enqueue(conn, record, 'design_tbox', description)
            self._put(conn, record)
            response = _public(record)
            conn.execute('INSERT INTO design_admissions VALUES (?,?,?,?)', (request_id, fingerprint, json_text(response), sid))
            return response

    def _request(self, conn, sid, request_id, operation, args):
        valid_text(request_id, 'request_id')
        fingerprint = json_digest({'operation': operation, **args})
        row = conn.execute('SELECT * FROM design_requests WHERE session_id=? AND request_id=?', (sid, request_id)).fetchone()
        if row is not None and row['fingerprint'] != fingerprint:
            raise _error('request_conflict', '此请求编号已用于不同操作或输入。')
        return fingerprint, json.loads(row['response']) if row else None

    def _reply(self, conn, record, request_id, fingerprint):
        self._put(conn, record)
        response = _public(record)
        conn.execute('INSERT INTO design_requests VALUES (?,?,?,?)',
            (record['session_id'], request_id, fingerprint, json_text(response)))
        return response

    def _expect(self, record, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise _error('invalid_request', '修订号必须是非负整数。', 400)
        if record['state']['revision'] != expected_revision:
            raise _error('revision_conflict', '设计版本已经变化，请刷新后重试。')
        if record['status'] in ('queued', 'running'):
            raise _error('session_busy', '此设计有尚未完成的运行，请稍后重试。')

    def _check_snapshot(self, conn, record):
        files = self._revision_files(conn, record['session_id'], record['state']['revision'])
        document = json.loads(self.storage.read_bytes(files['json']['path'], files['json']['sha256']))
        if document['state'] != record['state']:
            raise _error('snapshot_conflict', '当前状态与固定修订不一致。')
        contracts.check_state(record['state'])
        from .build_contracts import source_snapshot
        try:
            current = source_snapshot(self.storage, conn, [s['source_asset_id'] for s in record['sources']])
            if current != record['sources']:
                raise ValueError('Changed source')
        except (OSError, ValueError, KeyError) as exc:
            raise _error('source_integrity', '来源缺失或损坏，请重新上传。') from exc

    def message(self, session_id, *, text, request_id, expected_revision, expected_stage):
        if (not isinstance(text, str) or not text.strip() or len(text) > 20000
                or vocabulary.INVALID_TEXT.search(text)):
            raise _error('invalid_request', '建模要求需要有效的非空文本。', 400)
        with self.storage.transaction(write=True) as conn:
            record = self._get(conn, session_id)
            fingerprint, replay = self._request(conn, session_id, request_id, 'message',
                {'text': text, 'expected_revision': expected_revision, 'expected_stage': expected_stage})
            if replay is not None:
                return replay
            self._expect(record, expected_revision)
            if record['state']['stage'] != expected_stage or expected_stage not in ('classes', 'properties'):
                raise _error('stage_conflict', '设计阶段已经变化，请刷新后重试。')
            self._check_snapshot(conn, record)
            self._route(record)  # Read-only configuration resolution; never a network probe.
            self._enqueue(conn, record, 'design_tbox', text)
            return self._reply(conn, record, request_id, fingerprint)

    def _local(self, session_id, operation, *, request_id, expected_revision, snapshot_sha256=None):
        editing = operation in ('edit_classes', 'edit_properties')
        with self.storage.transaction(write=True) as conn:
            record = self._get(conn, session_id)
            args = {'expected_revision': expected_revision}
            if not editing:
                args['snapshot_sha256'] = snapshot_sha256
            fingerprint, replay = self._request(conn, session_id, request_id, operation, args)
            if replay is not None:
                return replay
            self._expect(record, expected_revision)
            self._check_snapshot(conn, record)
            try:
                function = getattr(tbox, operation)
                state = function(record['state']) if editing else function(
                    record['state'], snapshot_sha256=snapshot_sha256)
            except ModelingError as exc:
                raise _error(exc.code, str(exc), issues=exc.issues) from None
            record.update(state=state, updated_at=_now(), error=None, status='awaiting_input')
            if editing:
                record['output_asset_ref'] = None
            self._save_revision(conn, record)
            if operation == 'confirm_schema':
                self._enqueue(conn, record, 'extract_abox')
            return self._reply(conn, record, request_id, fingerprint)

    def confirm_classes(self, session_id, *, request_id, expected_revision, snapshot_sha256):
        return self._local(session_id, 'confirm_classes', request_id=request_id,
            expected_revision=expected_revision, snapshot_sha256=snapshot_sha256)

    def edit_classes(self, session_id, *, request_id, expected_revision):
        return self._local(session_id, 'edit_classes', request_id=request_id, expected_revision=expected_revision)

    def edit_properties(self, session_id, *, request_id, expected_revision):
        return self._local(session_id, 'edit_properties', request_id=request_id, expected_revision=expected_revision)

    def confirm_schema(self, session_id, *, request_id, expected_revision, snapshot_sha256):
        return self._local(session_id, 'confirm_schema', request_id=request_id,
            expected_revision=expected_revision, snapshot_sha256=snapshot_sha256)

    def retry(self, session_id, *, request_id, expected_revision, failed_run_id):
        with self.storage.transaction(write=True) as conn:
            record = self._get(conn, session_id)
            fingerprint, replay = self._request(conn, session_id, request_id, 'retry',
                {'expected_revision': expected_revision, 'failed_run_id': failed_run_id})
            if replay is not None:
                return replay
            try:
                old = _run(conn, failed_run_id)
            except KeyError:
                raise _error('retry_conflict', '该失败运行不属于此设计。') from None
            if (old['session_ref'] != record['session_ref'] or old['operation'] not in contracts.OPERATIONS
                    or old['status'] not in ('failed', 'interrupted')
                    or expected_revision != record['state']['revision']):
                raise _error('retry_conflict', '仅支持重试同一设计当前修订的失败或中断运行。')
            self._expect(record, expected_revision)
            if old['run_id'] != record['run_id'] or old['design_input']['state_sha256'] != json_digest(record['state']):
                raise _error('retry_conflict', '失败运行已过期，请刷新设计。')
            self._check_snapshot(conn, record)
            if old['operation'] == 'design_tbox':
                self._route(record)
            self._enqueue(conn, record, old['operation'], retry_of=failed_run_id, fixed=old['design_input'])
            return self._reply(conn, record, request_id, fingerprint)

    def claim_next(self):
        with self.storage.transaction(write=True) as conn:
            row = conn.execute("SELECT run_id FROM runs WHERE operation IN ('design_tbox','extract_abox') "
                               "AND status='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            run = _run(conn, row[0])
            record = self._get(conn, run['session_ref']['id'])
            self.runs._state(conn, run['run_id'], 'running')
            record.update(status='running', updated_at=_now())
            self._put(conn, record)
            return _run(conn, run['run_id'])

    def recover_interrupted(self):
        with self.storage.transaction(write=True) as conn:
            rows = conn.execute("SELECT run_id FROM runs WHERE operation IN ('design_tbox','extract_abox') "
                                "AND status='running' ORDER BY rowid").fetchall()
            for row in rows:
                run = _run(conn, row[0])
                self.runs._state(conn, run['run_id'], 'interrupted')
                record = self._get(conn, run['session_ref']['id'])
                if record['run_id'] == run['run_id']:
                    record.update(status='interrupted', updated_at=_now())
                    self._put(conn, record)
            return [r[0] for r in rows]

    def _current(self, conn, initial):
        run = _run(conn, initial['run_id'])
        record = self._get(conn, run['session_ref']['id'])
        current = (run['status'] == 'running' and record['run_id'] == run['run_id']
                   and json_digest(record['state']) == initial['design_input']['state_sha256']
                   and run['design_input'] == initial['design_input'])
        return run, record, current

    def _fail(self, initial, exc):
        code = getattr(exc, 'code', 'invalid_design')
        # Validator errors can quote untrusted provider content; persist only controlled messages.
        message = {'source_integrity': '来源缺失或损坏，原设计已保留。',
            'configuration_changed': '模型身份已变化，请恢复原配置或新建设计。',
            'unsupported_source': 'XML 包含当前抽取器不支持的结构。'}.get(code, '本轮设计失败，原有效修订已保留，可显式重试。')
        issues = deepcopy(getattr(exc, 'issues', None) or []) if code == 'unsupported_source' else []
        if code=='model_request_failed':
            from llm_diagnostics import safe_diagnostic, describe
            issues=[safe_diagnostic(i) for i in (getattr(exc,'issues',None) or [])[:1]]
            if issues:message=describe(issues[0])+'原有效修订已保留，可显式重试。'
        outcome = {'status': 'error', 'error': {'code': code, 'message': message, 'issues': issues},
                   'execution_readiness': 'not_verified'}
        with self.storage.transaction(write=True) as conn:
            run, record, current = self._current(conn, initial)
            if not current:
                return run
            self.runs._complete(conn, run, outcome,
                [contracts.artifact('failure.json', 'validation', _bytes(outcome))])
            record.update(status='failed', error=outcome['error'], updated_at=_now())
            self._put(conn, record)
            return run

    def execute(self, run_id):
        # Durable execution claim protects against duplicate callers, including other processes.
        with self.storage.transaction(write=True) as conn:
            initial = _run(conn, run_id)
            if initial['operation'] not in contracts.OPERATIONS or initial['status'] != 'running':
                raise ValueError('设计运行未领取或已结束。')
            try:
                conn.execute('INSERT INTO design_executions VALUES (?)', (run_id,))
            except sqlite3.IntegrityError:
                raise ValueError('此设计运行已开始执行。') from None
            record = self._get(conn, initial['session_ref']['id'])
        try:
            with self.storage.transaction() as conn:
                self._check_snapshot(conn, record)
                contracts.verify_input(self.storage, conn, initial)
            state = record['state']
            if json_digest(state) != initial['design_input']['state_sha256']:
                raise _error('snapshot_conflict', '运行快照已过期。')
            if initial['operation'] == 'design_tbox':
                route = self._route(record)
                revised = tbox.revise(state, initial['design_input']['text'], route,
                    session_id=record['session_id'], timeout=300)
                product, ontology = None, None
            else:
                if contracts.mapping_digest() != initial['design_input']['mapping_sha256']:
                    raise _error('mapping_changed', '抽取映射版本已变化，请恢复原版本。')
                revised = state
                product = abox.extract(state['schema_confirmation'], state['evidence'])
                ontology = export.abox_owl(product['result'], state['schema_confirmation']['tbox'], state['evidence']['evidence'])
                # Match the platform lock order: expensive RDF validation precedes the SQLite write transaction.
                document = contracts.envelope(record, run=initial, abox=product['result'], coverage=product['report'],
                    validation=contracts.candidate_outcome(), output_asset_ref=None, payload_sha256=digest(ontology))
                contracts.validate_candidate(document, ontology, initial)
            with self.storage.transaction(write=True) as conn:
                run, current, active = self._current(conn, initial)
                if not active:
                    return run
                contracts.verify_input(self.storage, conn, run)
                current.update(state=revised, updated_at=_now(), error=None)
                if run['operation'] == 'design_tbox':
                    contracts.check_state(revised)
                    current['status'] = 'awaiting_confirmation'
                    self._save_revision(conn, current, run=run)
                else:
                    self._finish_abox(conn, current, run, product, ontology)
                self._put(conn, current)
                return run
        except (OSError, sqlite3.Error):
            # Failed writes may make recording failure impossible. Keep running until explicit recovery.
            raise
        except (ValueError, KeyError) as exc:
            return self._fail(initial, exc)

    def _finish_abox(self, conn, record, run, product, ontology):
        last = record['last_asset_ref']
        oid = last['ontology_id'] if last else self.assets._create_asset(conn, record['name'], 'robotics')['ontology_id']
        version = self.assets._add_version(conn, oid, ontology, payload_kind='robotics_rdf',
            parent_version_id=last['version_id'] if last else None,
            source_asset_ids=[s['source_asset_id'] for s in record['sources']],
            metadata={'design_contract': contracts.DESIGN_CONTRACT, 'session_ref': record['session_ref'],
                'design_revision': record['state']['revision'], 'build_run_id': run['run_id'], 'profile_id': record['profile_id']})
        ref = {k: version[k] for k in ('ontology_id', 'version_id')}
        outcome = contracts.candidate_outcome()
        document = contracts.envelope(record, run=run, abox=product['result'], coverage=product['report'],
            validation=outcome, output_asset_ref=ref, payload_sha256=digest(ontology))
        run['output_asset_ref'] = ref
        self.runs._complete(conn, run, outcome, [
            contracts.artifact('candidate.owl', 'ontology', ontology, 'application/rdf+xml'),
            contracts.artifact('design.json', 'design', _bytes(document))])
        record.update(status='completed', output_asset_ref=ref, last_asset_ref=ref)

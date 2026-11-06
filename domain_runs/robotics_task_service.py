"""Persistent task dialogue and atomic, scoped knowledge completion — DR-061 / DR-064 / DR-066.

Model calls run outside database/RDF locks. Public state is a fixed session snapshot;
requests are idempotent, and immutable versions/runs remain the source of truth.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import uuid
import xml.etree.ElementTree as ET

from rdflib import RDF, RDFS, URIRef

from domains.robotics import consumer, evidence, task_knowledge
from domains.robotics.graph import S
from domains.robotics.task_completion import inspect_gaps
from domains.robotics.task_dialogue import catalog_from_graph, grounded_mentions, ENTITY_FIELDS
from domains.robotics.task_model import ModelClient, ModelError
from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceService
from ontology_assets.payloads import json_text
from ontology_assets.storage import digest
from srv_context import _RDF_LOCK

from . import RunStore
from .robotics import generate_application

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS robotics_task_sessions (session_id TEXT PRIMARY KEY, record TEXT NOT NULL)',
    '''CREATE TABLE IF NOT EXISTS robotics_task_requests (
        scope TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, response TEXT,
        PRIMARY KEY(scope,request_id))''',
)


class TaskError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _text(value, name, limit=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\0' in value:
        raise TaskError('invalid_request', f'{name} 需要长度合理的非空文字。')
    return value


def _tree(node):
    return {'tag': node.tag, 'attributes': dict(node.attrib), 'children': [_tree(c) for c in node]}


def _clear(state):
    state.update(candidate=None, tree=None, run_id=None, error=None, result=None, gaps=[], issues=[], configurations=[])
    state.update(optional_gaps=[], task_readiness='not_verified')


class TaskService:
    def __init__(self, root, model_client=None, *, generation_policy='strict'):
        if generation_policy not in ('strict', 'offline'):
            raise ValueError('未知的生成策略。')
        self.generation_policy = generation_policy
        self.assets = AssetStore(root)
        self.storage = self.assets.storage
        self.root = self.storage.root
        self.governance = GovernanceService(root)
        self.model = model_client if model_client is not None else ModelClient.from_datamind()
        # DR-064: additive application tables; the shared asset schema/version stays compatible.
        with self.storage.transaction(write=True) as conn:
            for statement in SCHEMA:
                conn.execute(statement)

    @contextmanager
    def _atomic(self):
        try:
            with _RDF_LOCK, self.storage.transaction(write=True) as conn:
                yield conn
        except TaskError:
            raise
        except KeyError as exc:
            raise TaskError('not_found', '指定的本体、版本或任务不存在。') from exc
        except OSError as exc:
            raise TaskError('save_failed', '保存失败，原版本和输入保持不变，请重试。') from exc
        except (ValueError, TypeError) as exc:
            raise TaskError('invalid_answer', str(exc)) from exc

    def _get(self, conn, sid):
        _text(sid, 'session_id', 128)
        row = conn.execute('SELECT record FROM robotics_task_sessions WHERE session_id=?', (sid,)).fetchone()
        if row is None:
            raise TaskError('not_found', '指定任务不存在。')
        state = json.loads(row[0])
        state.setdefault('generation_policy', self.generation_policy)
        state.setdefault('adaptation_notes', [])
        state['version_number'] = self.assets._version_number(conn, state['ontology_id'], state['version_id'])
        return state

    def _put(self, conn, state):
        conn.execute('INSERT INTO robotics_task_sessions VALUES (?,?) ON CONFLICT(session_id) '
                     'DO UPDATE SET record=excluded.record', (state['session_id'], json_text(state)))

    def get(self, session_id):
        with _RDF_LOCK, self.storage.transaction() as conn:
            state = self._get(conn, session_id)
            if state['generation_policy'] == 'offline' and state.get('task'):
                self._project(state, self._graph(state, conn, inspect_structure=False),
                              self.governance._revocations(conn, state['ontology_id']))
            if 'configurations' not in state:
                state['configurations'] = (inspect_gaps(self._graph(state, conn, inspect_structure=False),
                    state['task'], mode=state['mode'])['configurations'] if state.get('task') else [])
            return state

    def _request(self, conn, scope, request_id, operation, args):
        _text(request_id, 'request_id', 128)
        fingerprint = digest(json_text({'operation': operation, **args}).encode())
        row = conn.execute('SELECT * FROM robotics_task_requests WHERE scope=? AND request_id=?',
                           (scope, request_id)).fetchone()
        if row:
            if row['fingerprint'] != fingerprint:
                raise TaskError('request_conflict', '此请求编号已用于不同输入。')
            if row['response'] is None:
                raise TaskError('task_busy', '此请求正在处理，请稍后回看任务。')
            return json.loads(row['response'])
        conn.execute('INSERT INTO robotics_task_requests VALUES (?,?,?,NULL)', (scope, request_id, fingerprint))
        return None

    def _reply(self, conn, scope, request_id, state):
        conn.execute('UPDATE robotics_task_requests SET response=? WHERE scope=? AND request_id=?',
                     (json_text(state), scope, request_id))
        return state

    def _revision(self, state, expected):
        if type(expected) is not int or expected < 0:
            raise TaskError('invalid_request', '任务修订号格式错误。')
        if expected != state['revision']:
            raise TaskError('session_conflict', '任务已经变化，请刷新当前任务后重试。')
        if state['status'] == 'running':
            raise TaskError('task_busy', '此任务正在处理，可等待或新建任务。')

    def _graph(self, state, conn=None, *, inspect_structure=True):
        try:
            if conn is None:
                with self.storage.transaction() as reader:
                    return self._graph(state, reader, inspect_structure=inspect_structure)
            version = self.governance._version(conn, state['ontology_id'], state['version_id'])
            graph = self.governance._graph(conn, version)
            if inspect_structure:
                task_knowledge.inspect_structure(graph, state['mode'])
            return graph
        except (ValueError, OSError, KeyError) as exc:
            raise TaskError('invalid_ontology', '本体执行结构、来源或固定版本字节不兼容，请检查本体。') from exc

    def start(self, *, ontology_id, version_id, mode, request_id):
        _text(ontology_id, 'ontology_id', 128)
        _text(version_id, 'version_id', 128)
        if mode not in ('real', 'demo'):
            raise TaskError('invalid_request', '模式必须是 real 或 demo。')
        with self._atomic() as conn:
            replay = self._request(conn, 'start', request_id, 'start',
                {'ontology_id': ontology_id, 'version_id': version_id, 'mode': mode})
            if replay is not None:
                return replay
            state = {'session_id': 'task_' + uuid.uuid4().hex, 'ontology_id': ontology_id,
                'asset_name': self.governance._asset(conn, ontology_id)['name'],
                'version_id': version_id, 'mode': mode, 'revision': 0, 'status': 'idle', 'messages': [],
                'task': None, 'model_status': self.model.status(), 'execution_readiness': 'not_verified'}
            state.update(generation_policy=self.generation_policy, adaptation_notes=[],
                         version_number=self.assets._version_number(conn, ontology_id, version_id))
            _clear(state)
            self._graph(state, conn)
            self._put(conn, state)
            return self._reply(conn, 'start', request_id, state)

    def message(self, session_id, *, text, expected_revision, request_id):
        _text(text, '任务描述')
        with self._atomic() as conn:
            state = self._get(conn, session_id)
            replay = self._request(conn, session_id, request_id, 'message',
                {'text': text, 'revision': expected_revision})
            if replay is not None:
                return replay
            self._revision(state, expected_revision)
            graph = self._graph(state, conn)
            if len(state['messages']) >= 100:
                raise TaskError('invalid_request', '该任务对话已达到上限，请开始新任务。')
            _clear(state)
            state['revision'] += 1
            state['status'] = 'running'
            state['messages'].append({'role': 'user', 'content': text})
            state['model_status'] = self.model.status()
            self._put(conn, state)
        try:
            catalog = catalog_from_graph(graph)
            extracted = self.model.parse(state['messages'], catalog)
            selected, issues = grounded_mentions(extracted, state['messages'], catalog)
            state['task'] = {**extracted, **selected}
            if issues:
                state.update(status='needs_clarification', issues=issues)
            else:
                self._evaluate(state)
        except ModelError as exc:
            state.update(status='error', error={'code': exc.code, 'message': str(exc)})
        except (TaskError, ValueError, OSError) as exc:
            state.update(status='error', error={'code': getattr(exc, 'code', 'generation_error'),
                'message': '无法完成本次任务，请检查本体和连接后重试。'})
        return self._finish(state, request_id)

    def _project(self, state, graph, revocations):
        """DR-067: split optional applicability from required knowledge, without writes."""
        projection = inspect_gaps(graph, state['task'], mode=state['mode'], revocations=revocations)
        items = projection['items']
        optional = [g for g in items if g['kind'] == 'applicability'] if state['generation_policy'] == 'offline' else []
        required = [g for g in items if g not in optional]
        status = projection['status']
        if status == 'needs_information' and not required:
            status = 'ready'
        state.update(gaps=required, optional_gaps=optional, issues=projection['issues'],
                     configurations=projection['configurations'],
                     task_readiness='evidence_confirmed' if projection['status'] == 'ready' else 'not_verified')
        for item in state['gaps'] + state['optional_gaps']:
            subject = URIRef(item['entity_iri'])
            item['entity_label'] = str(graph.value(subject, RDFS.label) or subject)
            if item['kind'] == 'mapping':
                item['target_kind'] = task_knowledge.authoring.RELATIONS[item['field']][1]
            if item['kind'] == 'confirmation':
                item['values'] = {name: str(value) for name in ('objectName', 'modelName', 'threshold', 'exposureTime', 'rawValue')
                                 if (value := graph.value(subject, S[name])) is not None}
            if item['kind'] == 'applicability':
                item['scope_labels'] = [str(graph.value(URIRef(item['scope'][key]), RDFS.label)
                    or item['scope'][key].rsplit('#', 1)[-1])
                    for key in ('part_type', 'slot', 'implementation') if item['scope'].get(key)]
                item['saved_answer'] = task_knowledge.pending_answer(graph, item, revocations=revocations)
        return status

    def _evaluate(self, state):
        with _RDF_LOCK:
            # The admitted snapshot was inspected before extraction or controlled edits.
            # Re-read/check actual bytes here; ready candidates still pass full SHACL
            # in generate_application and again in the existing compiler pipeline.
            graph = self._graph(state, inspect_structure=False)
            revocations = self.governance.revocations(state['ontology_id'])
            state['status'] = self._project(state, graph, revocations)
            if state['status'] != 'ready':
                if state['status'] == 'error':
                    state['error'] = {'code': 'invalid_ontology', 'message': '本体知识格式不兼容。'}
                return
            run = generate_application(self.root, ontology_id=state['ontology_id'], version_id=state['version_id'],
                task=state['task'], mode=state['mode'], session_ref={'namespace': 'robotics_task', 'id': state['session_id']},
                purpose=state['generation_policy'], model_info={'invoked': True, 'provider': 'openai-compatible',
                            'model': state['model_status'].get('model') or 'unconfigured'})
            state.update(run_id=run['run_id'], result=run['result'], status=run['result']['status'])
            state['task_readiness'] = run['result'].get('task_readiness', state['task_readiness'])
            if state['status'] == 'generated':
                runs = RunStore(self.root)
                artifact = next(a for a in runs.artifacts(run['run_id']) if a['kind'] == 'candidate')
                raw = runs.read_artifact(run['run_id'], artifact['artifact_id'])
                state.update(candidate={'run_id': run['run_id'], 'artifact_id': artifact['artifact_id']},
                             tree=_tree(ET.fromstring(raw)))
            else:
                state['issues'] = run['result'].get('issues', [])

    def _finish(self, state, request_id):
        texts = {'generated': '行为树已生成，可以查看和下载。', 'needs_information': '请补齐下方当前任务所需的信息。',
                 'unsupported': '该任务超出当前固定抓移放模板的能力。', 'error': '本次任务未完成，请查看错误提示。'}
        text = texts.get(state['status']) or '；'.join(i['message'] for i in state['issues']) or '请明确当前任务的对象和目标。'
        state['messages'].append({'role': 'assistant', 'content': text})
        with self._atomic() as conn:
            current = self._get(conn, state['session_id'])
            if current['revision'] != state['revision']:
                raise TaskError('session_conflict', '任务已变化，本次迟到结果不再生效。')
            self._put(conn, state)
            return self._reply(conn, state['session_id'], request_id, state)

    def _save_version(self, conn, state, graph, sources=()):
        base = self.governance._version(conn, state['ontology_id'], state['version_id'])
        self.governance._sources(conn, sources)
        metadata = deepcopy(base['metadata'])
        build_id = metadata.pop('build_run_id', None)
        if build_id:
            metadata['origin_build_run_id'] = build_id
        metadata.update(evidence_policy=evidence.POLICY, mode=state['mode'], task_completion={
            'session_id': state['session_id'], 'origin': 'local_session', 'authenticated_actor': False})
        evidence.mark_governed(graph)
        version = self.assets._add_version(conn, state['ontology_id'], graph.serialize(format='xml', encoding='utf-8'),
            payload_kind='robotics_rdf', parent_version_id=base['version_id'],
            source_asset_ids=list(dict.fromkeys(base['source_asset_ids'] + list(sources))), metadata=metadata)
        # Keep advanced maintenance's head coherent without overwriting another branch's head.
        head = self.governance._head(conn, state['ontology_id'])
        if head['version_id'] in (base['version_id'], version['version_id']):
            conn.execute('INSERT INTO governance_heads VALUES (?,?,?) ON CONFLICT(ontology_id) '
                'DO UPDATE SET version_id=excluded.version_id,revision=excluded.revision',
                (state['ontology_id'], version['version_id'], head['revision'] + 1))
        state['version_id'] = version['version_id']
        state['version_number'] = self.assets._version_number(conn, state['ontology_id'], version['version_id'])

    def generate(self, session_id, *, expected_revision, request_id):
        return self._edit(session_id, 'generate', {}, expected_revision, request_id)

    def save_notes(self, session_id, *, notes, expected_revision, request_id):
        """Store optional, version-scoped notes; never manufacture RDF evidence."""
        with self._atomic() as conn:
            state = self._get(conn, session_id)
            replay = self._request(conn, session_id, request_id, 'notes', {'notes': notes, 'revision': expected_revision})
            if replay is not None:
                return replay
            self._revision(state, expected_revision)
            if state['task'] is None or not isinstance(notes, list) or not 1 <= len(notes) <= 2:
                raise TaskError('invalid_answer', '请先明确任务，并填写抓取或放置备注。')
            projection = inspect_gaps(self._graph(state, conn, inspect_structure=False), state['task'], mode=state['mode'])
            if state['generation_policy'] == 'offline':
                self._project(state, self._graph(state, conn, inspect_structure=False),
                              self.governance._revocations(conn, state['ontology_id']))
            available = {c['field'] for c in projection['configurations']}
            seen = set()
            relations = {'grasp_compatibility': 'hasPerceptionProfile', 'placement_compatibility': 'hasPlacementProfile'}
            for note in notes:
                if not isinstance(note, dict) or set(note) != {'kind', 'text'} or not isinstance(note['kind'], str):
                    raise TaskError('invalid_answer', '记录只接受类型和备注文字。')
                kind = note['kind']
                if kind not in relations or kind in seen or relations[kind] not in available:
                    raise TaskError('invalid_answer', '只能记录当前任务已明确的抓取或放置配置。')
                if not isinstance(note['text'], str) or not note['text'].strip() or len(note['text']) > 4096:
                    raise TaskError('invalid_answer', '备注必须为 1～4096 字符的非空文字。')
                seen.add(kind)
            state['adaptation_notes'] = [n for n in state['adaptation_notes']
                if n['version_id'] != state['version_id'] or n.get('task') != state['task'] or n['kind'] not in seen]
            state['adaptation_notes'].extend({**note, 'version_id': state['version_id'], 'task': deepcopy(state['task'])} for note in notes)
            state['revision'] += 1
            self._put(conn, state)
            return self._reply(conn, session_id, request_id, state)

    def complete(self, session_id, *, answers, expected_revision, request_id):
        return self._edit(session_id, 'complete', {'answers': answers}, expected_revision, request_id)

    def configure(self, session_id, *, field, expected_revision, request_id, target=None, create=None):
        return self._edit(session_id, 'configure', {'field': field, 'target': target, 'create': create},
                          expected_revision, request_id)

    def choose(self, session_id, *, field, expected_revision, request_id, target=None, create=None):
        return self._edit(session_id, 'choose', {'field': field, 'target': target, 'create': create},
                          expected_revision, request_id)

    def _edit(self, sid, operation, args, revision, request_id):
        with self._atomic() as conn:
            state = self._get(conn, sid)
            replay = self._request(conn, sid, request_id, operation, {**args, 'revision': revision})
            if replay is not None:
                return replay
            self._revision(state, revision)
            if state['task'] is None:
                raise TaskError('invalid_answer', '请先描述当前任务。')
            # start/message admitted this exact immutable version. Recheck stored
            # bytes/sources here, then permit only scoped authoring operations.
            # The saved result still passes full consumer/compiler validation;
            # repeating the baseline SHACL before every answer adds no new gate.
            graph = self._graph(state, conn, inspect_structure=False)
            sources = []
            if operation == 'complete':
                revocations = self.governance._revocations(conn, state['ontology_id'])
                graph, state['task'], sources = task_knowledge.apply_answers(graph, state['task'], args['answers'],
                    mode=state['mode'], revocations=revocations)
                self._save_version(conn, state, graph, sources)
            elif operation == 'generate':
                pass  # Explicit continuation: reuse the selected task; no LLM or version edit.
            elif operation == 'configure':
                graph = task_knowledge.replace_configuration(graph, state['task'], mode=state['mode'],
                    revocations=self.governance._revocations(conn, state['ontology_id']), **args)
                self._save_version(conn, state, graph)
                state['messages'].append({'role': 'user', 'content': '更换' +
                    ('感知配置' if args['field'] == 'hasPerceptionProfile' else '放置配置') + '，保存为新的本体版本。'})
            else:
                field, target, spec = args['field'], args['target'], args['create']
                if field not in ENTITY_FIELDS or not any(i.get('field') == field for i in state['issues']):
                    raise TaskError('invalid_answer', '这不是当前需要澄清的对象。')
                kind = ENTITY_FIELDS[field][0]
                if (target is None) == (spec is None):
                    raise TaskError('invalid_answer', '请选择已有对象或明确新建对象。')
                if spec is not None:
                    graph, selected = task_knowledge.create_entity(graph, spec, kind)
                    self._save_version(conn, state, graph)
                else:
                    selected = task_knowledge.authoring.iri(target)
                    if (selected, RDF.type, S[kind]) not in graph:
                        raise TaskError('invalid_answer', '对象类型不匹配。')
                state['task'][field] = str(selected)
                state['messages'].append({'role': 'user', 'content': '明确选择' + ENTITY_FIELDS[field][1] + '：' + str(selected)})
            _clear(state)
            state['revision'] += 1
            state['status'] = 'running'
            self._put(conn, state)
        try:
            self._evaluate(state)
        except (ValueError, OSError) as exc:
            state.update(status='error', error={'code': getattr(exc, 'code', 'generation_error'),
                'message': '信息已保存，但本体仍未通过严格生成校验，请检查后继续。'})
        return self._finish(state, request_id)

    def read_candidate(self, session_id, *, expected_revision, run_id=None, artifact_id=None):
        state = self.get(session_id)
        if expected_revision != state['revision'] or (run_id is not None and state['candidate'] != {
            'run_id': run_id, 'artifact_id': artifact_id}):
            raise TaskError('stale_result', '任务已变化，旧下载已失效。')
        if state['status'] != 'generated' or not state['candidate']:
            raise TaskError('no_candidate', '当前任务尚无可下载的行为树。')
        try:
            return RunStore(self.root, read_only=True).read_artifact(**state['candidate'])
        except (ValueError, OSError, KeyError) as exc:
            raise TaskError('artifact_integrity', '登记的 XML 文件缺失或已损坏。') from exc

    def import_owl(self, *, content, name, mode, request_id):
        _text(name, '本体名称', 256)
        try:
            consumer.load_ontology_bytes(content, mode=mode)
        except (ValueError, TypeError) as exc:
            raise TaskError('invalid_ontology', 'OWL 文件损坏、模式不匹配或不符合平台契约。') from exc
        with self._atomic() as conn:
            replay = self._request(conn, 'import', request_id, 'import',
                {'name': name, 'mode': mode, 'sha256': digest(content)})
            if replay is not None:
                return replay
            asset = self.assets._create_asset(conn, name, 'robotics')
            source = self.assets._put_source(conn, content, role='imported_ontology', media_type='application/rdf+xml')
            version = self.assets._add_version(conn, asset['ontology_id'], content, payload_kind='robotics_rdf',
                source_asset_ids=[source['source_asset_id']], metadata={'mode': mode, 'profile_id': 'ur_pick_place',
                'build_recipe': 'owl_import'})
            result = {'ontology_id': asset['ontology_id'], 'version_id': version['version_id'], 'mode': mode}
            return self._reply(conn, 'import', request_id, result)

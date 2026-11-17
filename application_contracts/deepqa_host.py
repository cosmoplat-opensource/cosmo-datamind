"""Shared QA admission, content-bound cache and legacy deletion fence — DR-156."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from . import evaluate
from .adapters import registry
from .deepqa import DataQAAdapter
from .models import ContractError, digest, require


def _lifecycle(host):
    path = Path(host.WORK) / 'qa_deleted_graphs.json'
    if not path.exists():
        return {'schema_version': 1, 'graphs': {}}
    try:
        value = json.loads(path.read_text())
        if type(value['schema_version']) is not int or value['schema_version'] != 1 or not isinstance(value['graphs'], dict):
            raise ValueError('invalid lifecycle schema')
        for key, state in value['graphs'].items():
            if host._bad_gkey(key) or type(state['deleted']) is not bool or type(state['revision']) is not int or state['revision'] < 0:
                raise ValueError('invalid lifecycle entry')
        return value
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        raise ContractError('lifecycle_unavailable', '本体删除记录暂不可用，请稍后重试。') from None


def record_delete(host, key):
    """Called under the same write lock as SQL admission, before the file is removed."""
    # Some legacy filenames are not legal QA graph keys. Their deletion remains compatible;
    # admission rejects those keys directly, so they must not poison the QA journal.
    if host._bad_gkey(key):
        return
    import fcntl
    # Gunicorn workers have separate RLocks: serialize journal read/modify/replace across processes.
    with (Path(host.WORK) / 'qa_deleted_graphs.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            value = _lifecycle(host)
            previous = value['graphs'].get(key, {'revision': 0})
            value['graphs'][key] = {'deleted': True, 'revision': previous['revision'] + 1}
            host._atomic_json(str(Path(host.WORK) / 'qa_deleted_graphs.json'), value)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _identities(host, keys, body):
    raw = body.get('graphs', []) if body else keys
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(k, str) or host._bad_gkey(k.strip()) for k in raw):
        raise ContractError('invalid_scope', '问数图谱范围无效，请重新选择本体。')
    state = _lifecycle(host)
    import asset_lifecycle
    state=deepcopy(state)
    for key in keys:
        life=asset_lifecycle.legacy_state(host,key)
        if life is not None:
            old=state['graphs'].get(key,{'revision':0})
            state['graphs'][key]={'deleted':life['state']=='deleted','revision':life['revision'],
                                   'legacy_revision':old['revision']}
    if any(state['graphs'].get(k, {}).get('deleted') for k in keys):
        raise ContractError('ontology_deleted', '所选本体已删除，请选择其它本体。')
    refs = body.get('asset_refs') if body else None
    identifiers = list(keys)
    if refs is not None:
        if not isinstance(refs, list) or any(not isinstance(r, dict) for r in refs):
            raise ContractError('invalid_scope', '数据本体引用无效。')
        identifiers += [r.get('ontology_id') for r in refs]
    configured = host.app.config.get('DATAMIND_DOMAIN_STORAGE_ROOT') or host.os.environ.get('DATAMIND_DOMAIN_STORAGE_ROOT')
    path = (Path(configured) if configured else Path(host.WORK) / 'domain_assets') / 'domain.sqlite3'
    if path.exists():
        try:
            with sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True) as conn:
                for key in identifiers:
                    if not isinstance(key, str):
                        raise ContractError('invalid_scope', '数据本体引用无效。')
                    row = conn.execute('SELECT domain FROM assets WHERE ontology_id=? UNION '
                                       'SELECT a.domain FROM versions v JOIN assets a USING(ontology_id) WHERE v.version_id=?',
                                       (key, key)).fetchone()
                    if row and row[0] != 'data':
                        raise ContractError('unsupported_payload', '机器人本体不能进入数据问数，请选择数据本体。')
                    if row:
                        raise ContractError('unsupported_asset_reference', '此处需要数据图谱 key，请从现有数据图谱入口选择本体。')
        except sqlite3.Error:
            raise ContractError('resolution_failed', '本体登记信息暂不可用。') from None
    if refs is not None:
        raise ContractError('unsupported_asset_reference', '请从现有数据图谱入口选择本体后问数。')
    return {key: state['graphs'].get(key, {'deleted': False, 'revision': 0}) for key in keys}


def _catalog(host):
    """Read schema, never business rows; schema changes matter even if stat is preserved."""
    schemas = []
    for filename in (host.DB, host.UPLOAD_DB):
        path = Path(filename)
        if not path.exists():
            schemas.append(None)
            continue
        try:
            conn = host.ro_connect(str(path))
            try:
                schemas.append([list(r) for r in conn.execute(
                    "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name,type")])
            finally:
                conn.close()
        except (OSError, sqlite3.Error):
            schemas.append({'unavailable': True})
    return digest({'schema': schemas, 'files': [list(x) for x in host._stat_sig(host.DB, host.UPLOAD_DB)]})


class HostAdapter(DataQAAdapter):
    """Preserve legacy default/demo/fallback exemptions without relaxing SQL validation."""
    def _schema(self, data):
        info = data['environment']['deep_qa']
        if info['legacy_exempt'] and not info['profile']['bound_tables']:
            return [{'check_code': 'data.bindings', 'status': 'passed', 'applicable': False,
                     'reason': '沿用默认示例或已记录回退；具体 SQL 仍需校验。', 'issues': []}]
        return super()._schema(data)

    def _execution(self, data):
        info = data['environment']['deep_qa']
        if info['legacy_exempt'] and not info['profile']['queryable']:
            return [{'check_code': 'data.queryable', 'status': 'passed', 'applicable': False,
                     'reason': '沿用默认示例或已记录回退；具体 SQL 仍需校验。', 'issues': []}]
        return super()._execution(data)


class RequestGate:
    """One request's frozen scope. Every reuse/admission resolves current facts again."""
    def __init__(self, host, graphs, focus, question, *, body=None):
        self.host, self.graphs, self.focus, self.question = host, graphs, focus, question
        self.body = body
        self.mode = host.os.environ.get('DATAMIND_DEEPQA_CONTRACT_MODE', 'enforce').lower()
        if self.mode not in ('enforce', 'shadow', 'off'):
            self.mode = 'enforce'
        self.adapter = HostAdapter(host)
        self.data, self.legacy_error = self._resolve()
        self.report = self._evaluate(self.data)
        self.basis = digest(self.report['basis'])
        self.comparison = {'legacy_blocked': bool(self.legacy_error),
                           'contract_blocked': self.report['preparation_status'] != 'available'}
        if self.mode == 'shadow':
            host.logging.info('deepqa_contract_shadow legacy_blocked=%s contract_blocked=%s',
                              self.comparison['legacy_blocked'], self.comparison['contract_blocked'])

    def _resolve(self):
        try:
            return self._resolve_current()
        except ContractError:
            raise
        except Exception:
            raise ContractError('resolution_failed', '无法取得当前本体或数据连接，请稍后重试。') from None

    def _resolve_current(self):
        host = self.host
        with host._WRITE_LOCK:
            state = _identities(host, self.graphs, self.body)
            # Recompute inventory/columns rather than trusting process caches across connection changes.
            host._INV_CACHE['sig'] = None
            host._COLS_CACHE.clear()
            ir, actual, fallback, profile = host._qa_anchor_ir(self.graphs)
            ir, profile = deepcopy(ir), {**host._qa_graph_profile(ir), **deepcopy(profile)}
            snapshots = []
            for key in actual:
                effective = host.load_ir_edited(key) or host.load_ir(key) or ir
                snapshots.append(self.adapter._snapshot(key, effective))
            info = {'requested_keys': list(self.graphs), 'actual_keys': list(actual), 'fallback': fallback,
                    'ir': ir, 'profile': profile, 'focus_tables': list(self.focus),
                    'strict': bool(self.graphs and actual != ['demo'] and not fallback),
                    'metric_cards': host._metric_cards(self.question, ir),
                    'legacy_exempt': not self.graphs or actual == ['demo'] or bool(fallback)}
            data = {'snapshots': snapshots,
                    'task': {'session_id': 'data-question', 'revision': 0,
                             'content': {'question': self.question, 'plan': None}},
                    'context': {'mode': 'real', 'generation_policy': 'offline'},
                    'environment': {'adapter_revision': 'data-deep-qa-host-1', 'deep_qa': info,
                                    'connections': _catalog(host), 'lifecycle': state,
                                    'edits_revision': digest([list(x) for x in host._stat_sig(
                                        *[host._edits_path(key) for key in actual])])}}
            legacy = host._qa_scope_error(self.graphs, actual, fallback, profile)
            return data, legacy

    def _evaluate(self, data):
        return evaluate(registry=registry(data_qa=self.adapter), entrypoint='data.ask', **data)

    @property
    def info(self):
        return self.data['environment']['deep_qa']

    @property
    def blocked(self):
        # Keep legacy fallback/demo exemptions; shadow/off never relax the existing scope guard.
        return bool(self.legacy_error) or (self.mode == 'enforce' and self.report['preparation_status'] != 'available')

    def public(self):
        return {key: deepcopy(self.report[key]) for key in ('application_id', 'contract_version',
                'preparation_status', 'execution_status', 'execution_readiness', 'issues', 'allowed_actions')} | {
                    'basis_sha256': self.basis, 'comparison': self.comparison, 'mode': self.mode}

    def failure(self, exc=None):
        if exc:
            return {'error': str(exc), 'code': exc.code, 'application_check': self.public()}
        return {'error': self.legacy_error or '本体尚未满足问数要求，请检查表绑定或数据连接。',
                'code': 'ontology_not_queryable', 'application_check': self.public(),
                'ontology': {'keys': self.info['actual_keys'], **self.info['profile']}}

    def fresh(self):
        data, _ = self._resolve()
        current = self._evaluate(data)
        if digest(current['basis']) != self.basis:
            raise ContractError('stale_result', '问数期间本体或连接已变化，请重新发起查询。')
        return data

    def validate_sql(self, sql):
        data = self.fresh()
        data['task']['content']['plan'] = {'analyses': [{'sql': sql}]}
        self.report = self._evaluate(data)
        return self.report['execution_status'] == 'ready'

    def cached(self, answer):
        with self.host._WRITE_LOCK:
            return self._cached(answer)

    def _cached(self, answer):
        results = answer.get('results') or []
        if not results or any(not isinstance(r.get('sql'), str) for r in results):
            return False
        for result in results:
            if not self.validate_sql(result['sql']):
                return False
        return True

    @contextmanager
    def execution(self, sql):
        # Legacy delete/edit operations use this same lock. Admission and this SQL are indivisible.
        with self.host._WRITE_LOCK:
            if not self.validate_sql(sql):
                raise ContractError('application_not_ready', '查询未通过当前本体检查，请重新生成计划。')
            yield


def failure(exc):
    """Stable early failure before a gate/report can be constructed."""
    return {'error': str(exc), 'code': exc.code,
            'application_check': {'application_id': 'data.deep_qa', 'contract_version': '1',
                'preparation_status': 'blocked', 'execution_status': 'blocked',
                'execution_readiness': 'not_verified', 'issues': [], 'allowed_actions': [],
                'basis_sha256': None}}


def cache_key(host, question, history, focus, graphs, gate=None):
    if gate is None:
        try:
            gate = RequestGate(host, graphs or [], focus or [], question)
        except ContractError as exc:
            return digest({'question': question, 'graphs': graphs, 'unavailable': exc.code})
    return digest({'question': question, 'history': (history or [])[-2:], 'focus': focus or [],
                   'graphs': graphs or [], 'basis': gate.basis})


class DataCheckResolver:
    """Bridge the existing durable check API for fixed data versions, with no implicit import."""
    def __init__(self, root, host):
        from .adapters import Resolver
        self.root, self.host, self.robotics = root, host, Resolver(root)

    def resolve(self, *, entrypoint, asset_refs, context, _history=False):
        if entrypoint != 'data.ask':
            return self.robotics.resolve(entrypoint=entrypoint, asset_refs=asset_refs, context=context, _history=_history)
        require(context.get('task_ref') is None)
        from ontology_assets import AssetStore
        with self.host._WRITE_LOCK:
            self.host._INV_CACHE['sig'] = None
            adapter = DataQAAdapter(self.host, assets=AssetStore(self.root, read_only=True),allow_deleted=_history)
            data = adapter.resolve(asset_refs=asset_refs)
            data['context']['mode'] = context['mode']
            data['environment']['connections'] = _catalog(self.host)
            return data

    def peek(self, report):
        if report['entrypoint'] != 'data.ask':
            return self.robotics.peek(report)
        return self.resolve(entrypoint='data.ask', asset_refs=report['asset_refs'], context=report['context'],_history=True)

    def resource_revision(self, target):
        return self.robotics.resource_revision(target)

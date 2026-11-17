"""Transactional reversible asset lifecycle and legacy snapshots — DR-075."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import sqlite3
import uuid

from srv_context import _RDF_LOCK
from .payloads import json_text, validate_payload
from .storage import Storage, digest
from .store import AssetStore


class LifecycleError(ValueError):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or {
            'invalid_request': '生命周期请求参数无效。', 'not_found': '本体或旧图记录不存在。',
            'ontology_deleted': '本体已移到回收站，请恢复并重新检查后继续。',
            'confirmation_required': '请先预览影响并明确确认。', 'protected_asset': '内置本体不能移到回收站。',
            'revision_conflict': '本体状态已变化，请刷新预览。', 'impact_changed': '本体引用已变化，请刷新预览。',
            'state_conflict': '当前本体状态不允许此操作。', 'request_conflict': '请求编号已用于不同操作。',
            'asset_busy': '本体仍有排队或运行中的任务，请完成后重试。',
            'reference_unavailable': '无法安全核对本体引用，请检查存储后重试。',
            'storage_unavailable': '本体生命周期存储暂不可用。', 'read_only': '只读存储不能修改本体状态。',
            'legacy_changed': '读取期间旧本体或编辑已变化，请重新预览。',
            'legacy_snapshot_unavailable': '旧本体快照不可用，需要从备份恢复。',
        }.get(code, '本体生命周期检查未通过。'))


def require(condition, code='invalid_request'):
    if not condition: raise LifecycleError(code)


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value)) and '..' not in value


def tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def state_in(conn, oid):
    require(identifier(oid))
    require(conn.execute('SELECT 1 FROM assets WHERE ontology_id=?', (oid,)).fetchone() is not None, 'not_found')
    if 'asset_lifecycle' not in tables(conn):
        return {'ontology_id': oid, 'state': 'active', 'revision': 0, 'protected': False}
    row = conn.execute('SELECT state,revision,protected FROM asset_lifecycle WHERE ontology_id=?', (oid,)).fetchone()
    protected = conn.execute("SELECT 1 FROM legacy_assets WHERE ontology_id=? AND legacy_key IN ('demo','app')", (oid,)).fetchone()
    if row is None:
        return {'ontology_id': oid, 'state': 'active', 'revision': 0, 'protected': bool(protected)}
    require(row['state'] in ('active','deleted') and type(row['revision']) is int and row['revision'] >= 0
            and row['protected'] in (0,1), 'reference_unavailable')
    return {'ontology_id': oid, 'state': row['state'], 'revision': row['revision'], 'protected': bool(row['protected'] or protected)}


def require_active(conn, oid):
    state = state_in(conn, oid)
    require(state['state'] == 'active', 'ontology_deleted')
    return state


def references(value, oid, version_ids=()):
    """Only explicit ID fields are references; ordinary text never owns an asset."""
    if isinstance(value, dict):
        if value.get('ontology_id') == oid or (isinstance(value.get('version_id'),str) and value['version_id'] in version_ids): return True
        return any(references(v, oid, version_ids) for v in value.values())
    if isinstance(value, list): return any(references(v, oid, version_ids) for v in value)
    return False


def decode(raw):
    try: return json.loads(raw)
    except (ValueError, TypeError): raise LifecycleError('reference_unavailable') from None


def _impact(conn, oid):
    versions = [dict(r) for r in conn.execute('SELECT * FROM versions WHERE ontology_id=? ORDER BY rowid', (oid,))]
    vids = {v['version_id'] for v in versions}; known = tables(conn)
    all_versions = [dict(r) for r in conn.execute('SELECT ontology_id,version_id,source_asset_ids FROM versions ORDER BY rowid')]
    def source_refs(row):
        value=decode(row['source_asset_ids'])
        require(isinstance(value,list) and all(isinstance(s,str) for s in value),'reference_unavailable')
        return value
    for row in all_versions:source_refs(row)
    source_ids = sorted({s for v in versions for s in source_refs(v)})
    sources = [{'source_asset_id': sid, 'other_asset_count': len({v['ontology_id'] for v in all_versions
        if v['ontology_id'] != oid and sid in decode(v['source_asset_ids'])})} for sid in source_ids]
    sessions, basis, design_ids = [], {}, set()
    for table, key in [('robotics_task_sessions','session_id'), ('design_sessions','session_id'),
                        ('robotics_adaptations','adaptation_id')]:
        if table not in known: continue
        records = []
        for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid'):
            doc = decode(row['record'])
            require(isinstance(doc,dict),'reference_unavailable')
            if references(doc, oid, vids):
                records.append(dict(row)); sid = row[key]
                sessions.append({'kind': table, 'id': sid, 'status': doc.get('status'),
                                 'revision': doc.get('revision', (doc.get('state') or {}).get('revision'))})
                if table == 'design_sessions': design_ids.add(sid)
        basis[table] = records
    runs, run_basis, blockers = [], [], []
    for row in conn.execute('SELECT * FROM runs ORDER BY rowid'):
        doc = dict(row)
        for field in ('build_input','design_input','output_asset_ref','session_ref'):
            doc[field] = decode(doc[field]) if doc.get(field) is not None else None
            require(doc[field] is None or isinstance(doc[field],dict),'reference_unavailable')
        linked = (doc.get('session_ref') or {}).get('id') in design_ids
        if doc['ontology_id'] == oid or references(doc, oid, vids) or linked:
            runs.append({'run_id': doc['run_id'], 'status': doc['status']}); run_basis.append(dict(row))
            if doc['status'] in ('queued','running'): blockers.append({'code':'asset_busy','run_id':doc['run_id']})
    for session in sessions:
        if session['status'] in ('queued','running'):
            blockers.append({'code':'asset_busy','session_id':session['id']})
    for table in ('governance_heads','governance_events','evidence_revocations','application_checks'):
        if table not in known: continue
        relevant = []
        for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid'):
            data = dict(row)
            if table == 'application_checks': matched = references(decode(data['report']), oid, vids)
            else: matched = data.get('ontology_id') == oid
            if matched: relevant.append(data)
        basis[table] = relevant
    keys = [r[0] for r in conn.execute('SELECT legacy_key FROM legacy_assets WHERE ontology_id=? ORDER BY legacy_key', (oid,))]
    impact = {'version_ids': [v['version_id'] for v in versions], 'sources': sources,
              'runs': runs, 'sessions': sessions, 'legacy_keys': keys}
    # Source owner identities matter even when the displayed count happens to be unchanged.
    owners = [v for v in all_versions if any(s in source_ids for s in decode(v['source_asset_ids']))]
    return impact, blockers, {'versions': versions, 'owners': owners, 'runs': run_basis, 'records': basis}


class LifecycleService:
    def __init__(self, root, *, protected_asset_ids=(), read_only=False):
        self.read_only = read_only
        try:
            self.storage = Storage(root, read_only=read_only)
            if protected_asset_ids:
                require(not read_only, 'read_only')
                with self._atomic(write=True) as conn:
                    for oid in protected_asset_ids:
                        state = state_in(conn, oid)
                        conn.execute('INSERT INTO asset_lifecycle VALUES (?,?,?,1) ON CONFLICT(ontology_id) '
                                     'DO UPDATE SET protected=1', (oid,state['state'],state['revision']))
        except (OSError, sqlite3.Error, ValueError) as exc:
            if isinstance(exc, LifecycleError): raise
            raise LifecycleError('storage_unavailable') from None

    @contextmanager
    def _atomic(self, *, write=False):
        require(not write or not self.read_only, 'read_only')
        try:
            with _RDF_LOCK, self.storage.transaction(write=write) as conn: yield conn
        except (OSError, sqlite3.Error): raise LifecycleError('storage_unavailable') from None

    def get(self, oid):
        with self._atomic() as conn: return state_in(conn, oid)

    def events(self, oid):
        with self._atomic() as conn:
            state_in(conn, oid)
            return [decode(r[0]) for r in conn.execute('SELECT record FROM asset_lifecycle_events WHERE ontology_id=? ORDER BY rowid', (oid,))]

    def list_assets(self, state='active'):
        require(state in ('active','deleted',None))
        with self._atomic() as conn:
            result = []
            for row in conn.execute('SELECT * FROM assets ORDER BY rowid'):
                life = state_in(conn, row['ontology_id'])
                if state is None or state == life['state']: result.append({**dict(row), 'lifecycle':life})
            return result

    def _preview(self, conn, oid, operation):
        require(operation in ('delete','restore'))
        life = state_in(conn, oid)
        impact, busy, basis = _impact(conn, oid)
        allowed_state = life['state'] == ('active' if operation == 'delete' else 'deleted')
        blocked = ([{'code':'protected_asset'}] if life['protected'] else []) + (busy if operation == 'delete' else [])
        return {'ontology_id':oid, 'operation':operation, **{k:life[k] for k in ('state','revision','protected')},
                'can_apply':allowed_state and not blocked, 'impact':impact, 'blockers':blocked,
                'impact_sha256':digest(json_text({'lifecycle':life,'operation':operation,'impact':impact,'basis':basis}).encode())}

    def preview(self, oid, operation='delete'):
        with self._atomic() as conn: return self._preview(conn, oid, operation)

    def _change(self, oid, operation, *, expected_revision, impact_sha256, request_id, confirmed, reason=''):
        require(identifier(oid) and identifier(request_id))
        require(type(confirmed) is bool and confirmed, 'confirmation_required')
        require(type(expected_revision) is int and expected_revision>=0)
        require(isinstance(impact_sha256,str) and bool(re.fullmatch('[a-f0-9]{64}', impact_sha256)))
        require(isinstance(reason,str) and len(reason)<=1000 and not any(ord(c)<32 for c in reason))
        args = {'operation':operation,'expected_revision':expected_revision,'impact_sha256':impact_sha256,
                'request_id':request_id,'confirmed':confirmed,'reason':reason}
        fingerprint = digest(json_text(args).encode())
        with self._atomic(write=True) as conn:
            current = state_in(conn, oid)
            old = conn.execute('SELECT * FROM asset_lifecycle_requests WHERE ontology_id=? AND request_id=?',(oid,request_id)).fetchone()
            if old:
                require(old['fingerprint']==fingerprint, 'request_conflict')
                return {'operation':decode(old['operation']), 'lifecycle':current, 'replayed':True}
            require(not current['protected'], 'protected_asset')
            require(current['state']==('active' if operation=='delete' else 'deleted'), 'state_conflict')
            require(current['revision']==expected_revision, 'revision_conflict')
            preview = self._preview(conn, oid, operation)
            require(not any(b['code']=='asset_busy' for b in preview['blockers']), 'asset_busy')
            require(preview['impact_sha256']==impact_sha256, 'impact_changed')
            if operation == 'restore':
                for row in conn.execute('SELECT path,payload_sha256 FROM versions WHERE ontology_id=?',(oid,)):
                    try: self.storage.read_bytes(row['path'],row['payload_sha256'])
                    except (OSError,ValueError): raise LifecycleError('legacy_snapshot_unavailable') from None
            new = {**current,'state':'deleted' if operation=='delete' else 'active','revision':current['revision']+1}
            conn.execute('INSERT INTO asset_lifecycle VALUES (?,?,?,?) ON CONFLICT(ontology_id) DO UPDATE SET '
                         'state=excluded.state,revision=excluded.revision,protected=excluded.protected',
                         (oid,new['state'],new['revision'],int(new['protected'])))
            event = {'operation_id':'life_'+uuid.uuid4().hex,'ontology_id':oid,'operation':operation,
                     'from_state':current['state'],'to_state':new['state'],'revision':new['revision'],
                     'request_id':request_id,'reason':reason,'impact_sha256':impact_sha256,
                     'created_at':datetime.now(timezone.utc).isoformat(),'origin':'local'}
            conn.execute('INSERT INTO asset_lifecycle_events VALUES (?,?,?)',(event['operation_id'],oid,json_text(event)))
            conn.execute('INSERT INTO asset_lifecycle_requests VALUES (?,?,?,?)',(oid,request_id,fingerprint,json_text(event)))
            return {'operation':event,'lifecycle':new,'replayed':False}

    def delete(self, oid, **args): return self._change(oid, 'delete', **args)
    def restore(self, oid, **args): return self._change(oid, 'restore', **args)

    def _legacy(self, conn, key):
        require(identifier(key))
        row = conn.execute('SELECT * FROM legacy_assets WHERE legacy_key=?',(key,)).fetchone()
        if row:
            return {'legacy_key':key,'ontology_id':row['ontology_id'],'version_id':row['version_id'],
                    'lifecycle':state_in(conn,row['ontology_id']),'restore_available':True}
        row = conn.execute('SELECT record FROM legacy_tombstones WHERE legacy_key=?',(key,)).fetchone()
        if row: return decode(row[0])
        raise LifecycleError('not_found')

    def resolve_legacy(self, key):
        with self._atomic() as conn: return self._legacy(conn,key)

    def bind_legacy(self, key, *, name, load_effective, provenance, deleted=False):
        require(identifier(key) and isinstance(name,str) and bool(name.strip()) and type(deleted) is bool)
        require(not self.read_only, 'read_only')
        if deleted: require(key not in ('demo','app'), 'protected_asset')
        with self._atomic() as conn:
            try:
                old = self._legacy(conn,key)
                if old['lifecycle']['state']=='deleted': return old
            except LifecycleError as exc:
                if exc.code!='not_found': raise
        missing = False
        try:
            before = json_text(provenance(key))
            content = json_text(load_effective(key)).encode()
            validate_payload('data','datamind_ir',content)
            require(before==json_text(provenance(key)), 'legacy_changed')
        except FileNotFoundError:
            require(deleted,'legacy_snapshot_unavailable'); missing = True
        except LifecycleError: raise
        except (OSError,ValueError,TypeError,KeyError): raise LifecycleError('legacy_snapshot_unavailable') from None
        with self._atomic(write=True) as conn:
            try:
                old = self._legacy(conn,key)
                if old['lifecycle']['state']=='deleted': return old
            except LifecycleError as exc:
                if exc.code!='not_found': raise
            if missing:
                result = {'legacy_key':key,'ontology_id':None,'version_id':None,
                          'lifecycle':{'ontology_id':None,'state':'deleted','revision':1,'protected':False},'restore_available':False}
                conn.execute('INSERT INTO legacy_tombstones VALUES (?,?)',(key,json_text(result)))
                return result
            metadata = {'legacy_key':key,'provenance':decode(before)}
            fingerprint = digest(content+b'\0'+json_text(metadata).encode())
            binding = conn.execute('SELECT * FROM legacy_assets WHERE legacy_key=?',(key,)).fetchone()
            assets = AssetStore.__new__(AssetStore); assets.storage = self.storage
            if not binding or binding['fingerprint']!=fingerprint:
                oid = binding['ontology_id'] if binding else assets._create_asset(conn,name,'data')['ontology_id']
                version = assets._add_version(conn,oid,content,payload_kind='datamind_ir',
                    parent_version_id=binding['version_id'] if binding else None,metadata=metadata)
                conn.execute('INSERT INTO legacy_assets VALUES (?,?,?,?) ON CONFLICT(legacy_key) DO UPDATE SET '
                             'version_id=excluded.version_id,fingerprint=excluded.fingerprint',(key,oid,version['version_id'],fingerprint))
            result = self._legacy(conn,key)
            if deleted:
                oid = result['ontology_id']
                conn.execute('INSERT INTO asset_lifecycle VALUES (?,\'deleted\',1,0) ON CONFLICT(ontology_id) '
                             'DO UPDATE SET state=\'deleted\',revision=revision+1',(oid,))
                result = self._legacy(conn,key)
            return result

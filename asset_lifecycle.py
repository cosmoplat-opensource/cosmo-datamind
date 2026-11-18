"""Host composition for lifecycle migration, legacy keys and HTTP — DR-157."""
from contextlib import nullcontext
from copy import deepcopy
import io
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from flask import Blueprint, jsonify, request, send_file

from ontology_assets.lifecycle import LifecycleError, LifecycleService, identifier, require


def root_for(host):
    configured=host.app.config.get('DATAMIND_DOMAIN_STORAGE_ROOT') or host.os.environ.get('DATAMIND_DOMAIN_STORAGE_ROOT')
    return Path(configured) if configured else Path(host.WORK)/'domain_assets'


def existing(root):
    return (Path(root)/'domain.sqlite3').is_file()


def binding(host,key):
    if not identifier(key):return None
    root=root_for(host)
    if not existing(root):return None
    try:return LifecycleService(root,read_only=True).resolve_legacy(key)
    except LifecycleError as exc:
        if exc.code=='not_found':return None
        raise


def enabled(root):
    if not existing(root):return False
    try:
        with sqlite3.connect((Path(root)/'domain.sqlite3').as_uri()+'?mode=ro',uri=True) as conn:
            known=conn.execute("SELECT 1 FROM sqlite_master WHERE name='asset_lifecycle_settings'").fetchone()
            # 只有迁移完成(ready)才启用版本化协议;中断的迁移不让未绑定的旧图落入 404。
            return bool(known and conn.execute("SELECT 1 FROM asset_lifecycle_settings WHERE name='ready'").fetchone())
    except sqlite3.Error:raise LifecycleError('storage_unavailable') from None


def legacy_state(host,key):
    found=binding(host,key)
    return found['lifecycle'] if found else None


def deleted(host,key):
    state=legacy_state(host,key)
    return bool(state and state['state']=='deleted')


def _snapshot(host, key):
    found = binding(host, key)
    if not found:
        return None
    require(found['lifecycle']['state'] == 'active', 'ontology_deleted')
    require(found['restore_available'], 'legacy_snapshot_unavailable')
    from ontology_assets import AssetStore
    assets = AssetStore(root_for(host), read_only=True)
    return assets, found, assets.get_version(found['ontology_id'], found['version_id'])


def base(host, key):
    """DR-158: immutable baseline, separate from the effective edited version."""
    snapshot = _snapshot(host, key)
    if snapshot is None:
        return None
    assets, found, version = snapshot
    saved = version['metadata'].get('provenance', {})
    if 'base_ir_sha256' in saved:
        return LifecycleService(root_for(host), read_only=True).load_base(saved['base_ir_sha256'])
    if isinstance(saved.get('base_ir'), dict):    # 早期版本把基线内联在元数据里
        return deepcopy(saved['base_ir'])
    # Compatibility with schema-9 snapshots created before baseline recording.
    if _hashes(host, key)['source_sha256'] == saved.get('source_sha256'):
        return _original(host, key)
    if saved.get('edit_count', 0) == 0:
        return assets.load_data_ir(found['ontology_id'], found['version_id'])
    require(False, 'legacy_changed')


def effective(host,key):
    snapshot = _snapshot(host, key)
    if snapshot is None:
        return None
    assets, found, version = snapshot
    if getattr(host, '_pinned', lambda k: False)(key):
        saved = version['metadata'].get('provenance', {})
        ops = saved.get('edit_ops', host._load_edits(key).get('ops', []))
        return host._replay_ir(key, base(host, key), ops[:host._IR_PIN.get()[key]])
    return assets.load_data_ir(found['ontology_id'],found['version_id'])


def _original(host,key):
    value = host._load_json(host._forged_path(key[7:])) if key.startswith('forged_') else host._load_ir_original(key)
    if not value:
        raise FileNotFoundError('missing legacy graph')
    return deepcopy(value)


def _raw(host,key):
    return host._replay_ir(key, _original(host, key), host._load_edits(key).get('ops', []))


def _hashes(host,key):
    # The canonical effective snapshot is the authority; stat alone never proves equality.
    if key.startswith('built_'):path=Path(host.WORK)/(key+'.json')
    elif key.startswith('forged_'):path=Path(host._forged_path(key[7:]))
    else:
        paths=host.IR_SOURCES.get(key,{}).get('paths',[])
        path=next((Path(p) for p in paths if Path(p).is_file()),Path(host.WORK)/'__missing__')
    edit=Path(host._edits_path(key))
    return {'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            'edits_sha256':hashlib.sha256(edit.read_bytes()).hexdigest() if edit.is_file() else None}


def provenance(host,key):
    return {**_hashes(host,key),
            'edit_count':len(host._load_edits(key).get('ops',[])),
            'edit_ops':deepcopy(host._load_edits(key).get('ops', [])),
            'base_ir':_original(host, key)}


def _drifted(host, key, found):
    """绑定后旧文件被改写(写入与绑定之间崩溃、绕过 _atomic_json 的写入者)时返回 True。"""
    if found['lifecycle']['state'] != 'active' or not found.get('version_id'):
        return False
    from ontology_assets import AssetStore
    saved = AssetStore(root_for(host), read_only=True).get_version(found['ontology_id'], found['version_id'])
    saved = saved['metadata'].get('provenance', {})
    try:
        current = _hashes(host, key)
    except (OSError, ValueError):
        return False
    with LifecycleService(root_for(host), read_only=True)._atomic() as conn:
        row = conn.execute('SELECT value FROM asset_lifecycle_settings WHERE name=?', ('accepted:' + key,)).fetchone()
    if row and json.loads(row[0]) == current:
        return False        # 恢复时已确认:快照优先于回收站期间改写的旧文件
    return any(saved.get(k) != current[k] for k in current)


def _rebind(host, service, key, name):
    found = service.resolve_legacy(key)
    if not _drifted(host, key, found):
        return
    service.bind_legacy(key, name=name, load_effective=lambda k: _raw(host, k), provenance=lambda k: provenance(host, k))
    _forget(service, key)


def _forget(service, key):
    with service._atomic(write=True) as conn:
        conn.execute('DELETE FROM asset_lifecycle_settings WHERE name=?', ('accepted:' + key,))


def _accept(host, oid):
    """恢复后以固定快照为准:记下此刻旧文件摘要,启动漂移检查不据此重新导入。"""
    service = LifecycleService(root_for(host))
    with service._atomic() as conn:
        keys = [r[0] for r in conn.execute('SELECT legacy_key FROM legacy_assets WHERE ontology_id=?', (oid,))]
    for key in keys:
        try:
            current = _hashes(host, key)
        except (OSError, ValueError):
            continue
        with service._atomic(write=True) as conn:
            conn.execute('INSERT OR REPLACE INTO asset_lifecycle_settings VALUES (?,?)', ('accepted:' + key, json.dumps(current)))


def _skipped(service, keys):
    with service._atomic(write=True) as conn:
        conn.execute("INSERT OR REPLACE INTO asset_lifecycle_settings VALUES ('skipped',?)",
                     (json.dumps(sorted(keys)),))


def skipped(host):
    """迁移时因内容无效而未绑定的旧图;它们沿用旧的删除协议。"""
    root = root_for(host)
    if not existing(root):
        return set()
    try:
        with LifecycleService(root, read_only=True)._atomic() as conn:
            row = conn.execute("SELECT value FROM asset_lifecycle_settings WHERE name='skipped'").fetchone()
        return set(json.loads(row[0])) if row else set()
    except (LifecycleError, ValueError, sqlite3.Error):
        return set()


def initialize(host):
    """Explicit startup/operator migration, never a GET side effect."""
    from application_contracts.deepqa_host import _lifecycle
    with host._WRITE_LOCK:
        ledger=_lifecycle(host)
        initialized=enabled(root_for(host))
        service=LifecycleService(root_for(host))
        with service._atomic(write=True) as conn:
            conn.execute("INSERT OR REPLACE INTO asset_lifecycle_settings VALUES ('migrating','1')")
        # Enumerating uses the original raw scanner; already deleted aliases are omitted but
        # their persisted bindings and the old ledger remain authoritative.
        with host.app.app_context():entries=host._robotics_legacy_catalog()
        names={r['key']:r['name'] for r in entries}
        bad=set()
        for key in sorted(set(names)|set(ledger['graphs'])):
            if not identifier(key):continue
            try:
                try:
                    service.resolve_legacy(key)
                    # 已绑定:来源或编辑摘要与快照不一致时补登新版本。
                    if initialized:_rebind(host,service,key,names.get(key,key))
                    continue
                except LifecycleError as exc:
                    if exc.code!='not_found':raise
                service.bind_legacy(key,name=names.get(key,key),load_effective=lambda k:_raw(host,k),
                    provenance=lambda k:provenance(host,k),deleted=ledger['graphs'].get(key,{}).get('deleted',False))
            except LifecycleError as exc:
                # 单张坏图(如缺 objects 列表)记下并跳过,不让整个迁移停在 migrating。
                if exc.code not in ('legacy_snapshot_unavailable','legacy_changed'):raise
                bad.add(key)
        _skipped(service,bad)
        with service._atomic(write=True) as conn:
            conn.execute("DELETE FROM asset_lifecycle_settings WHERE name='migrating'")
            conn.execute("INSERT OR REPLACE INTO asset_lifecycle_settings VALUES ('ready','1')")
        return service


def failure(exc):
    status={'invalid_request':400,'confirmation_required':400,'not_found':404,'protected_asset':403,
            'storage_unavailable':503,'reference_unavailable':503,'read_only':503}.get(exc.code,409)
    return jsonify(error={'code':exc.code,'message':str(exc)}),status


def invalidate(host):
    for name in ('_QA_CACHE', '_GRAPH_ROW_CACHE', '_SOURCE_KEYS_CACHE', '_DEMO_FITS', '_REV_CACHE'):
        cache = getattr(host, name, None)
        if cache is not None:
            cache.clear()


def legacy_delete(host,key,body):
    """Unmigrated IR028 hosts retain their versioned protocol; persisted v1 roots never do."""
    known=binding(host,key)
    if not known and (not enabled(root_for(host)) or key in skipped(host)):return None
    require(type(body.get('confirmed')) is bool and body['confirmed'],'confirmation_required')
    require(known is not None,'not_found')
    require(known['ontology_id'] is not None,'legacy_snapshot_unavailable')
    args={k:v for k,v in body.items() if k not in ('key','id')}
    validate_body(args)
    result=LifecycleService(root_for(host)).delete(known['ontology_id'],**args)
    invalidate(host)
    return {'ok':True,**result}


def validate_body(body):
    required={'expected_revision','impact_sha256','request_id','confirmed'}
    require(isinstance(body,dict) and required<=set(body)<=required|{'reason'})
    if request.headers.get('If-Match'):
        require(request.headers['If-Match']==f'"{body["expected_revision"]}"','revision_conflict')


def init_app(app, *, root_provider, host=None):
    bp=Blueprint('asset_lifecycle',__name__)
    bp.register_error_handler(LifecycleError,failure)
    app.register_error_handler(LifecycleError,failure)
    @bp.get('/api/asset-lifecycle/capabilities')
    def capabilities():return jsonify(enabled=enabled(root_provider()),logical_delete=True,physical_erase=False)
    def preview(oid,operation):
        require(request.get_json()=={})
        value=LifecycleService(root_provider(),read_only=True).preview(oid,operation)
        value['impact_totals']={k:len(v) for k,v in value['impact'].items()}
        value['has_more']={k:len(v)>100 for k,v in value['impact'].items()}
        value['impact']={k:v[:100] for k,v in value['impact'].items()}
        response=jsonify(value);response.set_etag(str(value['revision']));return response
    @bp.post('/api/ontology-assets/<oid>/deletion-preview')
    def deletion_preview(oid):return preview(oid,'delete')
    @bp.post('/api/ontology-assets/<oid>/restoration-preview')
    def restoration_preview(oid):return preview(oid,'restore')
    def change(oid,operation):
        body=request.get_json();validate_body(body)
        lock=host._WRITE_LOCK if host is not None else nullcontext()
        with lock:
            result=getattr(LifecycleService(root_provider()),operation)(oid,**body)
            if host is not None:
                if operation=='restore' and not result['replayed']:_accept(host,oid)
                invalidate(host)
        response=jsonify(result);response.set_etag(str(result['lifecycle']['revision']));return response
    @bp.post('/api/ontology-assets/<oid>/delete')
    def delete(oid):return change(oid,'delete')
    @bp.post('/api/ontology-assets/<oid>/restore')
    def restore(oid):return change(oid,'restore')
    @bp.get('/api/ontology-assets/<oid>/versions/<vid>/data-payload')
    def data_payload(oid,vid):
        from ontology_assets import AssetStore
        assets=AssetStore(root_provider(),read_only=True)
        require(assets.get_asset(oid)['domain']=='data')
        # 回收站中的本体只在显式请求删除视图时可下载。
        if request.args.get('state')!='deleted':
            require(LifecycleService(root_provider(),read_only=True).get(oid)['state']=='active','ontology_deleted')
        return send_file(io.BytesIO(assets.read_payload(oid,vid)),mimetype='application/json',as_attachment=True,download_name=vid+'.json')
    app.register_blueprint(bp)


def write_graph(host,path,value,writer):
    """Explicit legacy edits publish a new fixed version; failed saves restore the old file."""
    path=Path(path)
    key=None;is_edit=False
    # 调用方可能传 abspath 或 realpath(confine);符号链接工作目录下两者不同,统一按解析后路径比较。
    real=Path(os.path.realpath(path))
    if real.parent==Path(os.path.realpath(host.WORK)):
        if path.name.startswith('edits_') and path.suffix=='.json':key=path.stem[6:];is_edit=True
        elif path.name.startswith('built_') and path.suffix=='.json':key=path.stem
        else:
            for alias in ('demo','app'):
                if real in [Path(os.path.realpath(p)) for p in host.IR_SOURCES.get(alias,{}).get('paths',[])]:key=alias;break
    if key is None and path.suffix=='.json' and real.parent in [Path(os.path.realpath(p)) for p in host._forged_dirs()]:key='forged_'+path.stem
    if key is None or not enabled(root_for(host)):return writer(str(path),value)
    with host._WRITE_LOCK:
        found=binding(host,key)
        if found:require(found['lifecycle']['state']=='active','ontology_deleted')
        original=path.read_bytes() if path.exists() else None
        try:
            writer(str(path),value)
            document=None
            baseline=None
            if is_edit and found:
                baseline=base(host,key)
                document=host._replay_ir(key, deepcopy(baseline), value.get('ops',[]))
            LifecycleService(root_for(host)).bind_legacy(key,name=(value.get('scenario') or {}).get('name',key),
                load_effective=lambda k:document if document is not None else _raw(host,k),
                provenance=lambda k:{**provenance(host,k), **({'base_ir':baseline} if baseline is not None else {})})
            _forget(LifecycleService(root_for(host)),key)
            invalidate(host)
        except BaseException:
            if original is None:path.unlink(missing_ok=True)
            else:
                from srv_context import _atomic_bytes
                _atomic_bytes(str(path),original)
            raise

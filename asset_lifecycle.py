"""Host composition for lifecycle migration, legacy keys and HTTP — DR-075."""
from contextlib import nullcontext
import io
import hashlib
import json
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
            return bool(known and conn.execute("SELECT 1 FROM asset_lifecycle_settings WHERE name IN ('ready','migrating')").fetchone())
    except sqlite3.Error:raise LifecycleError('storage_unavailable') from None


def legacy_state(host,key):
    found=binding(host,key)
    return found['lifecycle'] if found else None


def deleted(host,key):
    state=legacy_state(host,key)
    return bool(state and state['state']=='deleted')


def effective(host,key):
    found=binding(host,key)
    if not found:return None
    require(found['lifecycle']['state']=='active','ontology_deleted')
    require(found['restore_available'],'legacy_snapshot_unavailable')
    from ontology_assets import AssetStore
    return AssetStore(root_for(host),read_only=True).load_data_ir(found['ontology_id'],found['version_id'])


def _raw(host,key):
    if key.startswith('forged_'):
        path=host._forged_path(key[7:])
        value=host._load_json(path)
    else:value=host._load_ir_original(key)
    if not value:raise FileNotFoundError('missing legacy graph')
    value=json.loads(json.dumps(value))
    for op in host._load_edits(key).get('ops',[]):
        try:host.apply_any(value,op)
        except (ValueError,KeyError):pass
    return value


def provenance(host,key):
    # The canonical effective snapshot is the authority; stat alone never proves equality.
    if key.startswith('built_'):path=Path(host.WORK)/(key+'.json')
    elif key.startswith('forged_'):path=Path(host._forged_path(key[7:]))
    else:
        paths=host.IR_SOURCES.get(key,{}).get('paths',[])
        path=next((Path(p) for p in paths if Path(p).is_file()),Path(host.WORK)/'__missing__')
    edit=Path(host._edits_path(key))
    return {'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            'edits_sha256':hashlib.sha256(edit.read_bytes()).hexdigest() if edit.is_file() else None,
            'edit_count':len(host._load_edits(key).get('ops',[]))}


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
        for key in sorted(set(names)|set(ledger['graphs'])):
            if not identifier(key):continue
            if initialized:
                try:service.resolve_legacy(key);continue
                except LifecycleError as exc:
                    if exc.code!='not_found':raise
            service.bind_legacy(key,name=names.get(key,key),load_effective=lambda k:_raw(host,k),
                provenance=lambda k:provenance(host,k),deleted=ledger['graphs'].get(key,{}).get('deleted',False))
        with service._atomic(write=True) as conn:
            conn.execute("DELETE FROM asset_lifecycle_settings WHERE name='migrating'")
            conn.execute("INSERT OR REPLACE INTO asset_lifecycle_settings VALUES ('ready','1')")
        return service


def failure(exc):
    status={'invalid_request':400,'confirmation_required':400,'not_found':404,'protected_asset':403,
            'storage_unavailable':503,'reference_unavailable':503,'read_only':503}.get(exc.code,409)
    return jsonify(error={'code':exc.code,'message':str(exc)}),status


def legacy_delete(host,key,body):
    """Unmigrated IR028 hosts retain their versioned protocol; persisted v1 roots never do."""
    known=binding(host,key)
    if not enabled(root_for(host)) and not known:return None
    require(type(body.get('confirmed')) is bool and body['confirmed'],'confirmation_required')
    require(known is not None,'not_found')
    require(known['ontology_id'] is not None,'legacy_snapshot_unavailable')
    args={k:v for k,v in body.items() if k not in ('key','id')}
    validate_body(args)
    result=LifecycleService(root_for(host)).delete(known['ontology_id'],**args)
    host._QA_CACHE.clear();host._GRAPH_ROW_CACHE.clear()
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
                host._QA_CACHE.clear();host._GRAPH_ROW_CACHE.clear()
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
        return send_file(io.BytesIO(assets.read_payload(oid,vid)),mimetype='application/json',as_attachment=True,download_name=vid+'.json')
    app.register_blueprint(bp)


def write_graph(host,path,value,writer):
    """Explicit legacy edits publish a new fixed version; failed saves restore the old file."""
    path=Path(path)
    key=None;is_edit=False
    if path.parent==Path(host.WORK):
        if path.name.startswith('edits_') and path.suffix=='.json':key=path.stem[6:];is_edit=True
        elif path.name.startswith('built_') and path.suffix=='.json':key=path.stem
        else:
            for alias in ('demo','app'):
                if str(path) in host.IR_SOURCES.get(alias,{}).get('paths',[]):key=alias;break
    if key is None and path.suffix=='.json' and path.parent in [Path(p) for p in host._forged_dirs()]:key='forged_'+path.stem
    if key is None or not enabled(root_for(host)):return writer(str(path),value)
    with host._WRITE_LOCK:
        found=binding(host,key)
        if found:require(found['lifecycle']['state']=='active','ontology_deleted')
        original=path.read_bytes() if path.exists() else None
        try:
            writer(str(path),value)
            document=None
            if is_edit and found:
                from ontology_assets import AssetStore
                assets=AssetStore(root_for(host),read_only=True)
                version=assets.get_version(found['ontology_id'],found['version_id'])
                count=version['metadata'].get('provenance',{}).get('edit_count',0)
                ops=value.get('ops',[])
                if len(ops)>=count:
                    document=assets.load_data_ir(found['ontology_id'],found['version_id'])
                    for op in ops[count:]:host.apply_any(document,op)
                else:
                    for old in reversed(assets.list_versions(found['ontology_id'])):
                        if old['metadata'].get('provenance',{}).get('edit_count')==len(ops):
                            document=assets.load_data_ir(found['ontology_id'],old['version_id']);break
                    if document is None:
                        require(provenance(host,key)['source_sha256']==version['metadata'].get('provenance',{}).get('source_sha256'),'legacy_changed')
            LifecycleService(root_for(host)).bind_legacy(key,name=(value.get('scenario') or {}).get('name',key),
                load_effective=lambda k:document if document is not None else _raw(host,k),
                provenance=lambda k:provenance(host,k))
            host._GRAPH_ROW_CACHE.clear();host._QA_CACHE.clear()
        except BaseException:
            if original is None:path.unlink(missing_ok=True)
            else:
                from srv_context import _atomic_bytes
                _atomic_bytes(str(path),original)
            raise

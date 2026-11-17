"""Transaction-local lifecycle admission; history remains readable — DR-157."""
from copy import deepcopy
import json

from .lifecycle import LifecycleError, require_active, state_in, tables


def asset_ids(conn, value):
    found = set()
    def visit(node):
        if isinstance(node,dict):
            oid=node.get('ontology_id')
            if isinstance(oid,str) and conn.execute('SELECT 1 FROM assets WHERE ontology_id=?',(oid,)).fetchone():found.add(oid)
            vid=node.get('version_id')
            if isinstance(vid,str):
                row=conn.execute('SELECT ontology_id FROM versions WHERE version_id=?',(vid,)).fetchone()
                if row:found.add(row[0])
            for v in node.values():visit(v)
        elif isinstance(node,list):
            for v in node:visit(v)
    visit(value)
    return found


def guard_record(conn, record, *, old=None, bind=False):
    refs=asset_ids(conn,record)
    basis=(old if old is not None else record).get('_lifecycle_basis',{})
    previous=asset_ids(conn,old) if old is not None else refs
    current={}
    for oid in sorted(refs):
        state=require_active(conn,oid);current[oid]=state['revision']
        if oid in previous and basis.get(oid,0)!=state['revision']:
            raise LifecycleError('stale_lifecycle','本体经历过删除或恢复，请重新检查并明确新建任务。')
    if bind and any(current.values()):record['_lifecycle_basis']=current
    return current


def session_get(conn, record):
    if getattr(conn,'write_intent',False):guard_record(conn,record)
    return record


def session_put(conn, table, key, record):
    old=conn.execute(f'SELECT record FROM {table} WHERE {key}=?',(record[key],)).fetchone()
    if old:guard_record(conn,record,old=json.loads(old[0]),bind=True)
    else:
        current={oid:require_active(conn,oid)['revision'] for oid in asset_ids(conn,record)}
        if any(current.values()):record['_lifecycle_basis']=current


def project(conn, record):
    result=deepcopy(record)
    states=[state_in(conn,oid) for oid in sorted(asset_ids(conn,record))]
    changed=next((s for s in states if s['revision'] or s['state']=='deleted'),None)
    if changed:
        result['lifecycle']=changed
        result['write_frozen']=changed['state']=='deleted' or record.get('_lifecycle_basis',{}).get(changed['ontology_id'],0)!=changed['revision']
    return result


def run_guard(conn, run):
    if run.get('ontology_id'):require_active(conn,run['ontology_id'])
    for field in ('design_input','build_input','output_asset_ref'):
        for oid in asset_ids(conn,run.get(field)):require_active(conn,oid)
    ref=run.get('session_ref') or {}
    table={'robotics_design':'design_sessions','robotics_task':'robotics_task_sessions'}.get(ref.get('namespace'))
    if table and table in tables(conn):
        row=conn.execute(f'SELECT record FROM {table} WHERE session_id=?',(ref.get('id'),)).fetchone()
        if row:guard_record(conn,json.loads(row[0]))

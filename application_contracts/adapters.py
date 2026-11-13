"""Fixed-version diagnostics with explicit registered repair actions — DR-071 / DR-072."""
from copy import deepcopy
import json
import sqlite3
from . import Registry
from .models import ContractError, require


class Resolver:
    def __init__(self, root): self.root = root

    def resolve(self, *, entrypoint, asset_refs, context, _history=False):
        from ontology_assets import AssetStore
        assets = AssetStore(self.root, read_only=True)
        snapshots = []
        try:
            for ref in asset_refs:
                v = assets.get_version(**ref)
                snapshots.append({**ref, 'domain': assets.get_asset(ref['ontology_id'])['domain'],
                    'payload_kind': v['payload_kind'], 'payload': assets.read_payload(**ref), 'lifecycle_revision': 0})
            task = None; policy = 'offline'
            if context.get('task_ref'):
                with sqlite3.connect(self.root / 'domain.sqlite3') as conn:
                    row = conn.execute('SELECT record FROM robotics_task_sessions WHERE session_id=?',
                                       (context['task_ref']['session_id'],)).fetchone()
                if row is None: raise KeyError('task')
                state = json.loads(row[0]); policy = state['generation_policy']
                if not _history:
                    require(asset_refs == [{'ontology_id': state['ontology_id'], 'version_id': state['version_id']}])
                    require(context['mode'] == state['mode'])
                if state['task']:
                    task = {'session_id': state['session_id'], 'revision': state['revision'], 'content': state['task']}
            robotics={}
            version=assets.get_version(**asset_refs[0])
            from ontology_assets.robotics_application import is_application, ApplicationAssets
            if is_application(version):
                document=ApplicationAssets(self.root).detail(**asset_refs[0])
                robotics['application_origin']={'tbox':document['origin']['design_snapshot']['tbox'],
                    'role_map':document['role_map'],'session_ref':version['metadata'].get('origin_design_session')}
            doc=None
            if version['metadata'].get('design_contract'):
                from ontology_assets.robotics_design import DesignAssets
                doc=DesignAssets(self.root)._load(**asset_refs[0])[1]
                robotics['design']={'session_ref':doc['session_ref'],
                    'state':{'schema_confirmation':doc['state']['schema_confirmation']},
                    'abox':{'individuals':doc['abox']['individuals'][:1]}}
            with assets.storage.transaction() as conn:
                session_ref=doc['session_ref'] if doc else robotics.get('application_origin',{}).get('session_ref')
                if session_ref:
                    saved=conn.execute('SELECT record FROM design_sessions WHERE session_id=?',(session_ref['id'],)).fetchone()
                    robotics['design_revision']=json.loads(saved[0])['state']['revision']
                robotics['adaptations']=[{'id':r[0],'revision':json.loads(r[1])['revision']} for r in conn.execute(
                    "SELECT adaptation_id,record FROM robotics_adaptations WHERE json_extract(record,'$.asset_ref.ontology_id')=? AND json_extract(record,'$.asset_ref.version_id')=? ORDER BY rowid",
                    (asset_refs[0]['ontology_id'],asset_refs[0]['version_id']))]
                robotics['latest']=conn.execute('SELECT version_id FROM versions WHERE ontology_id=? ORDER BY rowid DESC LIMIT 1',(asset_refs[0]['ontology_id'],)).fetchone()[0]
            from ontology_assets.governance import GovernanceService
            revocations = [GovernanceService(self.root, read_only=True).revocations(r['ontology_id']) for r in asset_refs]
            return {'snapshots': snapshots, 'task': task, 'context': {'mode': context['mode'], 'generation_policy': policy},
                    'environment': {'adapter_revision': 'existing-consumer-1', 'revocations': revocations, 'robotics':robotics}}
        except KeyError:
            raise ContractError('not_found', '固定版本或任务不存在。') from None

    def peek(self, report):
        return self.resolve(entrypoint=report['entrypoint'], asset_refs=report['asset_refs'],
            context={'mode': report['context']['mode'], 'task_ref': None if report['task_ref'] is None else
                     {'session_id': report['task_ref']['session_id']}}, _history=True)

    def resource_revision(self, target):
        from .robotics_checks import resource_revision
        return resource_revision(self.root,target)


def registry(root=None):
    reg = Registry()
    from .robotics_checks import row, schema, execution, task, repair
    from functools import partial
    spec = {'application_id': 'robotics.pick_place', 'contract_version': '1',
        'entrypoints': ['robotics.generate_behavior_tree'], 'accepted_payload_kinds': ['robotics_rdf'],
        'asset_cardinality': {'min': 1, 'max': 1}, 'availability': 'available', 'executor_id': 'robotics.generate',
        'repair_handlers': {name:partial(repair,root=root) for name in ('robotics.revise_tbox','robotics.adapt_execution','robotics.complete_task')}, 'checkers': [
            {'layer': layer, 'required_codes': [code], 'required': True,
             'depends_on': {'execution':['schema.payload'], 'task':['schema.payload','execution.consumer']}.get(layer,[]), 'check': fn}
            for layer, code, fn in [('schema', 'schema.payload', schema),
                ('execution', 'execution.consumer', execution), ('task', 'task.configuration', task),
                ('environment', 'environment.local', lambda d: row('environment.local'))]]}
    reg.register(spec)
    spec = deepcopy(spec); spec.update(application_id='data.deep_qa', entrypoints=['data.ask'], availability='unavailable')
    reg.register(spec)
    return reg


def service(root, *, read_only=False):
    from .service import CheckService
    return CheckService(root, registry=registry(root), resolver=Resolver(root), read_only=read_only)

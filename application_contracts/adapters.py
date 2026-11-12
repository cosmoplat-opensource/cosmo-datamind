"""Initial existing-consumer adapter; no automatic domain repair — DR-071."""
from copy import deepcopy
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
                import sqlite3, json
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
            from ontology_assets.governance import GovernanceService
            revocations = [GovernanceService(self.root, read_only=True).revocations(r['ontology_id']) for r in asset_refs]
            return {'snapshots': snapshots, 'task': task, 'context': {'mode': context['mode'], 'generation_policy': policy},
                    'environment': {'adapter_revision': 'existing-consumer-1', 'revocations': revocations}}
        except KeyError:
            raise ContractError('not_found', '固定版本或任务不存在。') from None

    def peek(self, report):
        return self.resolve(entrypoint=report['entrypoint'], asset_refs=report['asset_refs'],
            context={'mode': report['context']['mode'], 'task_ref': None if report['task_ref'] is None else
                     {'session_id': report['task_ref']['session_id']}}, _history=True)

    def resource_revision(self, target):
        raise ContractError('repair_unavailable', '领域修复尚未接通。')


def registry():
    reg = Registry()
    def row(code, status='passed', issues=None):
        return [{'check_code': code, 'status': status, 'applicable': True, 'issues': issues or []}]
    def execution(data):
        from domains.robotics.consumer import load_ontology_bytes
        try:
            load_ontology_bytes(data['snapshots'][0]['payload'], mode=data['context']['mode'])
            return row('execution.consumer')
        except ValueError:
            return row('execution.consumer', 'unsupported', [{'code': 'execution.consumer_incompatible',
                'layer': 'execution', 'severity': 'blocking', 'message': '当前本体未满足现有行为树消费者要求。',
                'expected': '现有行为树执行结构', 'observed': '不兼容', 'entity_refs': [], 'evidence_refs': [], 'repair_actions': []}])
    def task(data):
        from rdflib import Graph
        from domains.robotics.task_completion import inspect_gaps
        g = Graph().parse(data=data['snapshots'][0]['payload'], format='xml')
        result = inspect_gaps(g, data['task']['content'], mode=data['context']['mode'],
            revocations=data['environment'].get('revocations', [[]])[0])
        ready = result['status'] == 'ready' or (data['context']['generation_policy'] == 'offline'
            and result['status'] == 'needs_information' and all(i['kind'] == 'applicability' for i in result['items']))
        return row('task.configuration', 'passed' if ready else 'missing')
    spec = {'application_id': 'robotics.pick_place', 'contract_version': '1',
        'entrypoints': ['robotics.generate_behavior_tree'], 'accepted_payload_kinds': ['robotics_rdf'],
        'asset_cardinality': {'min': 1, 'max': 1}, 'availability': 'available', 'executor_id': 'robotics.generate',
        'repair_handlers': {}, 'checkers': [
            {'layer': layer, 'required_codes': [code], 'required': True, 'depends_on': [], 'check': fn}
            for layer, code, fn in [('schema', 'schema.payload', lambda d: row('schema.payload')),
                ('execution', 'execution.consumer', execution), ('task', 'task.configuration', task),
                ('environment', 'environment.local', lambda d: row('environment.local'))]]}
    reg.register(spec)
    spec = deepcopy(spec); spec.update(application_id='data.deep_qa', entrypoints=['data.ask'], availability='unavailable')
    reg.register(spec)
    return reg


def service(root, *, read_only=False):
    from .service import CheckService
    return CheckService(root, registry=registry(), resolver=Resolver(root), read_only=read_only)

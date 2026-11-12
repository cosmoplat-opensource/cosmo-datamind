"""T04 integration fixtures: real SQLite/HTTP, synthetic checker facts only — DR-071."""
from copy import deepcopy
import importlib
import sqlite3

from flask import Flask
import pytest

from application_contracts import ContractError, Registry
from ontology_assets import AssetStore
from tests.application_contract_support import ENTRYPOINT, contract, inputs, problem, result
from tests.build_runtime_support import BASE_URL


def service_api():
    # Fail a collected test if absent; never skip or supply a fake service.
    return importlib.import_module('application_contracts.service')


def http_api():
    return importlib.import_module('bp_application_contracts')


class LocalResolver:
    """Trusted test adapter. Reads actual asset bytes; task/environment facts are synthetic."""
    def __init__(self, root):
        self.root = root
        self.data = inputs()
        self.calls = []
        self.peeks = []
        self.unknown = False
        self.target_revision = 3

    def resolve(self, *, entrypoint, asset_refs, context):
        self.calls.append(deepcopy((entrypoint, asset_refs, context)))
        return self._data(asset_refs, context)

    def _data(self, refs, context):
        data = deepcopy(self.data)
        assets = AssetStore(self.root, read_only=True)
        data['snapshots'] = []
        for ref in refs:
            try:
                version = assets.get_version(**ref)
                asset = assets.get_asset(ref['ontology_id'])
                raw = assets.read_payload(**ref)
            except KeyError as exc:
                raise ContractError('not_found', '固定版本不存在。') from exc
            data['snapshots'].append({**ref, 'domain': asset['domain'], 'payload_kind': version['payload_kind'],
                'payload': raw, 'lifecycle_revision': self.data['snapshots'][0]['lifecycle_revision']})
        data['context']['mode'] = context['mode']
        if context.get('task_ref') is None:
            data['task'] = None
        elif context['task_ref'] != {'session_id': data['task']['session_id']}:
            raise ContractError('not_found', '任务不存在。')
        return data

    def peek(self, report):
        self.peeks.append(report['check_id'])
        if self.unknown:
            return None
        return self._data(report['asset_refs'], {'mode': report['context']['mode'],
            'task_ref': None if report['task_ref'] is None else {'session_id': report['task_ref']['session_id']}})

    def resource_revision(self, target):
        assert target['resource_kind'] == 'design_session' and target['resource_id'] == 'design-1'
        return self.target_revision


class RepairRecorder:
    """Synthetic local repair effect uses the dispatcher's transaction; no ontology repair claimed."""
    def __init__(self, *, kind='navigate', fail=False):
        self.kind, self.fail, self.calls = kind, fail, []

    def __call__(self, command, conn):
        self.calls.append(deepcopy(command))
        conn.execute('INSERT INTO test_repair_effects VALUES (?)', (command['request_id'],))
        if self.fail:
            raise OSError('synthetic-private-endpoint-token')
        target = command['target']
        response = {'kind': self.kind, 'target': target, 'return_context': command['return_context']}
        if self.kind == 'navigate':
            response['href'] = '#build?recipe=robotics&session=' + target['resource_id']
        else:
            response['run_id'] = 'synthetic-repair-run'
        return response


def seeded(root):
    assets = AssetStore(root)
    asset = assets.create_asset(name='合成应用检查资产', domain='robotics')
    version = assets.add_version(asset['ontology_id'],
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>', payload_kind='robotics_rdf')
    return {k: version[k] for k in ('ontology_id', 'version_id')}


def request_body(ref, **changes):
    return {'entrypoint': ENTRYPOINT, 'asset_refs': [ref], 'context': {'mode': 'real'},
            'request_id': 'check-1', **changes}


def harness(root, *, overrides=None, repair=False, kind='navigate', fail_repair=False):
    ref = seeded(root)
    handler = RepairRecorder(kind=kind, fail=fail_repair)
    if repair:
        issue = problem(); issue['repair_actions'][0]['kind'] = kind
        overrides = {'execution': [result('execution', 'missing', issues=[issue])]}
    spec, recorders = contract(overrides=overrides,
        repair_handlers={'robotics.reextract': handler} if repair else None)
    reg = Registry(); reg.register(spec)
    resolver = LocalResolver(root)
    service = service_api().CheckService(root, registry=reg, resolver=resolver)
    if repair:
        with sqlite3.connect(root / 'domain.sqlite3') as conn:
            conn.execute('CREATE TABLE test_repair_effects(request_id TEXT PRIMARY KEY)')
    return service, resolver, reg, recorders, ref, handler


def app_for(root, *, registry=None, resolver=None, enabled=True):
    app = Flask('application-contract-integration')
    app.config.update(TESTING=True, MAX_CONTENT_LENGTH=4096)
    import server
    for guard in (server._host_guard, server._csrf_guard, server._json_object_guard):
        app.before_request(guard)
    http_api().init_app(app, root=root, registry=registry, resolver=resolver, enabled=enabled)
    return app, app.test_client()


def ok(response, status=200):
    assert response.status_code == status, response.get_data(as_text=True)
    assert response.is_json
    return response.get_json()


def error(response, status, code):
    body = ok(response, status)
    assert body['error']['code'] == code and body['error']['message']
    return body


def post(client, body, path='/api/application-checks', **kwargs):
    return client.post(path, json=body, base_url=BASE_URL, **kwargs)


def expect_error(code, call):
    with pytest.raises(ContractError) as caught:
        call()
    assert caught.value.code == code


def repair_args(report, **changes):
    issue = report['issues'][0]
    return {'check_id': report['check_id'], 'issue_id': issue['issue_id'], 'action_id': 'robotics.reextract',
        'input': {}, 'expected_revision': 3, 'request_id': 'repair-1', **changes}

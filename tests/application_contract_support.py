"""IR-025 core RED fixtures: synthetic checkers, never real application readiness — DR-071."""
from copy import deepcopy
import importlib
import socket

import pytest

ENTRYPOINT = 'robotics.generate_behavior_tree'
APP = 'robotics.pick_place'
LAYERS = ('schema', 'execution', 'task', 'environment')
CODES = dict(zip(LAYERS, ('schema.roles', 'execution.bindings', 'task.configuration', 'environment.dependencies')))


def api():
    # Import in the test body: absent implementation is a failed case, not collection error.
    return importlib.import_module('application_contracts')


@pytest.fixture
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Application contract tests must not access network or a paid model')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def problem(layer='execution', *, code='execution.binding_ambiguous', action='robotics.reextract'):
    return {'issue_id': 'gap-1', 'code': code, 'layer': layer, 'severity': 'blocking',
            'message': '参数绑定存在歧义', 'expected': '唯一的绑定来源', 'observed': ['port-a', 'port-b'],
            'entity_refs': ['urn:test:invocation-1'],
            'evidence_refs': [{'source_asset_id': 'source-1', 'node_path': '/root/BehaviorTree/Action[1]'}],
            'repair_actions': [{'action_id': action, 'label': '查看并修复提取', 'kind': 'navigate',
                                'availability': 'available',
                                'target': {'resource_kind': 'design_session', 'resource_id': 'design-1',
                                           'expected_revision': 3}}]}


def result(layer, status='passed', *, issues=None, **extra):
    return {'check_code': CODES[layer], 'status': status, 'applicable': True,
            'issues': deepcopy(issues or []), **extra}


class Recorder:
    """Only emits scripted observations; does not aggregate, authorize or calculate freshness."""
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, context):
        self.calls.append(deepcopy(context))
        if isinstance(self.response, Exception):
            raise self.response
        return deepcopy(self.response)


def contract(*, application_id=APP, entrypoint=ENTRYPOINT, overrides=None, repair_handlers=None):
    overrides = overrides or {}
    recorders = {layer: Recorder(overrides.get(layer, [result(layer)])) for layer in LAYERS}
    spec = {'application_id': application_id, 'contract_version': '1', 'entrypoints': [entrypoint],
            'accepted_payload_kinds': ['robotics_rdf'], 'asset_cardinality': {'min': 1, 'max': 1},
            'availability': 'available', 'executor_id': 'robotics.generate',
            'repair_handlers': repair_handlers or {},
            'checkers': [{'layer': layer, 'required_codes': [CODES[layer]], 'required': True,
                          'depends_on': [], 'check': recorders[layer]} for layer in LAYERS]}
    return spec, recorders


def inputs(*, with_task=True):
    return {'snapshots': [{'ontology_id': 'ont-a', 'version_id': 'ver-1', 'domain': 'robotics',
                           'payload_kind': 'robotics_rdf', 'payload': b'<rdf>synthetic snapshot</rdf>',
                           'lifecycle_revision': 0}],
            'task': {'session_id': 'task-1', 'revision': 2, 'content': {'operation': 'pick_place', 'part': 'part-a'}}
                    if with_task else None,
            'context': {'mode': 'real', 'generation_policy': 'offline'},
            'environment': {'revision': 'env-1', 'evidence_revision': 0}}


def registry(spec=None):
    reg = api().Registry()
    reg.register(contract()[0] if spec is None else spec)
    return reg


def evaluate(*, spec=None, data=None):
    return api().evaluate(registry=registry(spec), entrypoint=ENTRYPOINT,
                          **(inputs() if data is None else data))


def check(report, layer):
    return next(row for row in report['layers'][layer]['checks'] if row['check_code'] == CODES[layer])


def basis(data=None, *, application_id=APP, contract_version='1'):
    return api().make_basis(application_id=application_id, contract_version=contract_version,
                            **(inputs() if data is None else data))

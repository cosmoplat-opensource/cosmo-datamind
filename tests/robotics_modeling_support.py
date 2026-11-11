"""Synthetic proposals and frozen XML oracles for IR-024 T04 RED — DR-070."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import socket

import pytest

from tests.robotics_support import SOURCE, MODELS

NS = 'https://example.org/robotics-design#'
FIXTURES = Path(__file__).parent / 'fixtures' / 'robotics' / 'modeling'


def api(module):
    # Missing implementation fails a case, never collection or importorskip.
    return importlib.import_module('domains.robotics.modeling.' + module)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def sources():
    return {'behavior_tree': SOURCE.read_bytes(), 'node_models': MODELS.read_bytes()}


def bundle(values=None):
    return api('evidence').ingest_sources(sources() if values is None else values)


def vocabulary():
    """Small controlled semantics, independent of implementation's validator."""
    value = {'version': 'synthetic-v1', 'classes': [], 'object_properties': [], 'data_properties': []}
    for name in ('Skill', 'Atomic', 'Composite', 'Parameter', 'ReviewOnly'):
        value['classes'].append({'id': name, 'label': name, 'en_label': name, 'definition': name,
            'note': '', 'iri': NS + name, 'scope': 'scope_review' if name == 'ReviewOnly' else 'schema'})
    value['object_properties'] = [{'id': 'hasInput', 'label': '输入', 'definition': '输入参数',
        'iri': NS + 'hasInput', 'scope': 'schema', 'domain_ids': ['Atomic', 'Composite'],
        'range_ids': ['Parameter']}]
    value['data_properties'] = [{'id': 'stepIndex', 'label': '序号', 'definition': '步骤序号',
        'iri': NS + 'stepIndex', 'scope': 'schema', 'domain_ids': [], 'datatype': 'xsd:integer'}]
    value['sha256'] = digest(value)
    return value


def classes(eid='E00001', mid='M0001'):
    return {'reply': '合成类别提议，仅用于测试。',
        'competency_questions': [{'id': 'CQ1', 'question': '技能使用哪些参数？', 'source_refs': [mid]}],
        'classes': [{'id': name, 'label': name, 'definition': name,
                     'parent_ids': ['Skill'] if name in ('Atomic', 'Composite') else [],
                     'source_refs': [eid], 'cq_refs': ['CQ1']}
                    for name in ('Skill', 'Atomic', 'Composite', 'Parameter')], 'clarifications': []}


def properties(eid='E00001'):
    return {'reply': '合成关系提议，仅用于测试。', 'object_properties': [{
        'id': 'hasInput', 'label': '输入', 'definition': '输入参数',
        'domain_ids': ['Atomic', 'Composite'], 'range_ids': ['Parameter'],
        'source_refs': [eid], 'cq_refs': ['CQ1']}], 'data_properties': [{
        'id': 'stepIndex', 'label': '序号', 'definition': '步骤序号', 'domain_ids': [],
        'class_ids': ['Atomic'], 'datatype': 'xsd:integer', 'source_refs': [eid], 'cq_refs': ['CQ1']}],
        'clarifications': []}


def schema():
    return {'namespace': NS, 'classes': classes()['classes'],
            'object_properties': properties()['object_properties'],
            'data_properties': properties()['data_properties']}


def ur_schema():
    return json.loads((FIXTURES / 'ur_selected_tbox.json').read_text())


def confirmation(tbox, evidence):
    return {'tbox': deepcopy(tbox), 'sha256': digest(tbox),
        'source_sha256': {d['role']: d['sha256'] for d in evidence['documents']},
        'vocabulary_sha256': vocabulary()['sha256']}


class ScriptedRoute:
    """Only transport is synthetic; no parser/extractor/exporter replacement."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def public(self):
        return {'method': 'llm_tbox_abox', 'model': {
            'provider': 'openai', 'model': 'synthetic', 'configuration_id': 'test-model-id'}}

    def request(self, session_id, prompt, *, timeout):
        self.calls.append({'session_id': session_id, 'prompt': prompt, 'timeout': timeout})
        assert self.replies, 'Unexpected extra model request'
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result


def draft():
    tbox, evidence = api('tbox'), bundle()
    state = tbox.new_design(evidence, vocabulary(), namespace=NS)
    route = ScriptedRoute(classes(evidence['evidence'][0]['id']))
    return tbox.revise(state, '说明技能输入参数', route, session_id='design-test', timeout=7), route


def property_draft():
    tbox = api('tbox')
    state, _ = draft()
    state = tbox.confirm_classes(state, snapshot_sha256=state['snapshot_sha256'])
    return tbox.revise(state, '设计关系', ScriptedRoute(properties()), session_id='design-test', timeout=7)


@pytest.fixture(autouse=False)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Modeling core attempted network access')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)

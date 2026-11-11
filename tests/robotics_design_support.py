"""Real storage/core with synthetic runtime for IR-024 T06 RED — DR-070."""
import base64
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sqlite3

import pytest

from domain_runs import RunStore
from domain_runs.robotics_build_routing import resolve_route
from tests.build_pipeline_support import sha
from tests.robotics_dual_build_support import ScriptedRuntime, RuntimeRegistry, model_config
from tests.robotics_modeling_support import bundle, sources, ur_schema, no_network

__all__ = ['no_network']
SNAPSHOT5 = Path(__file__).parent / 'fixtures/domain_storage/schema5_snapshot.json'


def service_api():
    # Fail missing implementation at execution, not collection; no skip/xfail.
    return importlib.import_module('domain_runs.robotics_design_service')


def assets_api():
    return importlib.import_module('ontology_assets.robotics_design')


def proposals():
    selected = ur_schema()
    eid = bundle()['evidence'][0]['id']
    for group in ('classes', 'object_properties', 'data_properties'):
        for row in selected[group]:
            row.update(source_refs=[eid], cq_refs=['CQ1'])
    return ({'reply': '合成类别提议', 'classes': selected['classes'], 'clarifications': [],
        'competency_questions': [{'id': 'CQ1', 'question': '技能调用与参数是什么？', 'source_refs': ['M0001']}]},
        {'reply': '合成属性提议', 'clarifications': [],
         **{k: selected[k] for k in ('object_properties', 'data_properties')}})


def reply(value):
    return True, json.dumps(value, ensure_ascii=False)


def inputs(**changes):
    return {'name': '设计测试', 'recipe': 'bt_xml', 'profile_id': 'ur_pick_place',
        'request_id': 'submit', 'description': '',
        'sources': {role: {'filename': role + '.xml', 'content': raw} for role, raw in sources().items()},
        **changes}


class Harness:
    def __init__(self, root, *, max_pending=4):
        self.root = root
        self.runtime = ScriptedRuntime()
        self.config = model_config()
        self.service = service_api().DesignService(root, route_resolver=self.resolve, max_pending=max_pending)

    def resolve(self):
        return resolve_route(config=deepcopy(self.config), environ={}, registry=RuntimeRegistry(openai=self.runtime))

    def submit(self, **changes):
        return self.service.submit(**inputs(**changes))

    def run(self):
        claimed = self.service.claim_next()
        assert claimed is not None
        assert claimed['status'] == 'running'
        return self.service.execute(claimed['run_id'])

    def get(self, state):
        return self.service.get(state['session_id'])

    def message(self, state, **changes):
        return self.service.message(state['session_id'], text='设计关系与参数',
            expected_revision=state['revision'], expected_stage=state['stage'],
            request_id=f"message-{state['revision']}", **changes)

    def classes(self):
        self.runtime.responses.append(reply(proposals()[0]))
        state = self.submit(description='整理技能及调用参数')
        self.run()
        return self.get(state)

    def properties(self):
        state = self.classes()
        state = self.service.confirm_classes(state['session_id'], request_id='confirm-classes',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        self.runtime.responses.append(reply(proposals()[1]))
        self.message(state)
        self.run()
        return self.get(state)

    def candidate(self):
        state = self.properties()
        state = self.service.confirm_schema(state['session_id'], request_id='confirm-schema',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        self.run()
        return self.get(state)


def expect_error(code, call, status=None):
    with pytest.raises(ValueError) as caught:
        call()
    assert getattr(caught.value, 'code', None) == code, str(caught.value)
    if status is not None:
        assert caught.value.status_code == status
    return caught.value


def revisions(h, state):
    return h.service.list_revisions(state['session_id'])


def history_bytes(h, state):
    return {row['revision']: h.service.export_revision(state['session_id'], row['revision'], format='json')
            for row in revisions(h, state)}


def restore_v5(root):
    """Restore actual captured schema/rows/bytes without today's initializer."""
    fixture = json.loads(SNAPSHOT5.read_text())
    assert fixture['schema_version'] == 5
    root.mkdir(parents=True)
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        for entry in fixture['schema']:
            conn.execute(entry['sql'])
        for table, rows in fixture['rows'].items():
            for row in rows:
                fields = ','.join('"' + k + '"' for k in row)
                conn.execute(f'INSERT INTO "{table}" ({fields}) VALUES ({",".join("?" for _ in row)})',
                             tuple(row.values()))
        conn.execute('PRAGMA user_version=5')
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    for relative, item in fixture['blobs'].items():
        content = base64.b64decode(item['base64'])
        assert sha(content) == item['sha256']
        path = root / relative
        assert path.resolve().is_relative_to(root.resolve())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return fixture


def assert_no_candidate(h):
    with RunStore(h.root).storage.transaction() as conn:
        assert conn.execute('SELECT COUNT(*) FROM assets').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 0

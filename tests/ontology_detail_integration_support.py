"""Frozen real v1/v2/legacy/data fixtures for detail integration review — DR-155 / T04 RED."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3

from application_contracts.adapters import service as checks
from domain_runs.robotics_task_service import TaskService
from tests.asset_support import add_data
from tests.robotics_application_integration_support import adapted, fill_task
from tests.robotics_design_support import Harness, proposals, reply
from tests.robotics_task_support import fixture as legacy_fixture, message, model, start

ENTRY = 'robotics.generate_behavior_tree'
ROOT = Path(__file__).resolve().parents[1]


def ref(version):
    return {key: version[key] for key in ('ontology_id', 'version_id')}


def detail_path(version):
    return f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}"


def detail_hash(version):
    return '#library?asset=' + version['ontology_id'] + '&version=' + version['version_id']


def task_hash(version, **extra):
    from urllib.parse import urlencode
    return '#robotics-task?' + urlencode({'asset': version['ontology_id'], 'version': version['version_id'], 'mode': 'real', **extra})


def prepare_seed(root):
    """Real artifacts and software-only synthetic task data; no user files or model network."""
    assets, legacy, _ = legacy_fixture(root, ready=True)
    client, transport = model()
    tasks = TaskService(root, model_client=client, generation_policy='offline')
    legacy_task = message(tasks, start(tasks, legacy))
    assert legacy_task['status'] == 'generated' and len(transport.calls) == 1
    _, original, application = adapted(root)
    app_task, _ = fill_task(root, application['output_asset_ref'])
    assert app_task['status'] == 'generated'
    blocked = blocked_candidate(root)
    data_asset, data = add_data(assets, name='详情数据入口对照')
    return {'legacy': ref(legacy), 'legacy_task': legacy_task, 'design': original['output_asset_ref'],
            'application': application['output_asset_ref'], 'current_application': ref(app_task),
            'application_task': app_task, 'blocked': blocked['output_asset_ref'], 'blocked_session': blocked['session_id'],
            'data': ref(data), 'data_asset': data_asset}


def blocked_candidate(root):
    h = Harness(root)
    classes, properties = deepcopy(proposals())
    classes['classes'] = [row for row in classes['classes'] if row['id'] != 'ParameterBinding']
    for key in ('object_properties', 'data_properties'):
        properties[key] = [row for row in properties[key] if not any('ParameterBinding' in row.get(field, [])
            for field in ('domain_ids', 'range_ids', 'class_ids'))]
    h.runtime.responses.append(reply(classes))
    state = h.submit(description='合成测试：缺少参数绑定类别', request_id='detail-blocked-admission')
    h.run(); state = h.get(state)
    state = h.service.confirm_classes(state['session_id'], request_id='confirm-classes',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    h.runtime.responses.append(reply(properties)); h.message(state); h.run(); state = h.get(state)
    h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    h.run()
    return h.get(state)


def clone_seed(seed, target):
    source, info = seed
    shutil.copytree(source, target)
    return deepcopy(info)


def create_check(root, version, *, request_id='detail-check', task=None):
    context = {'mode': 'real'}
    if task is not None:
        context['task_ref'] = {'session_id': task['session_id']}
    return checks(root).evaluate(entrypoint=ENTRY, asset_refs=[ref(version)], context=context, request_id=request_id)


def stale_check(root, info):
    task = info['legacy_task']
    response = create_check(root, ref(task), request_id='historical-detail-check', task=task)
    assert response['report']['preparation_status'] == 'available' and response['freshness'] == 'current'
    # A real task revision changes the comparison basis; do not alter the stored report.
    TaskService(root).save_notes(task['session_id'], notes=[{'kind': 'grasp_compatibility', 'text': '合成测试：保留原任务输入'}],
        expected_revision=task['revision'], request_id='make-report-stale')
    current = checks(root).read(response['report']['check_id'])
    assert current['freshness'] == 'stale'
    return current


def task_records(root):
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        return {sid: json.loads(record) for sid, record in conn.execute('SELECT session_id,record FROM robotics_task_sessions')}


def check_records(root):
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        return [json.loads(row[0]) for row in conn.execute('SELECT report FROM application_checks ORDER BY rowid')]

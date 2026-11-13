"""IR-026 integration fixtures: real design/store/core, synthetic model transport only — DR-072."""
from copy import deepcopy
import hashlib
import importlib
import json
import sqlite3

import pytest
from rdflib import Graph, RDF
from rdflib.compare import isomorphic

from ontology_assets import AssetStore
from ontology_assets.robotics_design import DesignAssets
from tests.robotics_adaptation_support import role_map
from tests.robotics_design_support import Harness, proposals, reply
from tests.robotics_support import S, config
from tests.robotics_task_support import BASIS, EXPIRY, TEXT, model

ENTRY = 'robotics.generate_behavior_tree'
PREFIX = '/api/robotics/application-adaptations'


def service_api():
    # Missing target capability fails a collected test, never collection/importorskip.
    return importlib.import_module('domain_runs.robotics_application_service')


def reader_api():
    return importlib.import_module('ontology_assets.robotics_application')


def candidate(root, *, missing_role=None):
    h = Harness(root)
    if missing_role is None:
        state = h.candidate()
    else:
        classes, properties = deepcopy(proposals())
        classes['classes'] = [c for c in classes['classes'] if c['id'] != missing_role]
        for kind in ('object_properties', 'data_properties'):
            properties[kind] = [p for p in properties[kind] if not any(missing_role in p.get(field, [])
                for field in ('domain_ids', 'range_ids', 'class_ids'))]
        h.runtime.responses.append(reply(classes)); state = h.submit(description='合成测试：仅按通用骨架构建')
        h.run(); state = h.get(state)
        state = h.service.confirm_classes(state['session_id'], request_id='confirm-classes',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        h.runtime.responses.append(reply(properties)); h.message(state); h.run(); state = h.get(state)
        h.service.confirm_schema(state['session_id'], request_id='confirm-schema',
            expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
        h.run(); state = h.get(state)
    return h, state, state['output_asset_ref']


def service(root, *, read_only=False):
    return service_api().ApplicationService(root, read_only=read_only)


def begin(root, ref, *, request_id='prepare-1', task_ref=None, mode='real'):
    return service(root).prepare(**ref, mode=mode, task_ref=task_ref, request_id=request_id)


def confirm(root, state, *, request_id='confirm-roles', bindings=None):
    if bindings is None:
        detail = DesignAssets(root).detail(**state['asset_ref'])
        bindings = role_map(detail['tbox'])['bindings']
    return service(root).confirm_roles(state['adaptation_id'], bindings=bindings,
        expected_revision=state['revision'], request_id=request_id)


def submit(root, state, *, request_id='submit-1'):
    return service(root).submit(state['adaptation_id'], expected_revision=state['revision'], request_id=request_id)


def finish(root, queued):
    run = service(root).execute(queued['run_id'])
    assert run['status'] == 'completed', run
    state = service(root).get(queued['adaptation_id'])
    assert state['status'] == 'completed' and state['output_asset_ref'] == run['output_asset_ref']
    return state


def adapted(root, *, request_id='prepare-1'):
    h, original, ref = candidate(root)
    state = begin(root, ref, request_id=request_id)
    return h, original, finish(root, submit(root, confirm(root, state)))


def check_body(ref, request_id='check-1', task_ref=None):
    context = {'mode': 'real'}
    if task_ref is not None: context['task_ref'] = task_ref
    return {'entrypoint': ENTRY, 'asset_refs': [ref], 'context': context, 'request_id': request_id}


def expect_error(code, call):
    with pytest.raises(ValueError) as caught:
        call()
    assert getattr(caught.value, 'code', None) == code, str(caught.value)


def current_bundle(root, ref):
    reader = reader_api().ApplicationAssets(root, read_only=True)
    raw = reader.payload(**ref); document = json.loads(reader.export_json(**ref))
    from domains.robotics.application_adaptation import restore_bundle
    assert document['contract'] == {'id': 'robotics-application-design', 'version': 2}
    assert document['output_asset_ref'] == ref
    assert document['payload_sha256'] == hashlib.sha256(raw).hexdigest()
    assert raw == AssetStore(root, read_only=True).read_payload(**ref)
    assert isomorphic(Graph().parse(data=restore_bundle(reader.export_json(**ref)), format='xml'),
                      Graph().parse(data=raw, format='xml'))
    return raw, document


def archive_xml(root, ref):
    assets = AssetStore(root, read_only=True)
    version = assets.get_version(**ref); moved = []
    for source_id in version['source_asset_ids']:
        source = assets.get_source(source_id)
        if source['role'] not in ('behavior_tree', 'node_models'): continue
        path = root / source['path']; target = path.with_name(path.name + '.test-archived')
        path.rename(target); moved.append((path, target))
    assert len(moved) == 2
    return moved


def answers(state, graph, *, evidence=False):
    """Explicit software-test choices against returned gaps; never invent production defaults."""
    values = config()['Perception']
    parameters = {'objectName': values['object_name'], 'modelName': values['model_name'],
                  'threshold': values['threshold'], 'exposureTime': values['exposure_time']}
    classes = {'hasPartType': 'PartType', 'hasPerceptionProfile': 'PerceptionProfile',
               'hasPlacementProfile': 'PlacementProfile', 'hasCaptureJoints': 'JointConfiguration',
               'hasTargetPose': 'CartesianPose'}
    result = []
    for gap in state['gaps']:
        item = {'gap_id': gap['id']}; kind, field = gap['kind'], gap['field']
        if kind == 'mapping':
            if field in ('hasCarrier', 'hasSlot'):
                item['target'] = state['task']['carrier' if field == 'hasCarrier' else 'slot']
            else:
                choices = list(graph.subjects(RDF.type, S[classes[field]]))
                assert len(choices) == 1, (field, choices)
                item['target'] = str(choices[0])
        elif kind == 'parameter':
            assert field in parameters, 'New physical fields must have explicit test values'
            item['value'] = parameters[field]
        elif kind == 'confirmation': item['confirmed'] = True
        elif kind == 'applicability':
            assert evidence, 'Offline test must not accidentally manufacture applicability evidence'
            item.update(reference=BASIS, valid_until=EXPIRY, confirmed=True)
        else: raise AssertionError(gap)
        result.append(item)
    return result


def fill_task(root, ref, *, policy='offline', check_each=None, supply_evidence=False):
    from domain_runs.robotics_task_service import TaskService
    from tests.robotics_governance_support import TASK
    from tests.unit.test_robotics_task_model import envelope
    client, transport = model(envelope(TASK))
    tasks = TaskService(root, model_client=client, generation_policy=policy)
    state = tasks.start(**ref, mode='real', request_id='task-start')
    state = tasks.message(state['session_id'], text=TEXT, expected_revision=state['revision'], request_id='task-text')
    for index in range(16):
        if check_each is not None: check_each(state)
        if state['status'] == 'generated': return state, transport
        if state['status'] == 'needs_clarification':
            field = next(i['field'] for i in state['issues'] if i.get('field') in ('part', 'station', 'carrier', 'slot'))
            kinds = {'part': 'Workpiece', 'station': 'Workstation', 'carrier': 'Carrier', 'slot': 'PlacementSlot'}
            state = tasks.choose(state['session_id'], field=field, create={'kind': kinds[field], 'label': TASK[field]},
                expected_revision=state['revision'], request_id='choose-'+field)
        else:
            assert state['status'] == 'needs_information', state
            graph = Graph().parse(data=AssetStore(root, read_only=True).read_payload(state['ontology_id'], state['version_id']), format='xml')
            if policy == 'strict' and not supply_evidence and state['gaps'] and all(g['kind'] == 'applicability' for g in state['gaps']):
                return state, transport
            state = tasks.complete(state['session_id'], answers=answers(state, graph, evidence=supply_evidence),
                expected_revision=state['revision'], request_id=f'fill-{index}')
    raise AssertionError('Real task completion did not converge within declared stages')


def restore_task_record(root, state):
    # Independent observation helper, never used to authorize or fabricate a ready task.
    with sqlite3.connect(root / 'domain.sqlite3') as conn:
        row = conn.execute('SELECT record FROM robotics_task_sessions WHERE session_id=?', (state['session_id'],)).fetchone()
    return json.loads(row[0])

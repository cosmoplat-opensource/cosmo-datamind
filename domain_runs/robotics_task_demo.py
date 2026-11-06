"""Explicit fresh-source demo assets and offline task cases — DR-061 / DR-064 / DR-066.

Packaged examples originate in the hash-registered IR-016 migration fixtures.
Synthetic parameters/evidence are never applied to an uploaded user ontology.
"""
from pathlib import Path
import json
import uuid

from rdflib import Graph
from domains.robotics import demo, consumer
from domains.robotics.task_model import ModelClient
from ontology_assets import AssetStore
from srv_context import _RDF_LOCK

from . import RunStore
from .robotics_build import compute_build
from .robotics_task_service import TaskService

EXAMPLES = Path(__file__).resolve().parents[1] / 'domains/robotics/resources/task_demo'
TEXT = '生成把零部件A放到工位B载具B01放置位B01的行为树'
TASK = {'operation': 'pick_place', 'part': '零部件A', 'station': '工位B',
        'carrier': '载具B01', 'slot': '放置位B01', 'unsupported_requests': []}
BASIS = '合成测试资料 TEST-ONLY；仅验证软件流程，不代表现场机器人适配'


def build_example(root, *, mode='real'):
    assets, runs = AssetStore(root), RunStore(root)
    # Queuing and claiming share the same transaction: the live build worker cannot
    # accidentally claim this synchronous, explicitly requested packaged example.
    with _RDF_LOCK, runs.storage.transaction(write=True) as conn:
        sources = [assets._put_source(conn, (EXAMPLES / file).read_bytes(), role=role, media_type='application/xml')
            for role, file in [('behavior_tree', 'behavior_tree.xml'), ('node_models', 'node_models.xml')]]
        run = runs._create_build_run(conn, name='独立合成演示' if mode == 'demo' else '离线合成测试',
            recipe='bt_xml', profile_id='ur_pick_place', source_asset_ids=[s['source_asset_id'] for s in sources],
            session_ref={'namespace': 'robotics_demo', 'id': uuid.uuid4().hex},
            components={'profile_id': 'ur_pick_place', 'compiler_version': None, 'rule_digest': None,
                        'prompt_version': None, 'model': {'invoked': False}})
        runs._state(conn, run['run_id'], 'running')
    built = runs.finish_build(run['run_id'], **compute_build(root, run['run_id']))
    if built['result']['status'] != 'built':
        raise ValueError('登记示例构建失败。')
    version = assets.get_version(**built['output_asset_ref'])
    if mode == 'demo':
        # The actual build has already normalized/validated the real artifact. Check
        # its immutable bytes, supplement a copy, then validate the new actual bytes.
        graph = Graph().parse(data=assets.read_payload(version['ontology_id'], version['version_id']), format='xml')
        supplemented = demo.apply_demo_supplement(graph, EXAMPLES / 'supplement.json')
        content = supplemented['graph'].serialize(format='xml', encoding='utf-8')
        consumer.load_ontology_bytes(content, mode='demo')
        supplement = assets.put_source((EXAMPLES / 'supplement.json').read_bytes(),
            role='demo_supplement', media_type='application/json')
        version = assets.add_version(version['ontology_id'], content, payload_kind='robotics_rdf',
            parent_version_id=version['version_id'],
            source_asset_ids=version['source_asset_ids'] + [supplement['source_asset_id']],
            metadata={'mode': 'demo', 'profile_id': 'ur_pick_place', 'synthetic_inputs': True,
                      'origin_build_run_id': run['run_id']})
    return version


def synthetic_model(*, fail=False, clarification=False):
    def transport(**request):
        if fail:
            raise TimeoutError('scripted offline model failure')
        task = {**TASK, 'carrier': None, 'slot': None} if clarification else TASK
        return {'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps(task, ensure_ascii=False)}}]}
    return ModelClient('https://offline.invalid/v1', 'scripted-test-model', 'offline-test-only', transport=transport)


def run_demo(*, case, output):
    if case not in ('generated', 'clarification', 'missing-knowledge', 'model-error', 'complete-information'):
        raise ValueError('未知演示案例。')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = output / 'storage'
    mode = 'demo' if case in ('generated', 'clarification') else 'real'
    version = build_example(root, mode=mode)
    service = TaskService(root, model_client=synthetic_model(fail=case == 'model-error', clarification=case == 'clarification'))
    state = service.start(ontology_id=version['ontology_id'], version_id=version['version_id'], mode=mode, request_id='start')
    state = service.message(state['session_id'], text='生成把零部件A放到工位B的行为树' if case == 'clarification' else TEXT,
                            expected_revision=state['revision'], request_id='describe')
    if case == 'complete-information':
        ns = 'https://example.org/ur-skill#'
        for index in range(8):
            if state['status'] == 'generated':
                break
            if state['status'] != 'needs_information':
                raise ValueError('合成补齐案例未到达信息补齐步骤。')
            answers = []
            for gap in state['gaps']:
                answer = {'gap_id': gap['id']}
                if gap['kind'] == 'mapping':
                    target = {'hasPerceptionProfile': 'Perception_Current', 'hasPlacementProfile': 'Placement_Current'}[gap['field']]
                    answer['target'] = ns + target
                elif gap['kind'] == 'confirmation':
                    answer['confirmed'] = True
                elif gap['kind'] == 'applicability':
                    answer.update(reference=BASIS, valid_until='2099-01-01T00:00:00+00:00', confirmed=True)
                else:
                    raise ValueError('登记案例出现未定义的参数缺口，不可猜测默认值。')
                answers.append(answer)
            state = service.complete(state['session_id'], answers=answers,
                expected_revision=state['revision'], request_id=f'complete-{index}')
    expected = {'generated': 'generated', 'complete-information': 'generated', 'clarification': 'needs_clarification',
                'missing-knowledge': 'needs_information', 'model-error': 'error'}[case]
    assets = AssetStore(root, read_only=True)
    (output / 'ontology.owl').write_bytes(assets.read_payload(state['ontology_id'], state['version_id']))
    (output / 'dialogue.json').write_text(json.dumps(state['messages'], ensure_ascii=False, indent=2))
    result = state['result'] or {k: state[k] for k in ('status', 'gaps', 'issues', 'error', 'execution_readiness')}
    (output / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if state['candidate']:
        (output / 'candidate.xml').write_bytes(service.read_candidate(state['session_id'], expected_revision=state['revision']))
    report = {'case': case, 'passed': state['status'] == expected, 'session_id': state['session_id'],
        'model_source': 'scripted_test_double', 'synthetic_inputs': True, 'execution_readiness': 'not_verified',
        'status': state['status'], 'description': BASIS, 'storage_root': 'storage'}
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report

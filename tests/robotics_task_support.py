"""IR-020 real storage fixtures, scripted HTTP model and independent XML oracle — DR-062 / DR-064 / DR-066.

Synthetic inputs test software contracts only; never evidence of physical applicability.
Only the external model is replaced. No task/save/compiler/download service is mocked.
"""
from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import threading
import xml.etree.ElementTree as ET

from rdflib import Graph

from domain_runs import RunStore
from domains.robotics.publisher import publish_ontology
from domains.robotics.task_model import ModelClient
from ontology_assets import AssetStore
from tests.robotics_governance_support import NOW, TASK, actual_build, evidence_spec
from tests.robotics_support import S, config, sequence, source_root, trees
from tests.unit.test_robotics_task_completion import ready_graph
from tests.unit.test_robotics_task_model import ScriptedTransport, envelope

TEXT = '生成把零部件A放到工位B载具B01放置位B01的行为树'
BASIS = '合成测试资料 TEST-ONLY；仅验证软件流程，不代表现场机器人适配'
EXPIRY = '2099-01-01T00:00:00+00:00'


def task_api():
    # Missing production code is a collected RED assertion, never a skipped test.
    return importlib.import_module('domain_runs.robotics_task_service')


def model(*responses):
    transport = ScriptedTransport(*(responses or [envelope(TASK)]))
    return ModelClient('https://model.invalid/v1', 'scripted-test-model', 'synthetic-secret',
                       transport=transport), transport


def fixture(root, *, ready=False, mutate=None):
    """Use a real two-XML build; ready/mutations are clearly test-only setup."""
    version, build_run = actual_build(root)
    assets = AssetStore(root)
    if ready or mutate is not None:
        graph = ready_graph() if ready else Graph().parse(data=assets.read_payload(
            version['ontology_id'], version['version_id']), format='xml')
        target = root.parent / (root.name + '-synthetic-fixture.owl')
        publish_ontology(graph, target, mode='real')
        # Normalize a valid contract first. Incomplete editable candidates use storage's
        # RDF syntax contract, not the strict consumer/publisher gate they cannot yet pass.
        graph = Graph().parse(data=target.read_bytes(), format='xml')
        if ready:
            # Publishing normalizes executable port metadata. Confirm synthetic evidence
            # against that normalized graph, not the different pre-publication digest.
            from domains.robotics import evidence
            for index, kind, implementation, slot in [
                (0, 'grasp_compatibility', S.grasp_Subtree_Impl, None),
                (1, 'placement_compatibility', S.Place_Subtree_Impl, str(S.Slot_B_01)),
            ]:
                subject = S[f'TaskCompletionEvidence{index}']
                graph.remove((subject, None, None))
                spec = evidence_spec(iri=str(subject), kind=kind, implementation=str(implementation),
                    slot=slot, reference=BASIS, valid_until=EXPIRY)
                graph = evidence.register_evidence(graph, spec, mode='real', now=NOW)
                graph = evidence.review_evidence(graph, str(subject), decision='accepted',
                    actor='synthetic fixture', reason=BASIS, now=NOW)
        if mutate:
            mutate(graph)
        version = assets.add_version(version['ontology_id'], graph.serialize(format='xml', encoding='utf-8'), payload_kind='robotics_rdf',
            parent_version_id=version['version_id'], source_asset_ids=version['source_asset_ids'],
            metadata={**version['metadata'], 'fixture': 'synthetic, not physical validation'})
    return assets, version, build_run


def start(service, version, *, mode='real', request_id='start'):
    return service.start(ontology_id=version['ontology_id'], version_id=version['version_id'],
                         mode=mode, request_id=request_id)


def message(service, session, *, text=TEXT, request_id='message'):
    return service.message(session['session_id'], text=text, expected_revision=session['revision'],
                           request_id=request_id)


def complete(service, session, answers, *, request_id='complete'):
    return service.complete(session['session_id'], expected_revision=session['revision'],
                            answers=deepcopy(answers), request_id=request_id)


def gap(session, field, *, kind=None):
    matches = [g for g in session['gaps'] if g['field'] == field and (kind is None or g['kind'] == kind)]
    assert len(matches) == 1, (field, session['gaps'])
    return matches[0]


def graph_of(root, session):
    return Graph().parse(data=AssetStore(root, read_only=True).read_payload(
        session['ontology_id'], session['version_id']), format='xml')


def synthetic_answers(session):
    """Explicit test answers; never used by production as defaults or automatic consent."""
    mappings = {'hasPartType': S.PartType_A, 'hasCarrier': S.Carrier_B_01, 'hasSlot': S.Slot_B_01,
                'hasPerceptionProfile': S.Perception_Current, 'hasPlacementProfile': S.Placement_Current,
                'hasCaptureJoints': S.CaptureJoints_Current, 'hasTargetPose': S.PlacePose_Current}
    values = config()['Perception']
    parameters = {'objectName': values['object_name'], 'modelName': values['model_name'],
                  'threshold': values['threshold'], 'exposureTime': values['exposure_time']}
    result = []
    for item in session['gaps']:
        answer = {'gap_id': item['id']}
        if item['kind'] == 'mapping':
            answer['target'] = str(mappings[item['field']])
        elif item['kind'] == 'parameter':
            assert item['field'] in parameters, 'This fixture must explicitly define any new physical value'
            answer['value'] = parameters[item['field']]
        elif item['kind'] == 'confirmation':
            answer.update(confirmed=True)
        elif item['kind'] == 'applicability':
            answer.update(reference=BASIS, valid_until=EXPIRY, confirmed=True)
        else:
            raise AssertionError(item)
        result.append(answer)
    return result


def finish_information(service, session):
    for index in range(8):
        if session['status'] == 'generated':
            return session
        assert session['status'] == 'needs_information' and session['gaps'], session
        session = complete(service, session, synthetic_answers(session), request_id=f'fill-{index}')
    raise AssertionError('Completion did not converge within the declared dependency stages')


def tree_json(node):
    """Independent preview contract; attributes and child order come from downloaded bytes."""
    return {'tag': node.tag, 'attributes': dict(node.attrib), 'children': [tree_json(c) for c in node]}


def expected_xml(values=None):
    """Patch six frozen public slots directly; do not call the compiler to compute the oracle."""
    values = config() if values is None else values
    expected = source_root()
    main = sequence(expected, 'main')
    main[1].set('value', values['Perception']['position'])
    for name in ('object_name', 'model_name', 'threshold', 'exposure_time'):
        main[4].set(name, values['Perception'][name])
    main[8].set('place_pose', values['Place']['place_pose'])
    return expected


def assert_generated(root, session, *, values=None):
    assert session['status'] == 'generated' and session['gaps'] == []
    runs = RunStore(root, read_only=True)
    run = runs.get_run(session['run_id'])
    assert run['version_id'] == session['version_id'] and run['ontology_id'] == session['ontology_id']
    assert run['session_ref'] == {'namespace': 'robotics_task', 'id': session['session_id']}
    assert run['result']['status'] == 'generated'
    assert run['result']['execution_readiness'] == 'not_verified'
    artifacts = [a for a in runs.artifacts(run['run_id']) if a['kind'] == 'candidate']
    assert len(artifacts) == 1
    raw = runs.read_artifact(run['run_id'], artifacts[0]['artifact_id'])
    tree = ET.fromstring(raw)
    assert trees(tree) == trees(expected_xml(values))
    assert session['tree'] == tree_json(tree)
    assert session['candidate'] == {'run_id': run['run_id'], 'artifact_id': artifacts[0]['artifact_id']}
    manifest = runs.manifest(run['run_id'])
    assert manifest['version_id'] == session['version_id'] and manifest['task'] == run['task']
    assert len(run['result']['parameter_sources']) == 6
    return raw


def assert_no_result(session):
    assert session.get('candidate') is None and session.get('tree') is None


@contextmanager
def model_server(*responses):
    """Real loopback HTTP, with response bodies/status/optional finite synchronization gate."""
    queue = deque(responses)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            calls.append({'path': self.path, 'headers': dict(self.headers), 'payload': json.loads(raw)})
            item = queue.popleft() if queue else {'status': 500, 'body': b'unexpected model request'}
            if 'entered' in item:
                item['entered'].set()
            if 'release' in item:
                item['release'].wait(timeout=12)
            body = item.get('body', json.dumps(envelope(item.get('task', TASK)), ensure_ascii=False).encode())
            self.send_response(item.get('status', 200))
            for key, value in item.get('headers', {}).items():
                self.send_header(key, value)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', calls
    finally:
        for item in responses:
            if 'release' in item:
                item['release'].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

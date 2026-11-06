"""T04 RED: five runnable offline cases and independent saved-byte checks — DR-062 / DR-064 / DR-066."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from domain_runs import RunStore
from ontology_assets import AssetStore
from tests.robotics_support import SOURCE, MODELS
from tests.robotics_task_support import assert_generated, assert_no_result, task_api

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/demo_robotics_task.py'
CASES = {'generated': 'generated', 'clarification': 'needs_clarification',
         'missing-knowledge': 'needs_information', 'model-error': 'error', 'complete-information': 'generated'}
GUARD = '''import sys
def audit(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise AssertionError('Offline task demo attempted network access')
    if event == 'open':
        name=str(args[0]).replace('\\\\','/')
        if 'yang_implementation/' in name or '/fixtures/robotics/owl/' in name:
            raise AssertionError('Build fresh XML; do not consume archive or prebuilt OWL')
    if event == 'import' and args[0] == 'server':
        raise AssertionError('Offline demo must not import live application')
sys.addaudithook(audit)
'''


def invoke(tmp_path, case, output):
    assert SCRIPT.is_file(), 'T05 must deliver the five documented offline demo commands'
    guard = tmp_path / 'guard'
    guard.mkdir(exist_ok=True)
    (guard / 'sitecustomize.py').write_text(GUARD)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DATAMIND_', 'BT_AGENT_')) and 'API_KEY' not in k}
    env.update(PYTHONPATH=os.pathsep.join((str(guard), str(ROOT))), DATAMIND_WORKDIR=str(tmp_path / 'legacy'))
    return subprocess.run([sys.executable, str(SCRIPT), '--case', case, '--output', str(output)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)


def verify(output, case):
    report = json.loads((output / 'summary.json').read_text())
    assert report['case'] == case and report['passed'] is True
    assert report['model_source'] == 'scripted_test_double'
    assert report['execution_readiness'] == 'not_verified'
    assert report['synthetic_inputs'] is True
    root = output / 'storage'
    state = task_api().TaskService(root).get(report['session_id'])
    assert state['status'] == CASES[case]
    assert json.loads((output / 'dialogue.json').read_text()) == state['messages']
    assets = AssetStore(root, read_only=True)
    assert (output / 'ontology.owl').read_bytes() == assets.read_payload(state['ontology_id'], state['version_id'])
    runs = RunStore(root, read_only=True)
    builds = [r for r in runs.list_runs() if r['operation'] == 'build_ontology']
    assert builds, 'Each case must originate in the two actual XML inputs'
    for run in builds:
        ref = run['output_asset_ref']
        version = assets.get_version(**ref)
        sources = {assets.get_source(s)['role']: assets.read_source(s) for s in version['source_asset_ids']}
        assert sources['behavior_tree'] == SOURCE.read_bytes() and sources['node_models'] == MODELS.read_bytes()
    if state['status'] == 'generated':
        assert (output / 'candidate.xml').read_bytes() == assert_generated(root, state)
        assert json.loads((output / 'validation.json').read_text()) == runs.get_run(state['run_id'])['result']
    else:
        assert_no_result(state)
        assert not list(output.rglob('*.xml'))
        problem = json.loads((output / 'validation.json').read_text())
        assert problem['status'] == state['status']
        if case == 'missing-knowledge': assert problem['gaps'] == state['gaps'] and state['gaps']
        if case == 'model-error': assert problem['error']['code'] == state['error']['code']
    assert state['mode'] == ('demo' if case in ('generated', 'clarification') else 'real')
    return state


@pytest.mark.parametrize('case', list(CASES))
def test_documented_offline_command_builds_fresh_and_emits_independently_verifiable_artifacts(tmp_path, case):
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    sentinel = legacy / 'ont_chats.json'
    sentinel.write_text('{"keep":true}')
    original_sources = SOURCE.read_bytes(), MODELS.read_bytes()
    output = tmp_path / 'output'
    result = invoke(tmp_path, case, output)
    assert result.returncode == 0, result.stdout + result.stderr
    verify(output, case)
    assert sentinel.read_text() == '{"keep":true}'
    assert (SOURCE.read_bytes(), MODELS.read_bytes()) == original_sources


def test_complete_information_demo_uses_real_scoped_save_and_explicit_synthetic_answers(tmp_path, monkeypatch):
    assert SCRIPT.is_file(), 'Task demo not implemented'
    service = task_api().TaskService
    original = service.complete
    calls = []
    def observe(self, session_id, **args):
        result = original(self, session_id, **args)
        calls.append({'session_id': session_id, 'answers': args['answers'], 'version_id': result['version_id']})
        return result
    monkeypatch.setattr(service, 'complete', observe)
    cli = importlib.import_module('scripts.demo_robotics_task')
    report = cli.run_demo(case='complete-information', output=tmp_path / 'output')
    assert report['passed'] is True and calls
    answers = [answer for call in calls for answer in call['answers']]
    assert any('target' in a or 'create' in a for a in answers)
    assert any(a.get('confirmed') is True for a in answers)
    assert any(a.get('reference') and a.get('valid_until') and a.get('confirmed') is True for a in answers)
    assert len({call['version_id'] for call in calls}) >= 2
    state = verify(tmp_path / 'output', 'complete-information')
    assert state['mode'] == 'real'
    assert all(v['publication_state'] == 'candidate' for v in
               AssetStore(tmp_path / 'output/storage').list_versions(state['ontology_id']))


@pytest.mark.parametrize('populated', [False, True])
def test_existing_output_is_never_deleted_or_overwritten(tmp_path, populated):
    output = tmp_path / 'output'
    output.mkdir()
    if populated: (output / 'keep.txt').write_text('untouched')
    before = {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    result = invoke(tmp_path, 'complete-information', output)
    assert result.returncode != 0
    assert {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('failure', ['exception', 'failed_report'])
def test_cli_exit_status_follows_actual_failure(tmp_path, monkeypatch, failure):
    assert SCRIPT.is_file(), 'Task demo not implemented'
    cli = importlib.import_module('scripts.demo_robotics_task')
    def failed(**kwargs):
        if failure == 'exception': raise ValueError('synthetic failure')
        return {'passed': False}
    monkeypatch.setattr(cli, 'run_demo', failed)
    assert cli.main(['--case', 'generated', '--output', str(tmp_path / 'output')]) != 0

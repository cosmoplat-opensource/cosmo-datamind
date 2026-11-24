"""Exercise graph browser evidence at the pytest and runner boundaries — DR-179."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / 'tests/frontend/robotics-interactive-ontology-graph-browser.py'


def test_graph_browser_evidence_follows_each_pytest_outcome(tmp_path):
    # Run real pytest phases with an isolated evidence writer in place of Chromium.
    suite = tmp_path / SUITE.name
    suite.write_text("""
import json
import os
from pathlib import Path
import pytest

pytest_plugins = ['tests.robotics_graph_browser_evidence']

@pytest.fixture
def recorded(request):
    mode = request.param
    if mode == 'setup':
        pytest.fail('fixture setup')
    if mode == 'skip':
        pytest.skip('fixture skip')
    yield mode
    path = Path(os.environ['DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT']) / ('test_case_' + mode + '_') / 'evidence.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(assertions_completed=False, requests=['sample'],
                                   page_errors=['sample error'] if mode == 'page' else [])))
    if mode == 'teardown':
        pytest.fail('fixture teardown')

@pytest.mark.parametrize('recorded', ['pass', 'call', 'setup', 'skip', 'teardown', 'page'], indirect=True)
def test_case(recorded):
    assert recorded != 'call'
""")
    output = tmp_path / 'out'
    modes = ('pass', 'call', 'setup', 'skip', 'teardown', 'page')
    for mode in modes:
        directory = output / f'test_case_{mode}_'
        directory.mkdir(parents=True)
        (directory / 'evidence.json').write_text('{"assertions_completed":true,"old":true}')
    junit = output / 'results.xml'
    env = dict(os.environ, DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT=str(output),
               PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    result = subprocess.run([sys.executable, '-m', 'pytest', str(suite), '--junitxml', str(junit)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1, result.stdout + result.stderr
    cases = {c.attrib['name']: c for c in ET.parse(junit).iter('testcase')}
    assert len(cases) == len(modes)
    for mode in modes:
        case = cases[f'test_case[{mode}]']
        status = next((tag for tag in ('failure', 'error', 'skipped') if case.find(tag) is not None), 'pass')
        assert status == {'call': 'failure', 'skip': 'skipped', 'pass': 'pass'}.get(mode, 'error')
        record = json.loads((output / f'test_case_{mode}_' / 'evidence.json').read_text())
        assert record['assertions_completed'] is (mode == 'pass')
        assert 'old' not in record
        if mode not in ('setup', 'skip'):
            assert record['requests'] == ['sample']


@pytest.mark.parametrize('record, pytest_code, expected', [
    ('{"assertions_completed":true}', 0, 0),
    ('{"assertions_completed":false}', 0, 1),
    ('{"assertions_completed":"true"}', 0, 1),
    ('bad json', 0, 1), (None, 0, 1),
    ('{"assertions_completed":false}', 1, 1),
    ('{"assertions_completed":true}', 1, 1),
])
def test_graph_browser_runner_checks_junit_against_each_record(tmp_path, monkeypatch, capsys,
                                                            record, pytest_code, expected):
    def run(args):
        report = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--junitxml='))
        suite = ET.Element('testsuite')
        case = ET.SubElement(suite, 'testcase', name='test_graph[demo]')
        if pytest_code:
            ET.SubElement(case, 'failure')
        ET.ElementTree(suite).write(report)
        if record is not None:
            case_dir = tmp_path / 'test_graph_demo_'
            case_dir.mkdir()
            (case_dir / 'evidence.json').write_text(record)
        return pytest_code

    monkeypatch.setenv('DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT', str(tmp_path))
    monkeypatch.setattr(pytest, 'main', run)
    monkeypatch.setattr(sys, 'argv', [str(SUITE), '--output', str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SUITE), run_name='__main__')
    assert exc.value.code == expected
    assert json.loads((tmp_path / 'runner.json').read_text())['exit_code'] == expected
    if expected and (not pytest_code or record == '{"assertions_completed":true}'):
        assert 'test_graph[demo]' in capsys.readouterr().err

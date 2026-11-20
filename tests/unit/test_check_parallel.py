"""Bounded workers and CI partitions retain dependencies and coverage gates — DR-163."""
from importlib import import_module
import os
import runpy
import subprocess
import sys

import pytest
import yaml


@pytest.fixture
def ci_jobs():
    module = import_module('scripts.check')
    return yaml.safe_load((module.ROOT / '.github/workflows/ci.yml').read_text())['jobs']


def test_unit_command_keeps_all_tests_and_explicit_parallel_coverage(tmp_path):
    module = import_module('scripts.check')
    command = module.unit_command(sys.executable, tmp_path, 4)
    assert command[:3] == [sys.executable, '-m', 'pytest']
    assert 'tests/' in command and '-ra' in command
    assert command[command.index('-n') + 1] == '4'
    assert command[command.index('--dist') + 1] == 'worksteal'
    assert '--cov' in command and '--cov-report=' in command
    assert any(arg.startswith('--junitxml=') for arg in command)
    assert '--durations=30' in command
    serial = module.unit_command(sys.executable, tmp_path, 1)
    assert '-n' not in serial and '--cov' in serial and 'tests/' in serial
    for workers in (0, 5, -1):
        with pytest.raises(ValueError):
            module.unit_command(sys.executable, tmp_path, workers)


@pytest.mark.parametrize('workers', [1, 2])
def test_design_browser_entry_keeps_full_suite_and_bounded_isolated_workers(tmp_path, monkeypatch, workers):
    module = import_module('scripts.check')
    source = module.ROOT / 'tests/frontend/robotics-design-browser.py'
    calls = []
    monkeypatch.setenv('DATAMIND_ROBOTICS_DESIGN_TEST_OUTPUT', 'synthetic-test-output')
    monkeypatch.setattr(pytest, 'main', lambda args: calls.append(args) or 1)
    monkeypatch.setattr(sys, 'argv', [str(source), '--output', str(tmp_path), '--workers', str(workers)])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(source), run_name='__main__')
    assert caught.value.code == 1, 'browser failures must propagate'
    args = calls[0]
    assert args[0] == str(source) and '-k' not in args and '-m' not in args
    assert '--junitxml=' + str(tmp_path / 'results.xml') in args
    assert '--durations=20' in args
    if workers == 2:
        assert args[args.index('-n') + 1] == '2'
        assert args[args.index('--dist') + 1] == 'worksteal'
    else:
        assert '-n' not in args


@pytest.mark.parametrize('failure', [False, True])
def test_parallel_workers_merge_coverage_and_propagate_failures(tmp_path, failure):
    module = import_module('scripts.check')
    reports = tmp_path / 'reports'; reports.mkdir()
    tests = tmp_path / 'tests'; tests.mkdir()
    (tmp_path / 'left.py').write_text('def value():\n    return 1\n')
    (tmp_path / 'right.py').write_text('def value():\n    return 2\n')
    (tmp_path / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    (tests / 'test_workers.py').write_text(
        'import os\nfrom pathlib import Path\nimport pytest\n'
        '@pytest.mark.parametrize("index", [0,1,2,3])\n'
        'def test_value(index):\n'
        '    import left, right\n'
        '    Path("worker-"+os.environ["PYTEST_XDIST_WORKER"]).write_text("ran")\n'
        '    assert (left.value() if index < 2 else right.value()) == (1 if index < 2 else 2)\n'
        + ('    assert index != 3\n' if failure else ''))
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('COV_CORE_', 'COVERAGE_')) and k != 'PYTEST_ADDOPTS'}
    env['COVERAGE_FILE'] = str(reports / '.coverage')
    config = tmp_path / 'coverage.ini'
    config.write_text('[run]\nbranch = True\nsource = left,right\n[report]\nfail_under = 74\n')
    command = [('--cov-config=' + str(config)) if arg.startswith('--cov-config=') else arg
               for arg in module.unit_command(sys.executable, reports, 2)]
    result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == int(failure), result.stdout + result.stderr
    from coverage import CoverageData
    data = CoverageData(basename=str(reports / '.coverage')); data.read()
    assert data.has_arcs(), result.stdout + result.stderr
    for name in ('left.py', 'right.py'):
        assert 2 in data.lines(str(tmp_path / name))
    assert len(list(tmp_path.glob('worker-*'))) == 2
    assert (reports / 'unit.xml').is_file()


def test_ci_unit_job_installs_dependencies_required_by_all_collected_tests(ci_jobs):
    job = ci_jobs['unit_tests']
    steps = job['steps']
    commands = [step.get('run', '') for step in steps]
    assert any('-r requirements-dev.txt' in command and '-r requirements-ui.txt' in command
               for command in commands)
    assert any('playwright install --with-deps chromium' in command for command in commands)
    assert any(step.get('uses', '').startswith('actions/setup-node@') for step in steps)
    checks = next(step for step in steps if step.get('id') == 'checks')
    assert '--suite unit --unit-shard' in checks['run']
    assert checks['env']['SHARD'] == '${{ matrix.shard }}'
    assert checks['env']['COUNT'] == '${{ strategy.job-total }}'
    assert job['strategy']['matrix']['shard'] == list(range(1, 9))
    assert job['strategy']['fail-fast'] is False
    upload = next(step for step in steps if step.get('uses', '').startswith('actions/upload-artifact@'))
    assert 'always()' in upload['if']
    assert upload['with']['name'] == 'unit-shard-${{ matrix.shard }}'
    assert upload['with']['if-no-files-found'] == 'error'


def test_ci_aggregate_requires_successful_partitions_and_all_coverage_inputs(ci_jobs):
    job = ci_jobs['unit']
    assert job['needs'] == 'unit_tests'
    assert '!cancelled()' in job['if']
    guard = job['steps'][0]
    assert guard['if'] == "needs.unit_tests.result != 'success'"
    assert guard['run'].strip() == 'exit 1'
    commands = [step.get('run', '') for step in job['steps']]
    assert any('-r requirements-dev.txt' in command and '-r requirements-ui.txt' in command
               for command in commands)
    download = next(step for step in job['steps']
                    if step.get('uses', '').startswith('actions/download-artifact@'))
    assert download['with']['pattern'] == 'unit-shard-*'
    checks = next(step for step in job['steps'] if step.get('id') == 'checks')
    assert checks['env']['COUNT'] == '${{ needs.unit_tests.outputs.count }}'
    assert 'i<=COUNT' in checks['run']
    assert 'unit-shard-$i/coverage-data' in checks['run']
    assert '--suite unit --combine-unit "${inputs[@]}"' in checks['run']


@pytest.fixture
def check_stage_calls(monkeypatch):
    module = import_module('scripts.check')
    calls = []
    exit_codes = {}
    monkeypatch.setattr(module, 'isolated_env', lambda directory: {})

    def record(name, command, env, reports, results):
        calls.append((name, command, dict(env)))
        code = exit_codes.get(name, 0)
        results.append({'name': name, 'exit_code': code})
        return code == 0

    monkeypatch.setattr(module, 'run_check', record)
    return module, calls, exit_codes


@pytest.mark.parametrize('workers', [1, 4])
@pytest.mark.parametrize('exit_code', [0, 1, 2])
def test_normal_unit_entry_honors_workers_and_keeps_failure_and_coverage_gates(
        tmp_path, monkeypatch, check_stage_calls, workers, exit_code):
    module, calls, exit_codes = check_stage_calls
    reports = tmp_path / 'reports'
    exit_codes['unit'] = exit_code
    monkeypatch.setattr(sys, 'argv', ['check.py', '--suite', 'unit', '--workers', str(workers),
                                    '--report-dir', str(reports)])
    assert module.main() == int(bool(exit_code))
    assert calls[2][0] == 'unit'
    assert calls[2][1] == module.unit_command(sys.executable, reports.resolve(), workers)
    expected = ['lint', 'types', 'unit']
    if exit_code != 2:
        expected += ['coverage', 'coverage-core', 'coverage-routes']
    assert [name for name, _, _ in calls] == expected
    if exit_code != 2:
        assert '--fail-under=87' in calls[4][1]
        assert '--fail-under=61' in calls[5][1]


@pytest.mark.parametrize('exit_code', [0, 1])
def test_ci_partition_entry_runs_only_its_tests_and_propagates_failure(
        tmp_path, monkeypatch, check_stage_calls, exit_code):
    module, calls, exit_codes = check_stage_calls
    reports = tmp_path / 'reports'
    exit_codes['unit-shard'] = exit_code
    monkeypatch.setattr(sys, 'argv', ['check.py', '--suite', 'unit', '--unit-shard', '2', '8',
                                    '--report-dir', str(reports)])
    assert module.main() == exit_code
    assert len(calls) == 1
    name, command, env = calls[0]
    assert name == 'unit-shard'
    assert command[:8] == [sys.executable, '-m', 'coverage', 'run', '-m', 'pytest', 'tests/', '-ra']
    assert command[8:11] == ['--ci-shard', '2', '8']
    assert command[11:] == ['--junitxml', str(reports / 'unit.xml')]
    assert env['COVERAGE_FILE'] == str(reports / 'coverage-data')


@pytest.mark.parametrize('exit_code', [0, 1])
def test_ci_aggregate_merges_coverage_without_rerunning_tests_and_keeps_gates(
        tmp_path, monkeypatch, check_stage_calls, exit_code):
    module, calls, exit_codes = check_stage_calls
    inputs = [tmp_path / f'shard-{i}' / 'coverage-data' for i in (1, 2)]
    exit_codes['coverage-combine'] = exit_code
    monkeypatch.setattr(sys, 'argv', ['check.py', '--suite', 'unit', '--report-dir', str(tmp_path / 'reports'),
                                    '--combine-unit', *map(str, inputs)])
    assert module.main() == exit_code
    expected = ['lint', 'types', 'coverage-combine']
    if not exit_code:
        expected += ['coverage', 'coverage-core', 'coverage-routes']
    assert [name for name, _, _ in calls] == expected
    assert calls[2][1] == [sys.executable, '-m', 'coverage', 'combine', '--keep', *map(str, inputs)]
    if not exit_code:
        assert '--fail-under=87' in calls[4][1]
        assert '--fail-under=61' in calls[5][1]

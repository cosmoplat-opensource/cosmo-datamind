"""Bounded workers retain test discovery, failure status and merged branch coverage."""
from importlib import import_module
import os
import subprocess
import sys

import pytest


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


def test_ci_unit_job_installs_dependencies_required_by_all_collected_tests():
    import yaml
    module = import_module('scripts.check')
    workflow = yaml.safe_load((module.ROOT / '.github/workflows/ci.yml').read_text())
    steps = [step.get('run', '') for step in workflow['jobs']['unit']['steps']]
    assert any('-r requirements-dev.txt' in step and '-r requirements-ui.txt' in step for step in steps)
    assert any('playwright install --with-deps chromium' in step for step in steps)

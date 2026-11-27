"""Record graph-browser outcomes and cross-check the suite's JUnit report — DR-184."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from tests.robotics_design_browser_support import browser_case_directory

_EVIDENCE = pytest.StashKey[Path]()
_BODY_PASSED = pytest.StashKey[bool]()


def _write_record(path, record):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item):
    if (item.path.name == 'robotics-interactive-ontology-graph-browser.py'
            and not item.config.getoption('setuponly') and not item.config.getoption('setupplan')):
        # Also reset a previous success when setup skips before the live site exists.
        path = browser_case_directory(item.name) / 'evidence.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_record(path, {'assertions_completed': False})
        item.stash[_EVIDENCE] = path
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    path = item.stash.get(_EVIDENCE, None)
    if path is None:
        return report
    if report.when == 'call':
        item.stash[_BODY_PASSED] = report.passed
    if report.when == 'teardown' and report.passed and item.stash.get(_BODY_PASSED, False):
        # LiveSite.close has now written the observations, including late page errors.
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(record, dict) or record.get('page_errors') != []:
                raise ValueError('missing browser observations or a page error was recorded')
            record['assertions_completed'] = True
            _write_record(path, record)
        except (ValueError, OSError) as exc:
            report.outcome = 'failed'
            report.longrepr = f'{item.name}: could not finalize browser evidence: {exc}'
    return report


def check_junit_evidence(output):
    try:
        cases = list(ET.parse(output / 'results.xml').iter('testcase'))
    except (ET.ParseError, OSError) as exc:
        return [f'results.xml: {exc}']
    if not cases:
        return ['results.xml: no test cases recorded']
    errors = []
    for case in cases:
        name = case.get('name')
        if not name:
            errors.append('results.xml: test case without a name')
            continue
        passed = all(case.find(tag) is None for tag in ('failure', 'error', 'skipped'))
        path = browser_case_directory(name, output) / 'evidence.json'
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            errors.append(f'{name}: could not read evidence.json: {exc}')
            continue
        if not isinstance(record, dict) or record.get('assertions_completed') is not passed:
            errors.append(f'{name}: evidence.json disagrees with JUnit (expected assertions_completed={passed})')
    return errors

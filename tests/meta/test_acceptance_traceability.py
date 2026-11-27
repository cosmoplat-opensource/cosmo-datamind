"""Validate SDD-to-executable-test links; behavior is checked by the referenced tests."""
import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]


def test_acceptance_entries_keep_only_requirement_contract_fields():
    spec = json.loads((ROOT / 'specs/acceptance.json').read_text())
    fields = ['id', 'decision', 'iteration', 'status', 'criterion', 'tests']
    for group in ('requirements', 'planned_requirements'):
        for requirement in spec[group]:
            assert not (set(requirement) - set(fields)), requirement['id']
            assert list(requirement) == [key for key in fields if key in requirement], requirement['id']
            assert {'id', 'criterion', 'tests'} <= set(requirement), requirement['id']
            if 'status' in requirement:
                assert requirement['status'] in {'planned', 'implemented', 'delivered'}, requirement['id']
            if group == 'planned_requirements':
                assert requirement['status'] == 'planned', requirement['id']


def test_acceptance_iteration_mappings_are_explicit_and_resolve():
    spec = json.loads((ROOT / 'specs/acceptance.json').read_text())
    requirements = {r['id']: r for r in spec['requirements'] + spec['planned_requirements']}
    assert requirements['RKG-06'].get('iteration') == 'IR-019'
    assert requirements['STV-R01'].get('iteration') == 'IR-031'
    for requirement in requirements.values():
        if 'iteration' in requirement:
            iteration = requirement['iteration']
            assert isinstance(iteration, str) and iteration.startswith('IR-'), requirement['id']
            matches = list((ROOT / 'specs/iterations').glob(iteration + '-*.md'))
            assert len(matches) == 1, (requirement['id'], iteration)


def test_decision_numbers_are_unique_and_titles_match_filenames():
    decisions = sorted((ROOT / 'specs/decisions').glob('DR-*.md'))
    numbers = [path.name.split('-', 2)[:2] for path in decisions]
    assert len(numbers) == len({tuple(number) for number in numbers}), 'Decision numbers must be unique'
    for path, number in zip(decisions, numbers):
        assert path.read_text().splitlines()[0].startswith('# ' + '-'.join(number)), path.name


def test_integration_decision_numbers_are_contiguous():
    """New integration decisions must fill gaps after the master review record."""
    numbers = sorted(int(path.name.split('-', 2)[1])
                     for path in (ROOT / 'specs/decisions').glob('DR-*.md'))
    integration = [number for number in numbers if number >= 159]
    assert integration == list(range(159, max(integration) + 1))


def test_acceptance_requirements_reference_existing_behavior_tests():
    spec = json.loads((ROOT / "specs/acceptance.json").read_text())
    requirements = spec["requirements"]
    assert requirements and len({r["id"] for r in requirements}) == len(requirements)
    for requirement in requirements:
        assert requirement["criterion"] and requirement["tests"]
        for reference in requirement["tests"]:
            filename, name = reference.split("::")
            path = (ROOT / filename).resolve()
            assert path.is_relative_to(ROOT / "tests"), reference
            functions = {node.name for node in ast.walk(ast.parse(path.read_text()))
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name in functions and name.startswith("test_"), reference


def test_delivered_requirements_do_not_list_missing_planned_tests():
    """已交付的需求不能再挂着计划阶段、实际没有写出来的测试(读者会以为它们存在)。"""
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    reqs = json.loads((root / "specs" / "acceptance.json").read_text(encoding="utf-8"))["requirements"]
    missing = [(r["id"], t) for r in reqs if r.get("status") == "delivered" for t in r.get("planned_tests") or []
               if not (root / t.split("::")[0]).is_file()]
    assert not missing, missing


STATUS_SENTENCE = re.compile(r'(?P<scope>.*?)实现交付状态：(?:delivered|planned|in_progress|withdrawn)')


def test_decision_delivery_status_states_only_its_value():
    """A DR's delivery status is a bare value at the end of its line, with no test or regression notes — DR-193."""
    findings = []
    for path in sorted((ROOT / 'specs/decisions').glob('DR-*.md')):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            # Quoted examples and code spans describe the format; only declarations are checked.
            text = re.sub(r'“[^”]*”|`[^`]*`', '', line).strip()
            if text.startswith('#') or '交付状态' not in text:
                continue
            sentences = [s for s in text.split('。') if s.strip()]
            for index, sentence in enumerate(sentences):
                if not re.search(r'交付状态[：为]', sentence):
                    continue
                match = STATUS_SENTENCE.fullmatch(sentence.strip())
                scope = match['scope'] if match else ''
                if (not match or any(mark in scope for mark in ('（', '(', '回归', '通过', '测试'))
                        or index != len(sentences) - 1):
                    findings.append(f'{path.name}:{number}: {sentence.strip()[:60]}')
    for number, line in enumerate((ROOT / 'specs/map.md').read_text(encoding='utf-8').splitlines(), 1):
        if '交付状态' in line and ('回归' in line or '通过' in line):
            findings.append(f'map.md:{number}: delivery status carries regression notes')
    assert not findings, '\n'.join(findings)

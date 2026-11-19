"""Keep local review materials outside the published product tree — DR-160."""
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_TUTORIAL = 'docs/TUTORIAL-llm-build-qa.md'
LOCAL_MATERIALS = (
    'docs/audits/*', 'docs/architecture/datamind-robotics*-integration.drawio',
    'docs/ui-prototype/*', 'docs/datamind-application-contract-design.md',
    'docs/datamind-application-contract-system-design.docx', 'specs/PROPOSAL-*.md',
    'specs/sources/*', 'specs/contracts/*', 'specs/merge-numbering.md',
    'specs/audit-input-consumers.md',
)


def local_material(name):
    path = PurePosixPath(name)
    if path.parent == PurePosixPath('docs') and path.suffix in {'.md', '.doc', '.docx'}:
        return name != PUBLIC_TUTORIAL
    return any(fnmatchcase(name, pattern) for pattern in LOCAL_MATERIALS)


@pytest.mark.parametrize('name, expected', [
    ('docs/local-notes.md', True), ('docs/local-notes.doc', True), ('docs/local-notes.docx', True),
    (PUBLIC_TUTORIAL, False), ('docs/pipelines/example.md', False),
    ('docs/pipelines/ontology_build.yaml', False), ('docs/img/example.png', False),
])
def test_root_document_boundary_preserves_tutorial_and_subdirectories(name, expected):
    assert local_material(name) is expected


def tracked_files():
    return subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')


def test_development_materials_are_not_tracked():
    findings = [name for name in tracked_files() if local_material(name)]
    assert not findings, '\n'.join(findings)


@pytest.mark.parametrize('name', [
    'docs/audits/example.json', 'docs/architecture/datamind-robotics-integration.drawio',
    'docs/architecture/datamind-robotics-source-integration.drawio',
    'docs/ui-prototype/example.html', 'docs/datamind-application-contract-design.md',
    'docs/datamind-application-contract-system-design.docx', 'specs/PROPOSAL-example.md',
    'specs/sources/example.json', 'specs/contracts/example.md', 'specs/merge-numbering.md',
    'specs/audit-input-consumers.md',
    'docs/local-notes.md', 'docs/local-notes.doc', 'docs/local-notes.docx',
])
def test_local_material_paths_are_ignored(name):
    result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', name], cwd=ROOT, check=False)
    assert result.returncode == 0, name


@pytest.mark.parametrize('name', [
    'specs/decisions/DR-001-example.md', 'specs/iterations/IR-001-example.md',
    'specs/models/valid_rows_rules.json', 'docs/pipelines/ontology_build.yaml',
    PUBLIC_TUTORIAL, 'docs/pipelines/example.md', 'docs/img/example.png',
])
def test_retained_specifications_are_not_ignored(name):
    result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', name], cwd=ROOT, check=False)
    assert result.returncode == 1, name


def test_published_markdown_does_not_link_to_local_materials():
    findings = []
    tracked = set(filter(None, tracked_files()))
    for name in sorted(tracked):
        if not name.endswith('.md') or local_material(name):
            continue
        path = ROOT / name
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for target in re.findall(r'!?\[[^\[\]]+\]\(([^)\s]+)\)', line):
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                destination = (path.parent / unquote(parsed.path)).resolve()
                if destination.is_relative_to(ROOT):
                    relative = destination.relative_to(ROOT).as_posix()
                    published = relative in tracked or any(p.startswith(relative + '/') for p in tracked)
                    if local_material(relative) or not published:
                        findings.append(f'{name}:{number}: {target}')
    assert not findings, '\n'.join(findings)

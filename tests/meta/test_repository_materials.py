"""Keep local review materials outside the published product tree — DR-160."""
from fnmatch import fnmatchcase
from fnmatch import filter as match_paths
from pathlib import Path, PurePosixPath
import re
import shlex
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


@pytest.mark.parametrize('pattern', [
    r'(?:尚未|没有|未|无|不)(?:执行)?(?:提交(?:\s*Git\b|[、或]推送)|\s*Git\s*提交)',
    r'(?:确认本机[^。；]*后[，,]|实际\s*\d+\s*服务已)[^。；]*(?:重启|重载|重新加载)'
    r'|(?:重启|重载|重新加载)后[^。；]*(?:回读|读取)原失败任务',
], ids=['git-progress', 'local-service-receipt'])
def test_public_specs_exclude_transient_operation_reports(pattern):
    """Check known operation reports across public specifications, including their index."""
    findings = []
    for name in tracked_files():
        if not name.startswith('specs/') or not name.endswith('.md'):
            continue
        for number, line in enumerate((ROOT / name).read_text().splitlines(), 1):
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(f'{name}:{number}')
    assert not findings, '\n'.join(findings)


@pytest.mark.parametrize('record, entry_point', [
    ('IR-016', 'scripts/demo_robotics.py'),
    ('IR-027', 'tests/unit/test_ontology_detail_ui.py'),
    ('IR-029', 'tests/unit/test_asset_lifecycle.py'),
    ('IR-030', 'tests/unit/test_merge_lifecycle_regressions.py'),
    ('IR-031', 'tests/frontend/robotics-interactive-ontology-graph-browser.py'),
    ('IR-036', 'tests/frontend/robotics-task-browser.py'),
])
def test_iteration_replay_uses_published_test_files(record, entry_point):
    tracked = tracked_files()
    name, = match_paths(tracked, f'specs/iterations/{record}-*.md')
    commands = re.findall(r'^```(?:sh|bash)\n(.*?)^```', (ROOT / name).read_text(), re.M | re.S)
    paths = set()
    for block in commands:
        for line in block.replace('\\\n', ' ').splitlines():
            args = shlex.split(line, comments=True)
            if not args or Path(args[0]).name not in {'python', 'python3'}:
                continue
            if len(args) > 1 and args[1].endswith('.py'):
                paths.add(args[1])
            if args[1:2] == ['scripts/service.py']:
                assert not {'start', 'stop', 'restart'}.intersection(args[2:]), name
            paths.update(arg for arg in args if arg.startswith(('scripts/', 'tests/')) and arg.endswith('.py'))
    assert entry_point in paths, name
    missing = sorted(path for path in paths if not match_paths(tracked, path))
    assert not missing, (name, missing)


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

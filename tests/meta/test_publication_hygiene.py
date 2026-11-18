"""Publication boundary for tracked product materials, including office-document XML."""
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


def test_tracked_materials_respect_publication_boundary():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    findings = []
    for name in filter(None, names):
        if name == 'AGENTS.md' or name.startswith('.agents/'):
            findings.append(name + ': local workflow')
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix == '.docx':
            with ZipFile(path) as archive:
                content = '\n'.join(archive.read(n).decode('utf-8', errors='replace')
                                    for n in archive.namelist() if n.endswith('.xml'))
        else:
            content = path.read_bytes().decode('utf-8', errors='replace')
        if re.search(r'/(?:home|Users)/[\w.-]+/', content):
            findings.append(name + ': personal absolute path')
        if re.search(r'(?:Codex|ChatGPT|AI 工具)[^\n<]{0,35}(?:整理|撰写|编写)', content):
            findings.append(name + ': tool authorship')
        if path.suffix == '.md' and re.search(r'\]\([^)]*\.check-results[^)]*\)', content):
            findings.append(name + ': unpublished evidence link')
    assert not findings, '\n'.join(findings)


def test_encoded_fixture_blobs_use_only_synthetic_pose_types():
    import base64
    import json
    failures = []
    for path in (ROOT / 'tests/fixtures/domain_storage').glob('*.json'):
        for blob in json.loads(path.read_text()).get('blobs', {}).values():
            if 'base64' not in blob:
                continue
            content = base64.b64decode(blob['base64']).decode('utf-8', errors='replace')
            if re.search(r'\b(?!demo\b|std\b)[A-Za-z_]\w*::Pose(?:Quat|RPY)\b', content):
                failures.append(path.name)
    assert not failures, failures


def test_published_demo_parameters_are_synthetic():
    import json
    parameters = json.loads((ROOT / 'tests/fixtures/robotics/tasks/public_parameters.json').read_text())
    assert parameters['Perception']['object_name'] == 'sample_part'
    assert parameters['Perception']['model_name'] == 'sample_model'
    assert parameters['Perception']['position'] == '[0.1,0.2,0.3,0,0,0]'
    assert parameters['Place']['place_pose'] == '[100,200,300,1,0,0,0]'

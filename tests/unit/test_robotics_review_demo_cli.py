"""T06 RED: offline governance demo and independent read-only verification — DR-061 / DR-064 / DR-065."""
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

import pytest
from rdflib import Graph

from domain_runs import RunStore
from domains.robotics.evidence import evaluate_evidence
from ontology_assets import AssetStore
from ontology_assets.governance import GovernanceError, GovernanceService
from tests.robotics_governance_support import save_args
from tests.robotics_support import S, SOURCE, MODELS
from tests.build_pipeline_support import sha

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/demo_robotics_review.py'
CASES = ['incomplete-publication', 'concurrent-edit', 'rebuild-conflict', 'tamper-detection']
GUARD = '''import json, os, sys
with open(os.environ['REVIEW_TEST_PROCESS_LOG'], 'a') as stream:
    stream.write(json.dumps({'pid':os.getpid(),'ppid':os.getppid()})+'\\n')
def audit(event,args):
    if event in ('socket.connect','socket.getaddrinfo'):
        raise AssertionError('Offline governance demo must not access the network')
    if event == 'open':
        name=str(args[0]).replace('\\\\','/')
        if 'yang_implementation/' in name or '/fixtures/robotics/owl/' in name:
            raise AssertionError('Must build fresh from frozen XML, not archive/prebuilt OWL')
    if event == 'import' and args[0] == 'server':
        raise AssertionError('Offline demo must not import the live application')
sys.addaudithook(audit)
'''
_TEMPLATE = None
_HOMES = []


def run_cli(output, *, case='all', verify=None, extra=()):
    assert SCRIPT.is_file(), 'T07 must deliver scripts/demo_robotics_review.py'
    guard = output.parent / '.review-cli-guard'
    guard.mkdir(parents=True, exist_ok=True)
    (guard / 'sitecustomize.py').write_text(GUARD)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DATAMIND_', 'BT_AGENT_')) and 'API_KEY' not in k}
    env.update(PYTHONPATH=os.pathsep.join((str(guard), str(ROOT))),
               REVIEW_TEST_PROCESS_LOG=str(guard / 'processes.jsonl'), DATAMIND_WORKDIR=str(output.parent / 'legacy'))
    args = ['--verify', str(verify)] if verify is not None else ['--case', case, '--output-dir', str(output)]
    return subprocess.run([sys.executable, str(SCRIPT), *args, *extra], cwd=output.parent, env=env,
                          capture_output=True, text=True, timeout=240)


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}


def summary(output):
    body = json.loads((output / 'summary.json').read_text())
    assert body['schema_version'] == 1 and body['storage_root'] == 'storage'
    assert body['passed'] is True and (output / 'summary.md').is_file()
    return body


def copy_demo(tmp_path):
    """Build one real all-case template per pytest process; each mutation gets its own byte copy."""
    global _TEMPLATE
    assert SCRIPT.is_file(), 'T07 must deliver scripts/demo_robotics_review.py'
    if _TEMPLATE is None:
        home = tempfile.TemporaryDirectory(prefix='review-cli-template-')
        _HOMES.append(home)
        directory = Path(home.name) / 'demo'
        result = run_cli(directory)
        assert result.returncode == 0, result.stdout + result.stderr
        summary(directory)
        _TEMPLATE = directory
    target = tmp_path / 'demo'
    shutil.copytree(_TEMPLATE, target)
    return target, summary(target)


def validate_case(output, row):
    root = output / 'storage'
    assets = AssetStore(root, read_only=True)
    runs = RunStore(root, read_only=True)
    oid, vid = row['ontology_id'], row['version_id']
    base = assets.get_version(oid, row['base_version_id'])
    version = assets.get_version(oid, vid)
    assert row['passed'] is True and base['version_id'] != vid
    assert sha(assets.read_payload(oid, base['version_id'])) == base['payload_sha256']
    assert sha(assets.read_payload(oid, vid)) == version['payload_sha256']
    built = runs.get_run(base['metadata']['build_run_id'])
    assert built['status'] == 'completed' and built['operation'] == 'build_ontology'
    assert built['output_asset_ref'] == {'ontology_id': oid, 'version_id': base['version_id']}
    sources = {assets.get_source(s)['role']: assets.read_source(s) for s in base['source_asset_ids']}
    assert sources == {'behavior_tree': SOURCE.read_bytes(), 'node_models': MODELS.read_bytes()}
    service = GovernanceService(root, read_only=True)
    if row['case'] in ('incomplete-publication', 'tamper-detection'):
        assert version['publication_state'] == 'published'
        records = service.artifacts(oid, vid)
        assert {a['kind'] for a in records} == {'ontology', 'validation', 'manifest'}
        for record in records:
            raw = service.read_artifact(oid, vid, record['artifact_id'])
            assert sha(raw) == record['sha256'] and len(raw) == record['byte_size']
            if record['kind'] != 'ontology':
                document = json.loads(raw)
                assert document['version_id'] == vid and document['payload_sha256'] == version['payload_sha256']
                assert document['review_id'] == row['review_id']
                assert document['execution_readiness'] == 'not_verified'
        with assets.storage.transaction() as conn:
            reviewed = json.loads(conn.execute('SELECT record FROM governance_reviews WHERE review_id=?', (row['review_id'],)).fetchone()[0])
        assert reviewed['decision'] == 'accepted' and reviewed['version_id'] == vid
        assert reviewed['actor'] and reviewed['reason']
    if row['case'] == 'incomplete-publication':
        run = runs.get_run(row['run_id'])
        assert run['version_id'] == vid and run['mode'] == 'real'
        assert run['result']['status'] == 'needs_information' and run['result']['issues']
        assert run['result']['execution_readiness'] == 'not_verified'
        assert not [a for a in runs.artifacts(run['run_id']) if a['kind'] == 'candidate']
        assert runs.manifest(run['run_id'])['version_id'] == vid
    elif row['case'] == 'concurrent-edit':
        assert row['loser_error'] == 'head_conflict'
        assert len(assets.list_versions(oid)) == 2
        assert len([e for e in service.history(oid) if e['kind'] == 'save']) == 1
    elif row['case'] == 'rebuild-conflict':
        assert version['publication_state'] == 'candidate' and version['metadata']['merge']['decisions']
        incoming = assets.get_version(**{k: version['metadata']['merge']['incoming'][k] for k in ('ontology_id', 'version_id')})
        assert incoming['source_asset_ids'][0] in version['source_asset_ids']
        graph = Graph().parse(data=assets.read_payload(oid, vid), format='xml')
        assert str(graph.value(S.Perception_Current, S.threshold)) == '0.71'
        assert row['evidence_review_required'] is True and row['conflict_ids']
        assert set(row['conflict_ids']) == set(version['metadata']['merge']['decisions'])
        result = evaluate_evidence(graph, row['evidence_iri'], revocations=service.revocations(oid))
        assert not result['valid'] and result['issues']
    elif row['case'] == 'tamper-detection':
        assert row['probe_exit_code'] != 0 and row['tamper_probe'] == 'tamper-probe'
        probe = output / row['tamper_probe']
        assert (probe / 'summary.json').is_file()
        probe_body = json.loads((probe / 'summary.json').read_text())
        probe_store = GovernanceService(probe / probe_body['storage_root'], read_only=True)
        with pytest.raises(GovernanceError):
            probe_store.read_artifact(oid, vid, row['damaged_artifact_id'])


def test_all_offline_cases_build_fresh_sources_and_independently_verify(tmp_path):
    before = {str(p): p.read_bytes() for p in (SOURCE, MODELS)}
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    sentinel = legacy / 'ont_chats.json'
    sentinel.write_text('{"sentinel":"keep"}')
    output = tmp_path / 'demo'
    result = run_cli(output)
    assert result.returncode == 0, result.stdout + result.stderr
    data = summary(output)
    assert {r['case'] for r in data['cases']} == set(CASES)
    assert data['verification']['passed'] and data['producer_pid'] != data['verification']['pid']
    observed = {json.loads(line)['pid'] for line in (tmp_path / '.review-cli-guard/processes.jsonl').read_text().splitlines()}
    assert {data['producer_pid'], data['verification']['pid']} <= observed
    for row in data['cases']: validate_case(output, row)
    assert sentinel.read_text() == '{"sentinel":"keep"}'
    assert all(Path(p).read_bytes() == raw for p, raw in before.items())
    before_files = snapshot(output)
    verified = run_cli(output, verify=output)
    assert verified.returncode == 0, verified.stdout + verified.stderr
    answer = json.loads(verified.stdout)
    assert answer['passed'] and answer['pid'] != data['producer_pid']
    assert {(v['ontology_id'], v['version_id']) for v in answer['verified_versions']} == {
        (a['ontology_id'], v['version_id']) for a in AssetStore(output / 'storage', read_only=True).list_assets()
        for v in AssetStore(output / 'storage', read_only=True).list_versions(a['ontology_id'])}
    assert snapshot(output) == before_files


@pytest.mark.parametrize('case', CASES)
def test_offline_case_can_be_run_individually(tmp_path, case):
    output = tmp_path / 'demo'
    result = run_cli(output, case=case)
    assert result.returncode == 0, result.stdout + result.stderr
    data = summary(output)
    assert [r['case'] for r in data['cases']] == [case]
    validate_case(output, data['cases'][0])


def test_nonempty_output_is_rejected_without_replacing_any_files(tmp_path):
    output = tmp_path / 'demo'
    output.mkdir()
    (output / 'existing.json').write_text('{"keep":true}')
    before = snapshot(output)
    result = run_cli(output, case='incomplete-publication')
    assert result.returncode != 0
    assert snapshot(output) == before


@pytest.mark.parametrize('exists', [False, True])
def test_verify_does_not_initialize_missing_or_empty_output(tmp_path, exists):
    output = tmp_path / 'demo'
    if exists: output.mkdir()
    result = run_cli(output, verify=output)
    assert result.returncode != 0 and output.exists() is exists
    assert not list(output.rglob('*'))


@pytest.mark.parametrize('target', ['owl', 'manifest', 'source', 'missing_artifact', 'review', 'summary_lie'])
def test_independent_verifier_rejects_tampering_without_repair(tmp_path, target):
    output, data = copy_demo(tmp_path)
    row = next(r for r in data['cases'] if r['case'] == 'incomplete-publication')
    root = output / 'storage'
    assets = AssetStore(root, read_only=True)
    version = assets.get_version(row['ontology_id'], row['version_id'])
    records = GovernanceService(root, read_only=True).artifacts(row['ontology_id'], row['version_id'])
    if target == 'review':
        with sqlite3.connect(root / 'domain.sqlite3') as conn:
            record = json.loads(conn.execute('SELECT record FROM governance_reviews WHERE review_id=?', (row['review_id'],)).fetchone()[0])
            record['payload_sha256'] = '0' * 64
            conn.execute('UPDATE governance_reviews SET record=? WHERE review_id=?', (json.dumps(record), row['review_id']))
    else:
        if target == 'source': path = root / assets.get_source(version['source_asset_ids'][0])['path']
        elif target in ('manifest', 'missing_artifact'): path = root / next(a for a in records if a['kind'] == 'manifest')['path']
        else: path = root / version['path']
        if target == 'missing_artifact': path.unlink()
        else: path.write_bytes(b'corrupted registered bytes')
        if target == 'summary_lie':
            data['cases'] = []
            data['passed'] = True
            (output / 'summary.json').write_text(json.dumps(data))
    before = snapshot(output)
    verified = run_cli(output, verify=output)
    assert verified.returncode != 0, verified.stdout + verified.stderr
    assert snapshot(output) == before


def test_verification_is_read_only_even_with_a_running_task(tmp_path):
    output, data = copy_demo(tmp_path)
    row = next(r for r in data['cases'] if r['case'] == 'incomplete-publication')
    root = output / 'storage'
    runs = RunStore(root)
    completed = runs.get_run(row['run_id'])
    queued = runs.create_run(**{k: completed[k] for k in ('ontology_id', 'version_id', 'operation', 'task', 'mode', 'session_ref', 'components')})
    active = runs.start(queued['run_id'])
    before = snapshot(output)
    result = run_cli(output, verify=output)
    assert result.returncode == 0, result.stdout + result.stderr
    assert runs.get_run(active['run_id']) == active and snapshot(output) == before


def test_cli_conflict_case_is_an_actual_persisted_cas_failure(tmp_path):
    output, data = copy_demo(tmp_path)
    row = next(r for r in data['cases'] if r['case'] == 'concurrent-edit')
    service = GovernanceService(output / 'storage')
    base = service.assets.get_version(row['ontology_id'], row['base_version_id'])
    with pytest.raises(GovernanceError) as caught:
        service.save(row['ontology_id'], **save_args(base, request_id='independent-stale-probe'))
    assert caught.value.code == 'head_conflict'


@pytest.mark.parametrize('failure', ['reported_failure', 'exception'])
def test_cli_exit_code_follows_real_demo_failure(tmp_path, monkeypatch, failure):
    assert SCRIPT.is_file(), 'T07 must deliver the CLI before this failure-injection check'
    cli = importlib.import_module('scripts.demo_robotics_review')
    def failed(*args, **kwargs):
        if failure == 'exception': raise RuntimeError('synthetic runtime failure')
        return {'passed': False, 'storage_root': 'storage', 'cases': [], 'verification': {'passed': False}}
    monkeypatch.setattr(cli, 'run_demo', failed)
    assert cli.main(['--case', 'all', '--output-dir', str(tmp_path / 'demo')]) != 0

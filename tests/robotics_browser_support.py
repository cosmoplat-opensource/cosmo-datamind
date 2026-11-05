"""Isolated actual HTTP/browser evidence helpers, never route fulfilment — DR-063 / DR-064 / DR-065."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
import uuid

from domain_runs import RunStore
from ontology_assets import AssetStore
from scripts.check import isolated_env
from tests.asset_support import data_bytes
from tests.robotics_support import SOURCE, MODELS

ROOT = Path(__file__).resolve().parents[1]


def sha(content):
    return hashlib.sha256(content).hexdigest()


def wait_for(check, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Real backend did not reach the expected state in time')


class LiveSite:
    """Own a disposable Gunicorn process and root; restarting keeps real persisted data."""
    def __init__(self, directory, output, *, enabled=True, hold_owner=False):
        self.directory, self.output = Path(directory), Path(output)
        self.directory.mkdir(parents=True)
        self.output.mkdir(parents=True, exist_ok=True)
        self.env = isolated_env(self.directory)
        self.root = self.directory / 'assets'
        self.work = self.directory / 'workdir'
        self.enabled = enabled
        self.process = None
        self.owner_fd = None
        self.log = (self.output / 'server.log').open('w')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}'
        self.env.update(DATAMIND_PORT=str(self.port), DATAMIND_ROBOTICS_ENABLED='1' if enabled else '0',
                        DATAMIND_DOMAIN_STORAGE_ROOT=str(self.root), DATAMIND_ROBOTICS_TIMEOUT_SECONDS='30')
        self.evidence = {'scope': 'actual Gunicorn + Chromium; no route mocking', 'assertions_completed': False,
                         'requests': [], 'responses': [], 'page_errors': [], 'runs': [], 'downloads': []}
        if hold_owner:
            import fcntl
            from domain_runs.build_service import BuildService
            BuildService(self.root)
            self.owner_fd = os.open(self.root / '.build-executor.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
            fcntl.flock(self.owner_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.start()
        except Exception:
            self.close()
            raise

    def start(self):
        self.process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '-c', 'gunicorn.conf.py', 'wsgi:application'],
            cwd=ROOT, env=self.env, stdout=self.log, stderr=self.log, start_new_session=True)
        def ready():
            assert self.process.poll() is None, 'Real Gunicorn exited; see server.log'
            try:
                return self.get('/api/robotics/capabilities')['enabled'] == self.enabled
            except OSError:
                return False
        wait_for(ready, seconds=20)

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
            self.process = None

    def restart(self):
        self.stop()
        self.start()
        self.evidence['restarted'] = True

    def release_owner(self):
        if self.owner_fd is not None:
            os.close(self.owner_fd)
            self.owner_fd = None

    def close(self):
        self.stop()
        self.release_owner()
        self.log.close()
        (self.output / 'evidence.json').write_text(json.dumps(self.evidence, ensure_ascii=False, indent=2)+'\n')

    def get(self, path):
        with urlopen(self.url + path, timeout=10) as reply:
            return json.load(reply)

    def submit(self, *, source=None, models=None, name='实际后端准备的候选'):
        boundary = 'robotics-test-' + uuid.uuid4().hex
        parts = []
        fields = {'name': name, 'recipe': 'bt_xml', 'profile': 'ur_pick_place', 'request_id': uuid.uuid4().hex}
        for key, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        for role, data in [('behavior_tree', SOURCE.read_bytes() if source is None else source),
                           ('node_models', MODELS.read_bytes() if models is None else models)]:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{role}"; filename="same.xml"\r\nContent-Type: application/xml\r\n\r\n'.encode()+data+b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        request = Request(self.url+'/api/robotics/builds', data=b''.join(parts),
                          headers={'Content-Type': 'multipart/form-data; boundary='+boundary})
        with urlopen(request, timeout=10) as reply:
            assert reply.status == 202
            row = json.load(reply)
        return self.terminal(row['run_id'])

    def terminal(self, rid):
        def read():
            row = RunStore(self.root, read_only=True).get_run(rid)
            return row if row['status'] in ('completed', 'failed', 'interrupted') else None
        row = wait_for(read)
        self.evidence['runs'].append({'run_id': rid, 'status': row['status'], 'sources': row['build_input']['sources'],
                                     'output_asset_ref': row['output_asset_ref']})
        return row

    def observe(self, page):
        page.on('request', lambda r: self.evidence['requests'].append({'method': r.method, 'url': r.url}))
        page.on('response', lambda r: self.evidence['responses'].append({'status': r.status, 'url': r.url}))
        page.on('pageerror', lambda error: self.evidence['page_errors'].append(str(error)))

    def posts(self):
        return [r for r in self.evidence['requests'] if r['method'] == 'POST'
                and ('/api/robotics/builds' in r['url'] or '/retry' in r['url'])]

    def seed_legacy(self):
        (self.work / 'built_ui_fixture.json').write_bytes(data_bytes())
        return 'built_ui_fixture'

    def record_download(self, row, artifact, path):
        data = Path(path).read_bytes()
        runs = RunStore(self.root, read_only=True)
        assert data == runs.read_artifact(row['run_id'], artifact['artifact_id'])
        assert sha(data) == artifact['sha256'] and len(data) == artifact['byte_size']
        if artifact['kind'] == 'ontology':
            assert data == AssetStore(self.root, read_only=True).read_payload(**row['output_asset_ref'])
        self.evidence['downloads'].append({'run_id': row['run_id'], 'artifact_id': artifact['artifact_id'],
            'kind': artifact['kind'], 'sha256': sha(data), 'byte_size': len(data), 'saved_path': str(path)})


def choose_robotics(page, site):
    page.goto(site.url+'/#build', wait_until='domcontentloaded')
    selector = page.get_by_label('本体构建配方', exact=True)
    selector.wait_for(state='visible', timeout=2500)
    selector.select_option('robotics')
    page.get_by_label('主行为树 XML', exact=True).wait_for(state='visible')
    assert page.get_by_label('构建模板', exact=True).input_value() == 'ur_pick_place'


def fill_robotics(page, *, name='浏览器构建候选', source=None, models=None, filename='same.xml'):
    page.get_by_label('机器人本体名称', exact=True).fill(name)
    page.get_by_label('主行为树 XML', exact=True).set_input_files(
        {'name': filename, 'mimeType': 'application/xml', 'buffer': SOURCE.read_bytes() if source is None else source})
    page.get_by_label('节点模型 XML', exact=True).set_input_files(
        {'name': filename, 'mimeType': 'application/xml', 'buffer': MODELS.read_bytes() if models is None else models})


def submit_from_ui(page, site, *, keyboard=False):
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url == site.url+'/api/robotics/builds') as pending:
        button = page.get_by_role('button', name='构建机器人本体', exact=True)
        if keyboard:
            button.focus()
            page.keyboard.press('Enter')
        else:
            button.click()
    reply = pending.value
    assert reply.status == 202, reply.text()
    return reply.json()['run_id']


def wait_run_ui(page, rid, state):
    from playwright.sync_api import expect
    panel = page.get_by_test_id('robotics-run')
    expect(panel).to_have_attribute('data-run-id', rid)
    expect(panel).to_have_attribute('data-status', state, timeout=40000)
    return panel


def wait_version_ui(page, ref):
    from playwright.sync_api import expect
    panel = page.get_by_test_id('robotics-version')
    expect(panel).to_be_visible(timeout=2500)
    expect(panel).to_have_attribute('data-ontology-id', ref['ontology_id'])
    expect(panel).to_have_attribute('data-version-id', ref['version_id'])
    expect(panel).to_have_attribute('data-publication-state', 'candidate')
    return panel


def download_from_ui(page, site, row, kind):
    names = {'ontology': '下载 OWL', 'validation': '下载报告', 'manifest': '下载 manifest'}
    record = next(a for a in RunStore(site.root, read_only=True).artifacts(row['run_id']) if a['kind'] == kind)
    link = page.get_by_role('link', name=names[kind], exact=True)
    assert link.get_attribute('href') == f"/api/domain-runs/{row['run_id']}/artifacts/{record['artifact_id']}"
    with page.expect_download() as pending:
        link.click()
    path = site.output / pending.value.suggested_filename
    pending.value.save_as(path)
    site.record_download(row, record, path)
    return path

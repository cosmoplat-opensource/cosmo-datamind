"""Isolated HTTP/process fixtures and proposed runtime boundary — DR-060 / DR-063 / DR-064 / DR-065."""
from contextlib import contextmanager
import importlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from flask import Flask
from werkzeug.datastructures import MultiDict

from domain_runs import RunStore
from tests.build_pipeline_support import failure_result
from tests.robotics_support import SOURCE, MODELS

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = 'http://127.0.0.1'


def runtime_api():
    # Missing modules fail individual cases, not collection; never importorskip.
    return importlib.import_module('domain_runs.build_service'), importlib.import_module('domain_runs.executor')


def service(root, *, max_pending=4, file_limit_bytes=10 * 1024 * 1024):
    api, _ = runtime_api()
    return api.BuildService(root, max_pending=max_pending, file_limit_bytes=file_limit_bytes)


def executor(root, *, timeout_seconds=30, compute_fn=None):
    _, api = runtime_api()
    kwargs = {'timeout_seconds': timeout_seconds, 'poll_interval': 0.02}
    if compute_fn is not None:
        kwargs['compute_fn'] = compute_fn
    return api.BuildExecutor(root, **kwargs)


def upload_args(request_id='request-1', **changes):
    args = {'name': '抓移放候选', 'recipe': 'bt_xml', 'profile_id': 'ur_pick_place',
            'request_id': request_id,
            'sources': {'behavior_tree': {'filename': 'source.xml', 'content': SOURCE.read_bytes()},
                        'node_models': {'filename': 'source.xml', 'content': MODELS.read_bytes()}}}
    args.update(changes)
    return args


def multipart(request_id='request-1'):
    # Deliberately identical file names: roles come from field names, not names/extensions.
    return MultiDict([('name', '抓移放候选'), ('recipe', 'bt_xml'), ('profile', 'ur_pick_place'),
                      ('request_id', request_id),
                      ('behavior_tree', (io.BytesIO(SOURCE.read_bytes()), 'source.xml')),
                      ('node_models', (io.BytesIO(MODELS.read_bytes()), 'source.xml'))])


def post(client, data=None, **kwargs):
    return client.post('/api/robotics/builds', data=multipart() if data is None else data,
                       base_url=BASE_URL, **kwargs)


def wait_until(check, *, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError('Timed out waiting for the actual runtime condition')


def terminal(root, run_id, seconds=40):
    def read():
        row = RunStore(root).get_run(run_id)
        return row if row['status'] in ('completed', 'failed', 'interrupted') else None
    return wait_until(read, seconds=seconds)


@contextmanager
def http_app(tmp_path, *, enabled=True, start_executor=False, legacy=None, **config):
    module = importlib.import_module('bp_robotics')
    app = Flask('robotics-contract-test')
    root, work = tmp_path / 'assets', tmp_path / 'work'
    work.mkdir(parents=True, exist_ok=True)
    app.config.update(TESTING=True, MAX_CONTENT_LENGTH=25 * 1024 * 1024,
                      DATAMIND_ROBOTICS_ENABLED=enabled, DATAMIND_DOMAIN_STORAGE_ROOT=str(root),
                      DATAMIND_ROBOTICS_FILE_LIMIT_BYTES=10 * 1024 * 1024,
                      DATAMIND_ROBOTICS_MAX_PENDING=4, DATAMIND_ROBOTICS_TIMEOUT_SECONDS=30)
    app.config.update(config)
    # Reuse the real application guards; do not invent test-only replacements.
    import server
    for guard in (server._host_guard, server._csrf_guard, server._json_object_guard):
        app.before_request(guard)
    runtime = module.init_app(app, workdir=work, legacy_catalog=(lambda: [] if legacy is None else legacy),
                              start_executor=start_executor)
    try:
        yield app, app.test_client(), runtime, root
    finally:
        runtime.close()


def control_path(root, run_id, suffix):
    directory = Path(root) / '_test_process_control'
    directory.mkdir(exist_ok=True)
    return directory / (run_id + '.' + suffix)


def controlled_compute(root, run_id):
    """Injected only by trusted Python tests, never selectable via HTTP/config.

    Writes PID/handshake files for observing a real child. A finite gate avoids
    long sleeps and makes timeout/death tests independent of RDF execution speed.
    """
    started = control_path(root, run_id, 'started')
    temporary = started.with_suffix('.writing')
    temporary.write_text(json.dumps({'pid': os.getpid(), 'ppid': os.getppid()}))
    temporary.replace(started)
    mode_file = control_path(root, run_id, 'mode')
    mode = mode_file.read_text() if mode_file.exists() else 'real'
    if mode in ('block', 'block-error'):
        deadline = time.monotonic() + 45
        while not control_path(root, run_id, 'release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('Test gate deadline exceeded')
            time.sleep(0.01)
    if mode == 'crash':
        os._exit(23)
    if mode == 'exception':
        raise RuntimeError(str(Path(root) / 'private/internal.owl'))
    if mode in ('error', 'block-error'):
        return {'result': failure_result('test_failure'), 'ontology': None}
    if mode == 'bad-result':
        return {'result': {'status': 'built'}, 'ontology': b'not an ontology'}
    return importlib.import_module('domain_runs.robotics_build').compute_build(root, run_id)


def child_info(root, run_id):
    path = control_path(root, run_id, 'started')
    return wait_until(lambda: json.loads(path.read_text()) if path.exists() else None, seconds=10)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@contextmanager
def running_executor(root, **kwargs):
    worker = executor(root, **kwargs)
    worker.start()
    try:
        wait_until(lambda: worker.is_owner, seconds=10)
        yield worker
    finally:
        worker.stop()


@contextmanager
def owner_process(root):
    runtime_api()  # Make an absent implementation a direct RED failure.
    env = dict(os.environ, DATAMIND_WORKDIR=str(root.parent / 'owner-work'), PYTHONPATH=str(ROOT))
    process = subprocess.Popen([sys.executable, '-m', 'tests.build_runtime_support', 'owner', str(root)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        wait_until(lambda: (root / '_test_owner_ready').exists() or process.poll() is not None, seconds=10)
        assert process.poll() is None, process.communicate(timeout=5)
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        # Parent was intentionally killed in crash cases; reclaim only recorded test children.
        for path in (root / '_test_process_control').glob('*.started'):
            pid = json.loads(path.read_text())['pid']
            if alive(pid):
                os.kill(pid, signal.SIGKILL)


if __name__ == '__main__':
    assert sys.argv[1] == 'owner'
    storage_root = Path(sys.argv[2])
    worker = executor(storage_root, timeout_seconds=30, compute_fn=controlled_compute)
    worker.start()
    wait_until(lambda: worker.is_owner, seconds=10)
    (storage_root / '_test_owner_ready').write_text(str(os.getpid()))
    try:
        while True:
            time.sleep(0.1)
    finally:
        worker.stop()

"""Single-owner local build supervisor with disposable compute children — DR-064 / DR-065."""
import fcntl
import logging
import math
import multiprocessing
import os
import sqlite3
from pathlib import Path
import threading
import time

from domains.robotics.source_validation import issue
from srv_context import confine

from .robotics_build import compute_build
from .store import RunStore

_LOG = logging.getLogger(__name__)


def _failure(code, message):
    return {'result': {'status': 'error', 'execution_readiness': 'not_verified',
                      'task_readiness': 'not_assessed', 'issues': [issue(code, message, None)]}, 'ontology': None}


def _compute_child(sender, compute_fn, root, run_id):
    """Spawn imports this module only, never the web app or its executor lock."""
    try:
        try:
            result = compute_fn(root, run_id)
        except Exception:
            result = _failure('worker_error', '计算进程发生异常，未生成候选。')
        sender.send(result)
    finally:
        sender.close()


class BuildExecutor:
    def __init__(self, root, *, timeout_seconds=300, poll_interval=0.1, compute_fn=None):
        if (isinstance(timeout_seconds, bool) or not math.isfinite(float(timeout_seconds))
                or not 0 < float(timeout_seconds) <= 3600):
            raise ValueError('构建超时必须是有限正数且不超过 3600 秒。')
        if not math.isfinite(float(poll_interval)) or not 0 < float(poll_interval) <= 10:
            raise ValueError('轮询间隔必须为有限正数且不超过 10 秒。')
        self.root = Path(root).resolve()
        self.runs = RunStore(root)
        self.timeout_seconds, self.poll_interval = float(timeout_seconds), float(poll_interval)
        self.compute_fn = compute_build if compute_fn is None else compute_fn
        self._stop = threading.Event()
        self._owner = threading.Event()
        self._thread = None
        self._lifecycle = threading.Lock()
        # spawn avoids inheriting SQLite/RDF locks or a file-lock descriptor from gthread.
        self._context = multiprocessing.get_context('spawn')

    @property
    def is_owner(self):
        return self._owner.is_set()

    def start(self):
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._serve, name='robotics-build-executor', daemon=True)
            self._thread.start()

    def stop(self):
        with self._lifecycle:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=15)
                if self._thread.is_alive():
                    # Retain ownership until a commit finishes; never allow a second writer.
                    raise RuntimeError('构建宿主正在完成持久提交，尚未释放执行权。')
                self._thread = None

    def _serve(self):
        lock = None
        active = None
        try:
            path = confine(self.root, '.build-executor.lock')
            lock = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            while not self._stop.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self._stop.wait(self.poll_interval)
            if self._stop.is_set():
                return
            # DR-064: recovery only after acquiring ownership, scoped to our operation.
            self.runs.recover_interrupted(operation='build_ontology')
            self._owner.set()
            while not self._stop.is_set():
                try:
                    row = self.runs.claim_build()
                    if row is None:
                        self._stop.wait(self.poll_interval)
                        continue
                    active = row['run_id']
                    output = self._compute(active)
                    if output is None or self._stop.is_set():
                        self.runs.interrupt_build(active)
                    else:
                        self._commit(active, output)
                    active = None
                except Exception:
                    _LOG.exception('机器人构建调度或持久提交失败')
                    if active is not None:
                        self.runs.interrupt_build(active)
                        active = None
                    self._stop.wait(self.poll_interval)
        except Exception:
            _LOG.exception('机器人构建宿主停止，等待后续宿主恢复')
        finally:
            self._owner.clear()
            if lock is not None:
                # close is enough: do not unlink a lock file another host may have opened.
                os.close(lock)

    def _compute(self, run_id):
        receiver, sender = self._context.Pipe(duplex=False)
        child = self._context.Process(target=_compute_child,
            args=(sender, self.compute_fn, self.root, run_id), name='robotics-compute', daemon=True)
        started = False
        deadline = time.monotonic() + self.timeout_seconds
        try:
            child.start()
            started = True
            sender.close()
            while not self._stop.is_set():
                if time.monotonic() >= deadline:
                    return _failure('build_timeout', '构建计算超过时间预算，已停止计算进程。')
                if receiver.poll(min(self.poll_interval, max(0, deadline - time.monotonic()))):
                    try:
                        result = receiver.recv()
                        return result if result is not None else _failure(
                            'invalid_build_result', '计算进程返回空结果。')
                    except EOFError:
                        return _failure('worker_crashed', '计算进程异常退出，未返回结果。')
                if not child.is_alive():
                    return _failure('worker_crashed', '计算进程异常退出，未返回结果。')
            return None
        finally:
            sender.close()
            receiver.close()
            if started:
                child.join(timeout=0.1)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=1)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=1)
                child.close()

    def _commit(self, run_id, output):
        try:
            if not isinstance(output, dict) or set(output) != {'result', 'ontology'}:
                raise ValueError('Invalid worker output')
            self.runs.append_event(run_id, kind='stage', payload={'stage': 'committing'})
            self.runs.finish_build(run_id, **output)
        except (ValueError, TypeError, KeyError):
            self.runs.finish_build(run_id, **_failure('invalid_build_result', '计算结果不满足构建提交契约。'))
        except (OSError, sqlite3.Error):
            self.runs.finish_build(run_id, **_failure('storage_error', '构建结果保存失败，未登记成功候选。'))

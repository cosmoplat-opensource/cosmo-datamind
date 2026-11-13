"""Single-owner design execution and explicit startup recovery — DR-070."""
import fcntl
import logging
import os
import threading
from pathlib import Path

from .robotics_design_service import DesignService

_LOG = logging.getLogger(__name__)


class DesignExecutor:
    def __init__(self, root):
        self.root = Path(root)
        self.service = DesignService(root)
        from .robotics_application_service import ApplicationService
        self.application = ApplicationService(root)
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.serve, daemon=True, name='robotics-design-executor')
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def serve(self):
        fd = os.open(self.root / '.design-executor.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            while not self.stop_event.is_set():
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.stop_event.wait(.1)
            if self.stop_event.is_set(): return
            self.service.recover_interrupted()
            for rid in self.application.runs.recover_interrupted(operation='adapt_execution'):
                with self.application._atomic(write=True) as conn:
                    run=self.application.runs.get_run(rid)
                    state=self.application._get(conn,run['session_ref']['id'])
                    state.update(status='interrupted',revision=state['revision']+1)
                    self.application._put(conn,state)
            while not self.stop_event.is_set():
                try:
                    run = self.service.claim_next()
                    if run:
                        self.service.execute(run['run_id'])
                    else:
                        adaptation=self.application.claim_next()
                        if adaptation:self.application.execute(adaptation['run_id'])
                        else:self.stop_event.wait(.1)
                except Exception:
                    _LOG.exception('设计运行执行失败，等待恢复')
                    self.stop_event.wait(.5)
        finally:
            os.close(fd)

"""Resume the frozen observer after balance-endpoint transport outages.

This runtime supervisor does not reissue finalized failed batches, change the
four-attempt inference budget, inspect answers, or repair ambiguous requests.
All other errors stop execution for review. The existing continuation process
consumes the observer's complete manifest independently.
"""

from datetime import datetime, timezone
import fcntl
import json
import os
import shutil
import time

from native_context_crops_v12 import DEST
from native_context_observe_v12 import run as observe
from prepare import save, sha


RETRYABLE_STOP = "Balance endpoint unavailable; inference queue not advanced"


def supervise(observer, record, *, sleep=time.sleep, clock=time.monotonic,
              retry_interval=30, maximum_wait=86400):
    """Only resume known pre-inference health-check transport failures."""
    started = clock()
    interruptions = 0
    while True:
        try:
            observer()
        except RuntimeError as exc:
            if str(exc) != RETRYABLE_STOP:
                raise
            interruptions += 1
            if clock() - started >= maximum_wait:
                record("recovery_wait_limit_reached", interruptions)
                raise
            record("waiting_for_balance_endpoint_recovery", interruptions)
            sleep(retry_interval)
            continue
        record("observer_returned", interruptions)
        return


def run():
    folder = DEST / "common_visual"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "runtime_recovery.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("A native context recovery supervisor is already running") from None
        events = folder / "runtime_recovery_events.jsonl"

        def record(phase, interruptions):
            state = {
                "utc": datetime.now(timezone.utc).isoformat(),
                "phase": phase,
                "supervisor_pid": os.getpid(),
                "balance_endpoint_interruptions_in_this_session": interruptions,
                "runner_sha256": sha(__file__),
                "frozen_observer_sha256": sha(observe.__code__.co_filename),
                "exhausted_or_format_failed_batches_reissued": False,
                "maximum_inference_attempts_per_batch": 4,
                "private_teacher_or_scoring_files_opened": False,
            }
            save(folder / "runtime_recovery_progress.json", state)
            with events.open("a") as log:
                log.write(json.dumps(state, ensure_ascii=False) + "\n")
            print(phase, "interruptions", interruptions, flush=True)

        def observed():
            if shutil.disk_usage(folder).free < 512 * 1024 ** 2:
                raise RuntimeError("Insufficient disk space for observer continuation")
            observe()
            manifest = json.loads((folder / "manifest.json").read_text())
            if not manifest["complete"]:
                raise RuntimeError("Observer returned without a complete manifest")

        record("resuming_frozen_native_context_observer", 0)
        supervise(observed, record)


if __name__ == "__main__":
    run()

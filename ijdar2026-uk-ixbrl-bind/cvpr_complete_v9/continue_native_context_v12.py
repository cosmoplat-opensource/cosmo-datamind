"""Resumable context-observation -> same-input learning -> complete scoring."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from native_context_crops_v12 import DEST
from prepare import save, sha


def run():
    root = DEST / "field_retrieval_v1"
    observation = DEST / "common_visual/manifest.json"
    start = time.monotonic()
    while not observation.exists():
        if time.monotonic()-start > 7200:
            raise RuntimeError("Observation queue incomplete; preserve all targets and resume explicitly")
        save(root / "pipeline_progress.json", {"phase": "waiting_common_context_observations",
                                              "utc": datetime.now(timezone.utc).isoformat()})
        print("Waiting for shared native context observation completion", flush=True)
        time.sleep(30)
    if not json.loads(observation.read_text())["complete"]:
        raise ValueError("Complete common visual manifest required")
    stages = (
        (root / "manifest.json", "native_field_retrieval_v12.py"),
        (root / "features/manifest.json", "native_field_features_v12.py"),
        (root / "learning_v1/manifest.json", "native_field_learning_v12.py"),
        (root / "learning_v1/development_results.json", "score_native_field_learning_v12.py"),
    )
    for artifact, runner in stages:
        save(root / "pipeline_progress.json", {"phase": runner, "utc": datetime.now(timezone.utc).isoformat()})
        if artifact.exists():
            if not json.loads(artifact.read_text())["complete"]:
                raise ValueError("Existing incomplete stage must not be silently replaced")
            continue
        subprocess.run([sys.executable, str(Path(__file__).with_name(runner))], check=True)
        if not artifact.exists() or not json.loads(artifact.read_text())["complete"]:
            raise ValueError("Successful exit without complete artifact is not experiment completion")
    save(root / "pipeline_progress.json", {"phase": "complete", "completed_utc": datetime.now(timezone.utc).isoformat(),
                                          "development_field_channel_only": True,
                                          "all_cvpr_or_independent_test_completed": False,
                                          "runner_sha256": sha(__file__)})


if __name__ == "__main__":
    run()

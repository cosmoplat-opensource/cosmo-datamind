"""Run replay inference and complete scoring in sequence; resume artifacts."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from native_field_replay_v15 import DEST
from prepare import save


def run():
    for runner, artifact in (("native_field_replay_v15.py", DEST / "manifest.json"),
                             ("score_native_field_replay_v15.py", DEST / "development_results.json")):
        save(DEST / "pipeline_progress.json", {"phase": runner, "utc": datetime.now(timezone.utc).isoformat(),
                                              "full_binding_training_started": False})
        if artifact.exists():
            if not json.loads(artifact.read_text())["complete"]:
                raise ValueError("Incomplete replay artifact requires review")
            continue
        subprocess.run([sys.executable, str(Path(__file__).with_name(runner))], check=True)
        if not artifact.exists() or not json.loads(artifact.read_text())["complete"]:
            raise ValueError("Successful exit without complete replay artifact")
    save(DEST / "pipeline_progress.json", {"phase": "complete", "completed_utc": datetime.now(timezone.utc).isoformat(),
                                          "field_channel_only": True, "full_binding_training_started": False,
                                          "all_original_cvpr_experiments_completed": False})


if __name__ == "__main__":
    run()

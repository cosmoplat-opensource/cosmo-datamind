"""Resumable public candidate preparation and historical feasibility audit."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from native_binding_schema_v16 import DEST
from prepare import save


def run():
    root = Path(__file__).resolve().parent
    progress = DEST / "pipeline_progress.json"
    stages = (
        ("preparing_public_entity_dimension_candidates", "native_binding_schema_v16.py", DEST / "manifest.json"),
        ("auditing_historical_candidate_feasibility", "native_binding_candidate_audit_v16.py", DEST / "training_only/historical_candidate_audit.json"),
    )
    for phase, script, artifact in stages:
        if artifact.exists():
            if not json.loads(artifact.read_text())["complete"]:
                raise ValueError("Incomplete artifact cannot be treated as a finished stage")
            continue
        save(progress, {"phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(),
                        "full_binding_model_training_started": False,
                        "all_original_cvpr_experiments_completed": False})
        status = subprocess.run([sys.executable, str(root / script)], check=False)
        if status.returncode:
            save(progress, {"phase": "failed", "failed_stage": phase, "exit_code": status.returncode,
                            "updated_utc": datetime.now(timezone.utc).isoformat(),
                            "full_binding_model_training_started": False})
            raise SystemExit(status.returncode)
    save(progress, {"phase": "candidate_preparation_complete", "updated_utc": datetime.now(timezone.utc).isoformat(),
                    "full_binding_model_training_started": False, "full_binding_model_evaluation_complete": False,
                    "all_original_cvpr_experiments_completed": False})
    print("Public candidate preparation and historical feasibility audit complete; joint training remains pending", flush=True)


if __name__ == "__main__":
    run()

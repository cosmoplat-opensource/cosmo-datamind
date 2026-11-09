"""Resumable shared-candidate repair, equal-input training and frozen scoring."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from native_binding_expand_v17 import DEST
from native_dimension_network_v17 import DEST as NETWORK
from prepare import save


def run():
    root = Path(__file__).resolve().parent
    progress = DEST / "pipeline_progress.json"
    stages = (
        ("public_definition_preparation", "native_dimension_network_v17.py", NETWORK/"manifest.json"),
        ("shared_candidate_expansion", "native_binding_expand_v17.py", DEST/"manifest.json"),
        ("historical_candidate_audit", "native_binding_audit_v17.py", DEST/"training_only/historical_candidate_audit.json"),
        ("shared_full_semantic_features", "native_binding_features_v17.py", DEST/"features/manifest.json"),
        ("full_semantic_tuple_learning", "native_binding_learning_v17.py", DEST/"learning_v1/manifest.json"),
        ("frozen_prediction_scoring", "score_native_binding_v17.py", DEST/"learning_v1/development_results.json"),
    )
    for phase, script, artifact in stages:
        if artifact.exists():
            if not json.loads(artifact.read_text())["complete"]:
                raise ValueError("Incomplete artifact cannot be treated as complete")
            continue
        save(progress, {"phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(),
                        "full_semantic_tuple_training_started": phase in {"full_semantic_tuple_learning", "frozen_prediction_scoring"},
                        "independent_native_test_complete": False, "all_original_cvpr_experiments_completed": False})
        with (DEST / (phase+".log")).open("a") as log:
            result = subprocess.run([sys.executable, str(root/script)], stdout=log, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            save(progress, {"phase": "failed", "failed_stage": phase, "exit_code": result.returncode,
                            "updated_utc": datetime.now(timezone.utc).isoformat(), "independent_native_test_complete": False})
            raise SystemExit(result.returncode)
        print("Complete binding pipeline stage finished", phase, flush=True)
    save(progress, {"phase": "development_semantic_tuple_scores_complete", "updated_utc": datetime.now(timezone.utc).isoformat(),
                    "full_semantic_tuple_training_started": True, "certified_visual_header_scope_complete": False,
                    "independent_native_test_complete": False, "all_original_cvpr_experiments_completed": False})
    print("Equal-input complete semantic-tuple development finished; native independent visual-scope experiments remain", flush=True)


if __name__ == "__main__":
    run()

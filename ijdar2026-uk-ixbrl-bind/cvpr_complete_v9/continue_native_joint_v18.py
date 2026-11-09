"""Resumable v18 uncertain candidates, joint learning and frozen scoring."""

from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys

from native_binding_candidates_v18 import DEST
from prepare import save


def run():
    root = Path(__file__).resolve().parent
    stages = (
        ("shared_uncertain_candidate_expansion","native_binding_candidates_v18.py",DEST/"candidates/manifest.json"),
        ("fold_safe_joint_feature_preparation","native_joint_features_v18.py",DEST/"features/manifest.json"),
        ("historical_joint_feasibility_audit","native_joint_audit_v18.py",DEST/"training_only/historical_feasibility.json"),
        ("joint_state_learning","native_joint_learning_v18.py",DEST/"learning_v1/manifest.json"),
        ("frozen_joint_prediction_scoring","score_native_joint_v18.py",DEST/"learning_v1/development_results.json"),
    )
    for phase,script,artifact in stages:
        if artifact.exists():
            if not json.loads(artifact.read_text())["complete"]:
                raise ValueError("Incomplete artifact cannot be treated as complete")
            continue
        save(DEST/"pipeline_progress.json",{"phase":phase,"updated_utc":datetime.now(timezone.utc).isoformat(),
             "joint_state_training_started":phase in {"joint_state_learning","frozen_joint_prediction_scoring"},
             "independent_native_test_completed":False,"all_original_cvpr_experiments_completed":False})
        with (DEST/(phase+".log")).open("a") as log:
            result = subprocess.run([sys.executable,str(root/script)],stdout=log,stderr=subprocess.STDOUT,check=False)
        if result.returncode:
            save(DEST/"pipeline_progress.json",{"phase":"failed","failed_stage":phase,"exit_code":result.returncode,
                 "updated_utc":datetime.now(timezone.utc).isoformat(),"all_original_cvpr_experiments_completed":False})
            raise SystemExit(result.returncode)
        print("v18 joint stage complete",phase,flush=True)
    save(DEST/"pipeline_progress.json",{"phase":"joint_development_scores_complete","updated_utc":datetime.now(timezone.utc).isoformat(),
         "joint_state_training_started":True,"certified_visual_scope_complete":False,
         "independent_native_test_completed":False,"all_original_cvpr_experiments_completed":False})
    print("v18 joint-state development scores complete; independent visual-scope and broader CVPR tests remain",flush=True)


if __name__ == "__main__":
    run()

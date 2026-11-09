"""Resume common v19 beams and the unchanged v18 learning/scoring controls.

The adapter changes artifact destinations only. Base scripts must still match
the completed v18 snapshots. Its own hash is frozen in the preparation design.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from native_binding_candidates_v18 import DEST as PREVIOUS
from native_joint_features_v19 import DEST
from prepare import save, sha

FOLDER = DEST / "learning_v1"


def verify_base(module_name, snapshot):
    path = Path(__file__).with_name(module_name + ".py")
    if sha(path) != sha(snapshot):
        raise ValueError("Base algorithm changed after the completed v18 run: " + module_name)


def dispatch(stage):
    if stage == "features":
        from native_joint_features_v19 import prepare
        prepare()
        return
    design_file = DEST / "preparation_design.json"
    design = json.loads(design_file.read_text())
    if design["shared_training_and_scoring_adapter_sha256"] != sha(__file__):
        raise ValueError("Frozen v19 training/scoring adapter changed")
    if stage == "historical":
        import diagnose_native_joint_beam_v18 as diagnosis
        import native_joint_audit_v18 as base
        base.DEST = DEST
        diagnosis.DEST = DEST
        if not (DEST / "training_only/historical_feasibility.json").exists():
            base.audit()
        current_file = DEST / "training_only/beam_retention_diagnosis.json"
        if not current_file.exists():
            diagnosis.run()
        previous_file = PREVIOUS / "training_only/beam_retention_diagnosis.json"
        current, previous = [json.loads(p.read_text()) for p in (current_file, previous_file)]
        if current["historical_teacher_sha256"] != previous["historical_teacher_sha256"]:
            raise ValueError("Candidate retention comparison uses different historical teachers")
        change = {}
        for seed, row in current["own_company_excluded_beam_representability_by_seed"].items():
            old = previous["own_company_excluded_beam_representability_by_seed"][seed]
            change[seed] = {key: value - old[key] for key, value in row.items()}
            if any(value < 0 for value in change[seed].values()):
                raise ValueError("Expanded beams lost an original historical positive")
        save(DEST / "training_only/historical_comparison.json", {
            "complete": True, "historical_targets": 280,
            "candidate_retention_change_by_seed": change,
            "previous_diagnosis_sha256": sha(previous_file),
            "current_diagnosis_sha256": sha(current_file),
            "future_or_independent_reference_files_read": False,
            "candidate_upper_bound_not_model_accuracy": True,
        })
    elif stage == "learning":
        import native_joint_learning_v18 as base
        verify_base("native_joint_learning_v18", PREVIOUS / "learning_v1/runner_snapshot.py")
        base.DEST, base.FOLDER = DEST, FOLDER
        base.run()
    elif stage == "scoring":
        import score_native_joint_v18 as base
        verify_base("score_native_joint_v18", PREVIOUS / "learning_v1/scorer_snapshot.py")
        base.DEST, base.FOLDER = DEST, FOLDER
        base.run()
    elif stage == "integrity":
        import review_native_joint_v18 as base
        base.DEST, base.FOLDER = DEST, FOLDER
        base.review()
    else:
        raise ValueError("Unknown continuation stage")


def status(phase, **extra):
    save(DEST / "pipeline_progress.json", {
        "phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(),
        "all_original_cvpr_experiments_completed": False,
        "independent_native_test_completed": False,
        "human_runtime_confirmations": 0,
        **extra,
    })
    subprocess.run([sys.executable, str(Path(__file__).with_name("update_v10_research_state.py"))], check=False)


def run():
    stages = (
        ("features", DEST / "features/manifest.json"),
        ("historical", DEST / "training_only/historical_comparison.json"),
        ("learning", FOLDER / "manifest.json"),
        ("integrity", DEST / "control_integrity_review.json"),
        ("scoring", FOLDER / "development_results.json"),
    )
    for phase, artifact in stages:
        if artifact.exists():
            if not json.loads(artifact.read_text())["complete"]:
                raise ValueError("Incomplete stage artifact cannot be reused")
            continue
        status(phase, training_started=phase in {"learning", "integrity", "scoring"})
        with (DEST / (phase + ".log")).open("a") as log:
            result = subprocess.run([sys.executable, str(Path(__file__)), "--stage", phase],
                                    stdout=log, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            status("failed", failed_stage=phase, exit_code=result.returncode)
            raise SystemExit(result.returncode)
        print("v19 stage complete", phase, flush=True)
    status("joint_development_scores_complete", training_started=True)
    print("v19 development comparison complete; visual scope and independent tests remain", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("features", "historical", "learning", "integrity", "scoring"))
    args = parser.parse_args()
    dispatch(args.stage) if args.stage else run()

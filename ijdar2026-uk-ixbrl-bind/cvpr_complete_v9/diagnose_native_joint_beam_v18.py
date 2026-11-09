"""Locate historical candidate losses without consulting future references."""

import json
from collections import Counter
from datetime import datetime, timezone

import numpy as np
from native_binding_candidates_v18 import DEST
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import historical_positive
from native_binding_prepare_v14 import DEST as TEACHERS
from native_joint_features_v18 import SEEDS
from prepare import save, sha


def run():
    features_file = DEST / "features/manifest.json"
    features = json.loads(features_file.read_text())
    task_file = DEST / "candidates/tasks.json"
    if not features["complete"] or sha(task_file) != features["public_candidate_sha256"]:
        raise ValueError("Frozen public candidates required")
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    if sha(teacher_file) != json.loads((TEACHERS / "manifest.json").read_text())["teacher_sha256"]:
        raise ValueError("Historical teacher provenance changed")
    records_file = DEST / "features/records.json"
    if sha(records_file) != features["files"]["records.json"]:
        raise ValueError("Shared component records changed")
    tasks, records, teachers = [json.loads(p.read_text()) for p in (task_file, records_file, teacher_file)]
    full, retained = Counter(), {str(seed): Counter() for seed in SEEDS}
    count = 0
    for i, task in enumerate(tasks):
        if task["source_year"] != 2023:
            continue
        count += 1
        ident = task["task_id"]
        positives = {channel: historical_positive(task, records[i][channel], teachers[ident], channel).numpy() for channel in CHANNELS}
        for channel in CHANNELS:
            full[channel] += bool(positives[channel].any())
        full["all_components"] += all(value.any() for value in positives.values())
        path = DEST / "features" / (ident + ".npz")
        if sha(path) != features["files"][path.name]:
            raise ValueError("Frozen historical beam artifact changed")
        with np.load(path) as arrays:
            for seed in SEEDS:
                states = arrays["states_" + task["company"] + "_" + str(seed)]
                known = [bool(positives[channel][states[:, j]].any()) for j, channel in enumerate(CHANNELS)]
                for channel, available in zip(CHANNELS, known, strict=True):
                    retained[str(seed)][channel] += available
                retained[str(seed)]["all_components"] += all(known)
    if count != 280 or any(v > full[k] for row in retained.values() for k, v in row.items()):
        raise ValueError("Historical inventory or subset relationship changed")
    result = {
        "complete": True,
        "diagnosed_utc": datetime.now(timezone.utc).isoformat(),
        "historical_targets": count,
        "full_candidate_representability": dict(full),
        "own_company_excluded_beam_representability_by_seed": {s: dict(row) for s, row in retained.items()},
        "candidate_cap_loss_by_seed": {s: {k: full[k] - row[k] for k in full} for s, row in retained.items()},
        "feature_manifest_sha256": sha(features_file),
        "historical_teacher_sha256": sha(teacher_file),
        "diagnostic_sha256": sha(__file__),
        "future_or_independent_reference_files_read": False,
        "diagnosis_does_not_change_frozen_v18_training_or_candidate_budget": True,
        "these_are_candidate_upper_bounds_not_model_accuracies": True,
    }
    save(DEST / "training_only/beam_retention_diagnosis.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run()

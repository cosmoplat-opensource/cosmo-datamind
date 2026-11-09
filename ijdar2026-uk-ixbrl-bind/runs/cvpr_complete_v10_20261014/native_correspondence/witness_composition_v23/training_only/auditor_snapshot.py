"""Historical value/witness feasibility after the v23 common input freeze."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import historical_positive
from native_binding_prepare_v14 import DEST as TEACHERS
from native_witness_inventory_v23 import DEST, INPUT
from prepare import save, sha


def run():
    output = DEST / "training_only/candidate_and_witness_feasibility.json"
    if output.exists():
        raise RuntimeError("Historical witness audit already frozen")
    public_file = DEST / "public/witnesses.json"
    public_manifest = DEST / "public/manifest.json"
    public = json.loads(public_manifest.read_text())
    feature_manifest = DEST / "features/manifest.json"
    features = json.loads(feature_manifest.read_text())
    if not public["complete"] or not features["complete"] or sha(public_file) != public["witness_inventory_sha256"] or features["witness_inventory_sha256"] != sha(public_file):
        raise ValueError("Both public witness and feature freezes required before reading teachers")
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    teacher_manifest = TEACHERS / "manifest.json"
    if sha(teacher_file) != json.loads(teacher_manifest.read_text())["teacher_sha256"]:
        raise ValueError("Historical teacher provenance changed")
    tasks = json.loads((INPUT / "candidates/tasks.json").read_text())
    inventory = json.loads(public_file.read_text())
    teachers = json.loads(teacher_file.read_text())
    historical = [(t, item) for t, item in zip(tasks, inventory, strict=True) if t["source_year"] == 2023]
    if len(historical) != 280 or {t["task_id"] for t, _ in historical} != set(teachers):
        raise ValueError("All 280 historical targets required")
    counts, rows = Counter(), {}
    for task, item in historical:
        ident = task["task_id"]
        if item["task_id"] != ident:
            raise ValueError("Historical item order differs")
        correct, row = [], {}
        for channel in CHANNELS:
            groups = item["components"][channel]
            # The helper appends its own explicit unknown; v23 already has one.
            positive = historical_positive(task, groups, teachers[ident], channel)[:-1]
            available = bool(positive.any())
            correct.append(available)
            counts["correct_value_available/" + channel] += available
            row[channel] = {"correct_semantic_value_proposed": available}
            if channel not in {"period", "unit_quantity"}:
                continue
            positive_groups = [group for group, match in zip(groups, positive.tolist(), strict=True) if match]
            located = [w for group in positive_groups for w in group["witnesses"] if w["anchor"]]
            ids = {w["anchor"]["anchor_id"] for w in located}
            counts["correct_value_with_located_text_hypothesis/" + channel] += bool(located)
            counts["correct_value_with_multiple_location_hypotheses/" + channel] += len(ids) > 1
            counts["correct_value_with_restored_unit_path/" + channel] += any(w.get("restored_from_public_unit_and_literal_proposals") for group in positive_groups for w in group["witnesses"])
            row[channel].update(located_text_hypothesis_count=len(ids), visible_ownership_certified=False)
        row["complete_semantic_value_tuple_available"] = all(correct)
        counts["complete_semantic_value_tuple_available"] += all(correct)
        counts["historical_targets"] += 1
        rows[ident] = row
    # v23 is a representation change. It must not improve this upper bound by
    # silently inserting answers from the teacher after the public freeze.
    if counts["complete_semantic_value_tuple_available"] != 181:
        raise ValueError("Witness grouping changed the original full-candidate semantic upper bound")
    snapshot = DEST / "training_only/auditor_snapshot.py"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {
        "complete": True, "audited_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "per_target": rows, "public_inventory_sha256": sha(public_file),
        "public_manifest_sha256": sha(public_manifest), "public_feature_manifest_sha256": sha(feature_manifest),
        "historical_teacher_sha256": sha(teacher_file), "auditor_snapshot_sha256": sha(snapshot),
        "candidate_upper_bounds_and_text_location_counts_are_not_model_accuracies": True,
        "located_text_is_not_certified_header_ownership": True,
        "no_teacher_values_inserted_into_public_candidates": True,
        "no_semantic_value_pruning_required_by_the_exact_common_composition": True,
        "future_or_reserved_independent_reference_files_read": False,
        "new_generative_visual_calls": 0, "human_runtime_confirmations": 0,
        "trained_model_or_new_accuracy_result": False, "ontology_unique_advantage_established": False,
        "all_original_cvpr_experiments_completed": False,
    })
    print("Historical witnessed candidate feasibility", dict(counts), flush=True)


if __name__ == "__main__":
    run()

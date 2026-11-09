"""Three matched native transfer conditions; freeze all predictions before scoring.

Full transfer, zero donor pixels, and localization-only share native images,
OCR, candidates, relocated geometry, availability flags and training budgets.
The original target-crop visual context remains in every condition.
"""

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from native_binding_candidates_v18 import DEST as BASELINE
from native_binding_features_v17 import CHANNELS
from native_header_alignment_v21 import DEST
from native_joint_features_v18 import SEEDS, state_features
from native_scope_joint_features_v21 import REL_DIM, STATE_DIM, transfer_mask
from prepare import save, sha

CONDITIONS = ("full_transfer", "donor_pixels_zeroed", "localization_only")


def folder_for(condition):
    if condition not in CONDITIONS:
        raise ValueError("Unknown frozen transfer condition")
    return DEST / ("learning_" + condition)


def load_packs(condition, company, seed, tasks, records, contexts, feedback):
    from native_joint_learning_v18 import state_feedback
    packs = {}
    for i, task in enumerate(tasks):
        if task["source_year"] != 2023 and task["company"] != company:
            continue
        with np.load(DEST / "features" / (task["task_id"] + ".npz")) as arrays:
            suffix = company + "_" + str(seed)
            states = arrays["states_" + suffix]
            unary = arrays["unary_" + suffix]
            relations = arrays["relations_" + suffix].copy()
            if condition == "donor_pixels_zeroed":
                relations[:, -8:] = arrays["scope_pixels_zeroed_" + suffix]
            elif condition == "localization_only":
                # Preserve availability flags and relocated geometry. Remove
                # only learned donor probability/span scores, not their inputs.
                relations[:, [-8, -7, -6, -4, -3, -2]] = 0
            nodes = {c: arrays["node_" + c] for c in CHANNELS}
            values = torch.from_numpy(state_features(nodes, states, unary, relations))
        common_prior = unary.sum(1) - (relations[:, 0] * (1 - relations[:, 1]) + relations[:, 2] * (1 - relations[:, 3]))
        if task["source_year"] == 2023:
            positive, known = state_feedback(task, records[i], feedback[task["task_id"]], states)
        else:
            positive, known = torch.zeros(len(states), dtype=torch.bool), [False] * 4
        packs[i] = {"states": values, "indices": states, "prior": torch.from_numpy(common_prior),
                    "context": contexts[i], "positive": positive, "known": known}
    return packs


def check_base(name, snapshot):
    if sha(Path(__file__).with_name(name + ".py")) != sha(snapshot):
        raise ValueError("Frozen base algorithm changed: " + name)


def condition_provenance(condition):
    feature_file = DEST / "features/manifest.json"
    feature = json.loads(feature_file.read_text())
    if feature["learning_adapter_sha256"] != sha(__file__):
        raise ValueError("Frozen native transfer learning adapter changed")
    result = {"condition": condition, "adapter_sha256": sha(__file__), "feature_manifest_sha256": sha(feature_file),
              "state_dimension": STATE_DIM, "relation_dimension": REL_DIM,
              "same_states_labels_geometry_availability_parameters_and_optimizer_budgets": True,
              "original_target_crop_visual_context_retained_in_all_conditions": True,
              "donor_image_removal_is_weight_tied_inference_not_donor_retraining": True,
              "native_visual_ownership_supervision_or_certification": False}
    path = folder_for(condition) / "condition_provenance.json"
    if path.exists() and json.loads(path.read_text()) != result:
        raise ValueError("Frozen transfer condition changed")
    save(path, result)
    return path


def all_predictions_frozen():
    for condition in CONDITIONS:
        folder = folder_for(condition)
        manifest = json.loads((folder / "manifest.json").read_text())
        if not manifest["complete"] or manifest["prediction_files"] != 1512:
            raise ValueError("All 4536 condition configurations must freeze before any score reference read")
        condition_provenance(condition)
        for name, digest in manifest["predictions_sha256"].items():
            if sha(folder / name) != digest:
                raise ValueError("Frozen transfer-condition prediction changed")


def dispatch(stage, condition=None):
    if stage == "joint_features":
        from native_scope_joint_features_v21 import prepare
        prepare()
        return
    if stage == "learning":
        import native_joint_learning_v18 as base
        check_base("native_joint_learning_v18", BASELINE / "learning_v1/runner_snapshot.py")
        condition_provenance(condition)
        base.DEST, base.FOLDER = DEST, folder_for(condition)
        base.STATE_DIM, base.REL_DIM = STATE_DIM, REL_DIM
        base.prototype_mask = transfer_mask
        base.load_packs = lambda *args: load_packs(condition, *args)
        base.run()
    elif stage == "integrity":
        import review_native_joint_v18 as base
        provenance = condition_provenance(condition)
        base.DEST, base.FOLDER = DEST, folder_for(condition)
        def save_review(path, result):
            if Path(path) != DEST / "control_integrity_review.json":
                raise ValueError("Unexpected integrity-review mutation")
            save(folder_for(condition) / "control_integrity_review.json", {
                **result, "condition": condition, "condition_provenance_sha256": sha(provenance)})
        base.save = save_review
        base.review()
    elif stage == "scoring":
        import score_native_joint_v18 as base
        check_base("score_native_joint_v18", BASELINE / "learning_v1/scorer_snapshot.py")
        all_predictions_frozen()
        base.DEST, base.FOLDER = DEST, folder_for(condition)
        base.run()
    else:
        raise ValueError("Unknown transfer continuation stage")


def summarize():
    rng = np.random.default_rng(20261016)
    results = {c: json.loads((folder_for(c) / "development_results.json").read_text()) for c in CONDITIONS}
    targets = Counter(t["company"] for t in json.loads((DEST / "candidates/tasks.json").read_text()) if t["source_year"] != 2023)
    effects = {}
    for comparator in CONDITIONS[1:]:
        for label, full in results["full_transfer"]["seed_means"].items():
            policy, method, budget = label.split("/")
            groups = {}
            for seed in SEEDS:
                key = f"{policy}/{method}/{seed}/{budget}"
                a = results["full_transfer"]["per_configuration"][key]["per_company"]
                b = results[comparator]["per_configuration"][key]["per_company"]
                for company in a:
                    groups.setdefault(company, []).append((a[company] - b[company]) / targets[company])
            values = np.array([np.mean(v) for v in groups.values()])
            samples = values[rng.integers(0, len(values), size=(4000, len(values)))].mean(1)
            effects[f"full_vs_{comparator}/{label}"] = {
                "mean_tuple_accuracy_difference": full["semantic_tuple_exact_accuracy"] - results[comparator]["seed_means"][label]["semantic_tuple_exact_accuracy"],
                "company_identifier_cluster_bootstrap_95_ci": np.quantile(samples, [.025, .975]).tolist(),
                "descriptive_development_interval_no_multiple_testing_or_independent_test_claim": True}
    save(DEST / "comparison_summary.json", {
        "complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "unique_future_development_targets": 560, "conditions": list(CONDITIONS), "prediction_configurations": 4536,
        "seed_means": {c: r["seed_means"] for c, r in results.items()}, "transfer_effects": effects,
        "result_file_sha256": {c: sha(folder_for(c) / "development_results.json") for c in CONDITIONS},
        "human_runtime_confirmations": 0, "scope_probabilities_remain_uncalibrated_transfer_hypotheses": True,
        "entity_is_common_report_metadata_assistance": True,
        "pixel_ablation_affects_only_the_donor_stream_not_the_original_target_crop_context": True,
        "native_visual_scope_teacher_certified": False, "ontology_unique_advantage_established": False,
        "independent_native_test_completed": False, "all_original_cvpr_experiments_completed": False})


def update(phase, **extra):
    save(DEST / "pipeline_progress.json", {"phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(),
         "conditions": list(CONDITIONS), "all_original_cvpr_experiments_completed": False,
         "independent_native_test_completed": False, "native_visual_scope_teacher_certified": False, **extra})
    subprocess.run([sys.executable, str(Path(__file__).with_name("update_v10_research_state.py"))], check=False)


def run():
    # Localization and donor inputs are already independently resumable. Their
    # manifests must complete before the joint feature adapter is run.
    for file in ("localization/manifest.json", "native_word_features/manifest.json", "donor_predictions/manifest.json"):
        if not json.loads((DEST / file).read_text())["complete"]:
            raise ValueError("Completed public transfer preparation required: " + file)
    stages = [("joint_features", None, DEST / "features/manifest.json")]
    stages += [(stage, condition, folder_for(condition) / artifact)
               for stage, artifact in (("learning", "manifest.json"), ("integrity", "control_integrity_review.json"), ("scoring", "development_results.json"))
               for condition in CONDITIONS]
    for stage, condition, artifact in stages:
        if artifact.exists():
            if not json.loads(artifact.read_text())["complete"]:
                raise ValueError("Incomplete stage cannot be reused")
            continue
        update(stage, condition=condition)
        args = [sys.executable, str(Path(__file__)), "--stage", stage]
        if condition:
            args += ["--condition", condition]
        with (DEST / (stage + ("_" + condition if condition else "") + ".log")).open("a") as log:
            result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            update("failed", failed_stage=stage, condition=condition, exit_code=result.returncode)
            raise SystemExit(result.returncode)
        print("Native transfer stage complete", stage, condition, flush=True)
    summarize()
    update("transfer_development_scores_complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("joint_features", "learning", "integrity", "scoring"))
    parser.add_argument("--condition", choices=CONDITIONS)
    arguments = parser.parse_args()
    if arguments.stage in {"learning", "integrity", "scoring"} and not arguments.condition:
        parser.error("A matched condition must be explicit")
    dispatch(arguments.stage, arguments.condition) if arguments.stage else run()

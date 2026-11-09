"""Wait for frozen v23 results, then independently audit and retain all outcomes.

This never restarts training, changes a model, or opens reserved sources.
An observation timeout leaves the pipeline incomplete, not failed or stopped.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from native_scope_learning_v23 import FOLDER
from prepare import save, sha


def readiness(folder):
    progress_file = folder / "progress.json"
    progress = json.loads(progress_file.read_text()) if progress_file.exists() else {}
    if progress.get("phase") == "failed":
        return "pipeline_reported_failure"
    files = [folder / name for name in ("manifest.json", "development_results.json")]
    if not all(p.exists() for p in files):
        return "pending"
    values = [json.loads(p.read_text()) for p in files]
    return "ready" if all(v.get("complete") for v in values) else "pending"


def geometry_summary(records):
    grouped = defaultdict(list)
    for row in records:
        scale = row["geometry_scale"]
        delta = row["geometry_parameter_delta_l2"]
        if not math.isfinite(scale) or not math.isfinite(delta) or delta < 0:
            raise ValueError("Nonfinite or invalid geometry audit values")
        grouped[row["method"]].append(row)
    result = {}
    for method, rows in grouped.items():
        signs = Counter("negative" if r["geometry_scale"] < 0 else "positive" if r["geometry_scale"] > 0 else "zero" for r in rows)
        result[method] = {"parent_models": len(rows), "calibration_sign_counts": dict(signs),
                          "minimum_geometry_scale": min(r["geometry_scale"] for r in rows),
                          "maximum_geometry_scale": max(r["geometry_scale"] for r in rows),
                          "mean_geometry_parameter_delta_l2": sum(r["geometry_parameter_delta_l2"] for r in rows) / len(rows),
                          "positive_density_coverage_not_established_by_parameter_updates_alone": True}
    return result


def status(phase, **details):
    save(FOLDER / "finalization_progress.json", {"phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(),
         "pid": os.getpid(), "does_not_restart_or_modify_training": True,
         "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False, **details})


def finalize():
    output = FOLDER / "stage_completion_review.json"
    if output.exists():
        raise RuntimeError("Completed development review is frozen")
    if readiness(FOLDER) != "ready":
        raise ValueError("All development predictions and scores must freeze before review")
    review_file = FOLDER / "control_integrity_review.json"
    status("independent_control_audit")
    if not review_file.exists():
        subprocess.run([sys.executable, str(Path(__file__).with_name("review_native_scope_v23.py"))], check=True)
    review = json.loads(review_file.read_text())
    results_file, manifest_file = FOLDER / "development_results.json", FOLDER / "manifest.json"
    results, manifest = [json.loads(p.read_text()) for p in (results_file, manifest_file)]
    if not review["complete"] or review["parent_checkpoints_checked"] != 126 or review["local_checkpoints_checked"] != 504 or manifest["prediction_files"] != 1512 or results["unique_future_targets"] != 560:
        raise ValueError("Full control/prediction/target denominator not independently verified")
    if review["prediction_manifest_sha256"] != sha(manifest_file) or results["prediction_manifest_sha256"] != sha(manifest_file):
        raise ValueError("Audited and scored predictions differ")
    snapshot = FOLDER / "finalizer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "development_only_reused_company_cohort": True, "unique_later_year_development_targets": 560,
        "company_identifiers": 14, "prediction_configurations": 1512,
        "all_seed_means_without_selection": results["seed_means"],
        "all_paired_development_comparisons_without_selection": results["paired_comparisons"],
        "all_feedback_error_propagation_without_selection": results["feedback_error_propagation"],
        "geometry_mechanism_audit": geometry_summary(review["parent_geometry_parameter_audit"]),
        "negative_scope_density_calibration_means_exclusion_not_positive_coverage": True,
        "same_case_sequence_and_optimizer_exposure_checked": review["same_actual_training_cases_and_witness_factor_budgets"],
        "strict_flop_or_wall_time_equivalence_certified": False,
        "semantic_tuple_success_does_not_certify_visual_header_ownership": True,
        "entity_output_uses_common_report_metadata": True,
        "legal_entity_group_template_and_original_publication_independence_uncertified": True,
        "no_independent_or_multiple_comparison_corrected_accuracy_claim": True,
        "new_historical_weak_year_data_not_used_in_this_frozen_experiment": True,
        "review_file_sha256": sha(review_file), "results_file_sha256": sha(results_file),
        "prediction_manifest_sha256": sha(manifest_file), "finalizer_snapshot_sha256": sha(snapshot),
        "future_semantic_reference_files_opened_by_this_finalizer": False,
        "reserved_or_calibration_source_assets_or_answers_opened": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "independent_native_test_completed": False, "ontology_unique_advantage_established": False,
        "all_original_cvpr_experiments_completed": False,
        "next_required_work": [
            "Interpret full-binding accuracy, uncertainty and feedback error propagation against the strong memory and generic controls; retain every negative result.",
            "Test the common historical weak year auxiliary objective with matched controls and actual compute accounting; never evaluate visual ownership using its own weak-label selection heuristic.",
            "Keep own-company auxiliary labels outside 0/5/10 local feedback; qualify legal groups, templates and chronology before any independent source experiment.",
            "Complete the original independent transfer, natural change, visual ablation and official public benchmark requirements."
        ]})
    status("development_stage_review_complete", review_sha256=sha(output))
    subprocess.run([sys.executable, str(Path(__file__).with_name("update_v10_research_state.py"))], check=True)
    print("v23 complete development/control review saved; independent advantage remains unproven", flush=True)


def run(wait=False, maximum_wait_seconds=10800):
    start = time.monotonic()
    while readiness(FOLDER) != "ready":
        state = readiness(FOLDER)
        if state == "pipeline_reported_failure":
            status("waiting_for_pipeline_repair", observation="Training pipeline explicitly reported failure; no automatic restart.")
            return
        if not wait:
            raise ValueError("Frozen v23 development results are not ready")
        if time.monotonic() - start >= maximum_wait_seconds:
            status("observation_timeout_pipeline_terminal_state_not_inferred")
            return
        status("waiting_for_frozen_development_results")
        time.sleep(15)
    finalize()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    try:
        run(wait=args.wait)
    except Exception as error:
        status("finalization_failed", error_type=type(error).__name__, error=str(error)[:400])
        raise

"""Independent budget/checkpoint audit; no future semantic references are read."""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from native_scope_composition_v23 import METHODS, ScopeComposer
from native_scope_learning_v23 import BUDGETS, FOLDER, POLICIES, SEEDS, source_hashes
from native_scope_score_v23 import validate_predictions
from native_witness_inventory_v23 import DEST, INPUT
from prepare import save, sha


def validate_exposure(exposure, steps, local, replay):
    if exposure["optimizer_steps"] != steps or exposure["sampled_local_cases"] != local or exposure["sampled_replay_cases"] != replay:
        raise ValueError("Actual optimizer/case exposure differs from the frozen budget")
    if steps and (not exposure["case_sequence_sha256"] or exposure["sampled_witness_factor_pairs"] <= 0):
        raise ValueError("Missing actual training sequence/factor accounting")


def review():
    output = FOLDER / "control_integrity_review.json"
    if output.exists():
        raise RuntimeError("Independent full-binding audit already frozen")
    manifest_file, manifest, _, companies, _ = validate_predictions()
    tasks = json.loads((INPUT / "candidates/tasks.json").read_text())
    by_id = {t["task_id"]: t for t in tasks}
    prefixes = json.loads((FOLDER / "feedback_prefixes.json").read_text())
    design_file = FOLDER / "design.json"
    design_sha = sha(design_file)
    design = json.loads(design_file.read_text())
    context_dimension = np.load(DEST / "features/context.npy", mmap_mode="r").shape[1]
    geometry, parent_times, local_times = [], {m: [] for m in METHODS}, {m: [] for m in METHODS}
    parents, locals_checked = 0, 0
    for company in companies:
        expected_global = {t["task_id"] for t in tasks if t["source_year"] == 2023 and t["company"] != company}
        for seed in SEEDS:
            parent_sequences, parent_pairs = set(), set()
            for method in METHODS:
                folder = FOLDER / "parents" / company / str(seed) / method
                weight = folder / "weights.pt"
                record = json.loads((folder / "metadata.json").read_text())
                if record["design_sha256"] != design_sha or record["weights_sha256"] != sha(weight) or record["excluded_company"] != company or set(record["training_task_ids"]) != expected_global or len(record["training_task_ids"]) != 260:
                    raise ValueError("Parent source/weight provenance differs")
                validate_exposure(record["exposure"], 256, 2048, 0)
                parent_sequences.add(record["exposure"]["case_sequence_sha256"])
                parent_pairs.add(record["exposure"]["sampled_witness_factor_pairs"])
                parent_times[method].append(record["exposure"]["wall_seconds"])
                torch.manual_seed(seed)
                initial = ScopeComposer(method, context_dimension)
                state = torch.load(weight, weights_only=True)
                initial_state = initial.state_dict()
                if set(state) != set(initial_state) or any(v.shape != initial_state[k].shape or not torch.isfinite(v).all() for k, v in state.items()):
                    raise ValueError("Invalid or mismatched parent parameters")
                if sum(v.numel() for v in state.values()) != design["parameter_counts"][method]:
                    raise ValueError("Reported active parameter count differs")
                geometry.append({"company": company, "seed": seed, "method": method,
                                 "geometry_scale": float(state["geometry_scale"]),
                                 "geometry_parameter_delta_l2": float(sum((v - initial_state[k]).square().sum() for k, v in state.items() if k.startswith("geometry.")).sqrt()),
                                 "geometry_hypotheses_are_not_certified_native_ownership": True})
                parents += 1
            if len(parent_sequences) != 1 or len(parent_pairs) != 1:
                raise ValueError("Parent training examples/factor budgets are not matched")
            for policy in POLICIES:
                for budget in BUDGETS[1:]:
                    expected_local = prefixes[policy][company][str(budget)]
                    if len(expected_local) != budget or any(by_id[i]["source_year"] != 2023 or by_id[i]["company"] != company for i in expected_local):
                        raise ValueError("Feedback crossed company/year boundary")
                    sequences, factor_counts = set(), set()
                    for method in METHODS:
                        weight = FOLDER / "local" / policy / company / method / str(seed) / (str(budget) + ".pt")
                        record = json.loads(weight.with_suffix(".json").read_text())
                        if record["design_sha256"] != design_sha or record["weights_sha256"] != sha(weight) or record["local_task_ids"] != expected_local:
                            raise ValueError("Local feedback/checkpoint provenance differs")
                        validate_exposure(record["exposure"], 64, 256, 256)
                        sequences.add(record["exposure"]["case_sequence_sha256"])
                        factor_counts.add(record["exposure"]["sampled_witness_factor_pairs"])
                        local_times[method].append(record["exposure"]["wall_seconds"])
                        locals_checked += 1
                    if len(sequences) != 1 or len(factor_counts) != 1:
                        raise ValueError("Local/replay examples and factor budgets differ")
    if parents != 126 or locals_checked != 504:
        raise ValueError("Incomplete parent/local checkpoint denominator")
    sources = source_hashes()
    for name, digest in design["source_files_sha256"].items():
        if sources[name] != digest or sha(FOLDER / "source_snapshot" / name) != digest:
            raise ValueError("Frozen algorithm source/snapshot changed")
    snapshot = FOLDER / "control_reviewer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "prediction_configurations_checked": manifest["prediction_files"], "parent_checkpoints_checked": parents,
        "local_checkpoints_checked": locals_checked, "same_actual_training_cases_and_witness_factor_budgets": True,
        "own_company_and_future_labels_excluded_from_parent_and_local_sources": True,
        "zero_feedback_policy_and_static_prior_invariance_checked": True,
        "parent_geometry_parameter_audit": geometry,
        "mean_parent_training_wall_seconds": {m: float(np.mean(v)) for m, v in parent_times.items()},
        "mean_local_training_wall_seconds": {m: float(np.mean(v)) for m, v in local_times.items()},
        "strict_wall_time_or_flop_equivalence_not_certified_by_this_audit": True,
        "prediction_manifest_sha256": sha(manifest_file), "reviewer_snapshot_sha256": sha(snapshot),
        "future_or_independent_scoring_references_read": False, "human_runtime_confirmations": 0,
        "native_visual_scope_teacher_certified": False, "independent_native_test_completed": False,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Full-binding control audit complete", parents, locals_checked, flush=True)


if __name__ == "__main__":
    review()

"""Actual-input engineering preflight of joint binding and weak-year learning.

All three controls use the same full binding cases, auxiliary link examples
and complete retrieval banks. Two optimizer steps test the integration; their
weights are discarded and no future accuracy or ownership score is computed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import torch
from native_scope_learning_v23 import source_hashes as v23_source_hashes
from native_witness_data_v23 import load_data
from native_witness_inventory_v23 import DEST as V23
from native_year_auxiliary_data_v24 import load_auxiliary
from native_year_composition_v24 import METHODS, YearScopeComposer
from native_year_fit_v24 import fit_year
from native_year_scope_features_v24 import DEST as AUXILIARY
from prepare import save, sha
from torch.utils.flop_counter import FlopCounterMode

DEST = AUXILIARY.parents[2] / "joint_binding_v1"
SOURCES = ("native_year_preflight_v24.py", "native_year_fit_v24.py", "native_year_composition_v24.py",
           "visual_year_scope_v24.py", "native_year_auxiliary_data_v24.py")


def preserve_previous(folder):
    output = folder / "engineering_preflight.json"
    if not output.exists():
        return None
    previous = json.loads(output.read_text())
    current = {name: sha(Path(__file__).with_name(name)) for name in SOURCES}
    if previous["source_files_sha256"] == current:
        raise RuntimeError("Completed joint integration preflight of this code is frozen")
    snapshots = {}
    for name, digest in previous["source_files_sha256"].items():
        original = folder / "source_snapshot" / name
        if sha(original) != digest:
            raise ValueError("Previous engineering snapshot changed before archival")
        snapshots[name] = original.read_bytes()
    archive = folder / "engineering_preflights" / sha(output)
    save(archive / "engineering_preflight.json", previous)
    for name, data in snapshots.items():
        copied = archive / "source_snapshot" / name
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_bytes(data)
    return sha(output)


def run():
    output = DEST / "engineering_preflight.json"
    previous_sha = preserve_previous(DEST)
    torch.set_num_threads(2)
    original_design = json.loads((V23 / "learning_v1/design.json").read_text())
    if original_design["source_files_sha256"] != v23_source_hashes():
        raise ValueError("Frozen v23 library code changed")
    tasks, _, packs, _, provenance = load_data()
    company = min(t["company"] for t in tasks)
    training = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
    inputs, labels, active, index, record = load_auxiliary(company)
    auxiliary = {"inputs": inputs, "labels": labels, "active": active, "index": index,
                 "excluded_company": company, "feature_manifest_sha256": record["source_feature_manifest_sha256"]}
    exposures, parameter_counts = {}, {}
    for method in METHODS:
        torch.manual_seed(19)
        model = YearScopeComposer(method, packs[0]["context"].numel())
        parameter_counts[method] = sum(p.numel() for p in model.parameters() if p.requires_grad)
        exposures[method] = fit_year(model, training, [], packs, auxiliary, 2, 19, stage="parent")
        print("Joint year-link full-input preflight", method, exposures[method], flush=True)
    if max(parameter_counts.values()) / min(parameter_counts.values()) > 1.01:
        raise ValueError("Active parameters differ by more than one percent")
    for key in ("case_sequence_sha256", "auxiliary_case_sequence_sha256", "sampled_local_cases",
                "sampled_auxiliary_year_cases", "sampled_witness_factor_pairs"):
        if len({r[key] for r in exposures.values()}) != 1:
            raise ValueError("Actual binding/auxiliary budgets differ between controls")
    files = {name: sha(Path(__file__).with_name(name)) for name in SOURCES}
    for name in SOURCES:
        snapshot = DEST / "source_snapshot" / name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(Path(__file__).with_name(name).read_bytes())
    save(output, {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "previous_engineering_preflight_preserved_sha256": previous_sha,
        "original_binding_provenance": provenance, "auxiliary_source_provenance": record,
        "source_files_sha256": files, "original_v23_source_files_sha256": v23_source_hashes(),
        "per_method_exposure": exposures, "active_parameter_counts": parameter_counts,
        "same_complete_common_inputs_and_case_sequences": True,
        "scope_and_memory_share_identical_original_semantic_memory_and_full_weak_link_memory": True,
        "generic_joint_control_also_receives_the_complete_weak_link_memory": True,
        "period_scale_is_positive_and_other_roles_keep_the_same_unrestricted_geometry": True,
        "scope_period_bounds_do_not_depend_on_the_queried_number_position": True,
        "initial_scores_are_not_claimed_identical_to_the_static_public_prior": True,
        "auxiliary_weight": 1., "auxiliary_loss": "0.5 BCE(direct logits) + 0.5 BCE(direct + common memory logits)",
        "bank_contains_only_other_company_historical_weak_year_links": True,
        "steps_per_method": 2, "engineering_weights_discarded": True,
        "no_method_predictions_or_accuracy_results_saved": True,
        "registered_dense_flops_counted": True,
        "registered_flop_counter_class": str(FlopCounterMode), "torch_version": torch.__version__,
        "unregistered_scalar_normalization_sorting_and_memory_costs_not_certified_by_flop_counter": True,
        "strict_total_compute_or_wall_time_equivalence_certified": False,
        "actual_equal_compute_frontier_required_before_independent_comparison": True,
        "formal_full_binding_experiment_started": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "future_semantic_or_reserved_reference_files_read": False,
        "native_scope_ownership_or_complete_period_teacher_certified": False,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Joint binding/year preflight complete; every engineering weight discarded", flush=True)


if __name__ == "__main__":
    run()

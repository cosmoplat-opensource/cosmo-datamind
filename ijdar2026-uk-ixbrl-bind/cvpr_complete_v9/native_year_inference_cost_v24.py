"""Historical-input inference cost probe, without saved predictions or scores.

Initialized models are profiled on three fixed historical queries. This
checks the decoder, complete banks and a head-only ablation. It does not
calibrate production budgets: trained retrieval choices and contention can
change costs, and registered FLOPs omit many scalar/sorting operations.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import torch
from native_scope_learning_v23 import bank_for
from native_witness_data_v23 import load_data
from native_year_auxiliary_data_v24 import load_auxiliary
from native_year_composition_v24 import METHODS, YearScopeComposer
from native_year_fit_v24 import configure_auxiliary
from native_year_preflight_v24 import DEST, SOURCES
from prepare import save, sha
from semantic_star_v23 import map_semantic_binding
from torch.utils.flop_counter import FlopCounterMode


def run():
    output = DEST / "historical_inference_cost_probe.json"
    if output.exists():
        raise RuntimeError("Completed historical inference cost probe is frozen")
    preflight_file = DEST / "engineering_preflight.json"
    preflight = json.loads(preflight_file.read_text())
    if not preflight["complete"] or preflight["source_files_sha256"] != {n: sha(Path(__file__).with_name(n)) for n in SOURCES}:
        raise ValueError("A current complete joint preflight is required")
    torch.set_num_threads(2)
    tasks, _, packs, _, provenance = load_data()
    if provenance != preflight["original_binding_provenance"]:
        raise ValueError("Binding data differ from the current preflight")
    company = preflight["auxiliary_source_provenance"]["excluded_company"]
    global_ids = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
    candidates = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] == company]
    probes = sorted(candidates, key=lambda i: tasks[i]["task_id"])[:3]
    if len(global_ids) != 260 or len(probes) != 3:
        raise ValueError("Historical parent/probe denominator differs")
    inputs, labels, active, index, record = load_auxiliary(company)
    auxiliary = {"inputs": inputs, "labels": labels, "active": active, "index": index,
                 "excluded_company": company, "feature_manifest_sha256": record["source_feature_manifest_sha256"]}
    bank = bank_for(global_ids, packs)
    results = {}
    for method in METHODS:
        torch.manual_seed(19)
        model = YearScopeComposer(method, packs[0]["context"].numel())
        configure_auxiliary(model, auxiliary)
        model.eval()
        with torch.no_grad():
            begin = time.perf_counter()
            with FlopCounterMode(display=False) as counter:
                cache = model.memory_cache(bank)
            cache_cost = {"wall_seconds": time.perf_counter() - begin,
                          "counted_registered_dense_flops": counter.get_total_flops()}
            records = {}
            for ablation in (False, True):
                model.geometry.period_head_off = ablation
                rows = []
                for i in probes:
                    begin = time.perf_counter()
                    with FlopCounterMode(display=False) as counter:
                        field, pairs, _ = model(packs[i], cache)
                        selected = map_semantic_binding(field, pairs)
                    if len(selected) != 4 or any(not torch.isfinite(p).all() for p in [field, *pairs]):
                        raise ValueError("Invalid historical full-binding inference")
                    rows.append({"task_id": tasks[i]["task_id"],
                                 "wall_seconds": time.perf_counter() - begin,
                                 "counted_registered_dense_flops": counter.get_total_flops(),
                                 "semantic_field_values": len(field),
                                 "semantic_leaf_values": [p.shape[1] for p in pairs],
                                 "visual_witness_factor_pairs": packs[i]["factor_pairs"]})
                records["period_head_off" if ablation else "full"] = rows
        results[method] = {"cache_preparation": cache_cost, "queries": records,
                           "original_semantic_bank_cases_available": len(bank),
                           "original_semantic_bank_used_by_architecture": model.has_memory,
                           "weak_year_bank_cases": len(model.geometry.memory.bank_inputs)}
        model.clear_auxiliary_cache()
        print("Historical full-binding inference cost probe", method, flush=True)
    snapshot = DEST / "inference_cost_probe_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "models": results, "excluded_company": company, "source_year": 2023,
        "joint_engineering_preflight_sha256": sha(preflight_file), "provenance": provenance,
        "inference_snapshot_sha256": sha(snapshot), "cpu_threads": torch.get_num_threads(),
        "initialized_unfitted_models_not_trained_experiment_checkpoints": True,
        "exact_same_historical_queries_banks_and_decoding_engine": True,
        "head_only_ablation_keeps_all_weak_year_memory_and_other_component_factors": True,
        "original_six_v23_algorithm_sources_not_modified": True,
        "measured_during_other_cpu_work_not_an_isolated_runtime_benchmark": True,
        "trained_checkpoint_costs_must_be_measured_again_before_budget_claims": True,
        "registered_dense_flops_are_not_total_computation": True,
        "no_binding_predictions_or_accuracy_outcomes_saved": True,
        "future_semantic_or_reserved_source_references_read": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "formal_full_binding_experiment_started": False,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Historical inference/ablation integration verified; no accuracy computed", flush=True)


if __name__ == "__main__":
    run()

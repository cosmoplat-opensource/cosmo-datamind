"""v33loco: as v33 with the few-shot priors fitted leave-one-company-out, same matched harness."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import torch
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import prediction
from native_scope_composition_v23 import METHODS as TRAINED_METHODS
from native_scope_composition_v23 import ScopeComposer, collapse_witnesses
from native_v33loco_data import load_data
from native_v30_bank import DEST as BANK30
DEST = BANK30.parent / "common_candidate_v33loco"
from prepare import save, sha
from semantic_star_v23 import (
    map_semantic_binding,
    partial_semantic_nll,
    selected_witness_posterior,
)
from torch import nn

FOLDER = DEST / "learning_v1"
METHODS = ("public_prior", "generic_relational", "scope_relational", "semantic_memory", "frozen_semantic_memory", "scope_geometry_zeroed")
POLICIES, SEEDS, BUDGETS = ("hash_prefix", "public_information_prefix"), (19, 41, 73), (0, 5, 10)
PRETRAIN_STEPS, LOCAL_STEPS = 256, 64
SOURCE_NAMES = ("native_scope_learning_v33loco.py", "native_scope_composition_v23.py", "native_v33loco_data.py", "native_structural_prior_v33_loco.py", "native_structural_prior_v33.py", "llm_fewshot_rank_v1.py", "llm_fewshot_baseline_v1.py", "native_structural_prior_v32.py", "native_v31_data.py", "native_structural_prior_v31.py", "llm_baseline_v30.py", "native_v30_data.py", "native_v30_bank.py", "native_v30_perception.py", "native_v30_render.py", "native_structural_prior_v30.py", "native_v29_data.py", "native_structural_prior_v29.py", "native_v28_data.py", "native_structural_prior_v28.py", "native_v27_data.py", "native_v26_data.py", "native_v26_bank.py", "native_structural_prior_v27.py",
                "native_scope_score_v33loco.py", "visual_scope_geometry_v23.py", "semantic_star_v23.py", "native_witness_data_v23.py")


def source_hashes():
    return {name: sha(Path(__file__).with_name(name)) for name in SOURCE_NAMES}


def bank_for(indices, packs):
    if len(indices) != len(set(indices)) or any(packs[i]["year"] != 2023 for i in indices):
        raise ValueError("Memory may contain unique historical feedback cases only")
    return {i: packs[i] for i in indices}


def update(phase, **details):
    record = {"phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
              "training_started": True, "independent_native_test_completed": False,
              "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False, **details}
    record.setdefault("completed_prediction_files", len(list((FOLDER / "predictions").rglob("*.json"))))
    save(FOLDER / "progress.json", record)
    save(DEST / "pipeline_progress.json", record)


def fit(model, local, replay, packs, steps, seed, resume=None, design_sha=None, progress=None):
    bank = bank_for(local + replay, packs)
    active = [i for i in local if any(mask.any() for mask in packs[i]["masks"])]
    older = [i for i in replay if any(mask.any() for mask in packs[i]["masks"])]
    exposure = {"optimizer_steps": 0, "sampled_local_cases": 0, "sampled_replay_cases": 0,
                "sampled_witness_factor_pairs": 0, "wall_seconds": 0.0, "case_sequence_sha256": None}
    if not active:
        return exposure
    rng = np.random.default_rng(seed + 20261016)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    sequence, start = [], 0
    if resume is not None and resume.exists():
        record = torch.load(resume, weights_only=True)
        if record["design_sha256"] != design_sha or record["steps"] != steps:
            raise ValueError("Partial optimizer checkpoint belongs to a different design/budget")
        model.load_state_dict(record["model"])
        optimizer.load_state_dict(record["optimizer"])
        rng.bit_generator.state = record["rng_state"]
        exposure, sequence, start = record["exposure"], record["sequence"], record["step"]
        if start > steps:
            raise ValueError("Partial checkpoint exceeds its optimizer budget")
    model.train()
    begin, elapsed = time.perf_counter(), exposure["wall_seconds"]
    for step in range(start, steps):
        batch = rng.choice(active, min(4 if older else 8, len(active)), replace=False).tolist()
        old = rng.choice(older, min(4, len(older)), replace=False).tolist() if older else []
        optimizer.zero_grad()
        cache = model.memory_cache(bank)
        losses = []
        for i in batch + old:
            field, pairs, _ = model(packs[i], cache, exclude=i)
            loss = partial_semantic_nll(field, pairs, packs[i]["masks"])
            if loss is None or not torch.isfinite(loss):
                raise ValueError("Nonfinite/unobserved full-binding loss")
            losses.append(loss)
        torch.stack(losses).mean().backward()
        if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
            raise ValueError("Nonfinite full-binding gradient")
        nn.utils.clip_grad_norm_(model.parameters(), 5)
        optimizer.step()
        exposure["optimizer_steps"] = step + 1
        exposure["sampled_local_cases"] += len(batch)
        exposure["sampled_replay_cases"] += len(old)
        exposure["sampled_witness_factor_pairs"] += sum(packs[i]["factor_pairs"] for i in batch + old)
        sequence.append(batch + old)
        if (step + 1) % 32 == 0 or step + 1 == steps:
            exposure["wall_seconds"] = elapsed + time.perf_counter() - begin
            exposure["case_sequence_sha256"] = hashlib.sha256(json.dumps(sequence).encode()).hexdigest()
            if resume is not None:
                resume.parent.mkdir(parents=True, exist_ok=True)
                temporary = resume.with_suffix(".tmp")
                torch.save({"design_sha256": design_sha, "steps": steps, "step": step + 1,
                            "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                            "rng_state": rng.bit_generator.state, "exposure": exposure, "sequence": sequence}, temporary)
                temporary.replace(resume)
            if progress:
                progress(step + 1, exposure)
    return exposure


def prior_factors(pack):
    unaries = pack["prior"]
    pairs = [p[:, g] for p, g in zip(pack["pair_prior"], pack["groups"], strict=True)]
    semantic = [collapse_witnesses(pair + unary[None, :], group, count)
                for pair, unary, group, count in zip(pairs, unaries[1:], pack["groups"], pack["group_counts"], strict=True)]
    return unaries[0], semantic, (unaries, pairs, None)


def predict(model, pack, task, item, bank_cache=None, geometry_off=False):
    field, pairs, raw = prior_factors(pack) if model is None else model(pack, bank_cache, geometry_off=geometry_off)
    selected = map_semantic_binding(field, pairs)
    choices = {c: item["components"][c][j]["value"] for c, j in zip(CHANNELS, selected, strict=True)}
    result = prediction(task, choices)
    result["selected_semantic_groups"] = selected
    result["all_semantic_values_were_available_without_top_k_pruning"] = True
    result["visual_witness_posteriors"] = {}
    unaries, raw_pairs, field_occurrences = raw
    for role, channel in enumerate(CHANNELS[1:]):
        indices, probabilities = selected_witness_posterior(unaries[role + 1], raw_pairs[role], pack["groups"][role], selected[0], selected[role + 1])
        result["visual_witness_posteriors"][channel] = [{"witness_index": int(j), "probability": float(p)} for j, p in zip(indices, probabilities, strict=True)]
    if field_occurrences is not None:
        result["field_glyph_pool_posterior"] = torch.softmax(field_occurrences[selected[0]], 0).tolist()
    quantity = choices["unit_quantity"]
    result["quantity_is_uncertain_dash_zero_hypothesis"] = bool(quantity and Decimal(quantity["quantity"]) == 0 and any(q.get("semantic_zero_hypothesis_not_literal_zero") for q in task["quantity_candidates"]))
    period = choices["period"]
    matching = [p for p in task["period_candidates"] if p["period"].get("complete") and {k: v for k, v in p["period"].items() if k != "complete"} == period]
    result["period_uses_uncertain_calendar_convention"] = bool(matching and all(p.get("calendar_convention_assumed") for p in matching))
    result["witness_posterior_is_uncalibrated_and_not_a_visual_ownership_certificate"] = True
    return result


def preflight():
    torch.set_num_threads(2)
    tasks, _, packs, _, provenance = load_data()
    company = min(t["company"] for t in tasks)
    training = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
    records = {}
    for method in TRAINED_METHODS:
        torch.manual_seed(19)
        model = ScopeComposer(method, packs[0]["context"].numel())
        records[method] = fit(model, training, [], packs, 2, 19)
        print("Native preflight", method, records[method]["wall_seconds"], "seconds / 2 optimizer steps", flush=True)
    sequences = {r["case_sequence_sha256"] for r in records.values()}
    if len(sequences) != 1:
        raise ValueError("Matched learners sampled different training cases")
    record = {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
         "source_files_sha256": source_hashes(), "provenance": provenance, "per_method": records,
         "steps_per_method": 2, "engineering_test_only_no_model_accuracy_computed": True,
         "engineering_weights_are_discarded_not_used_as_experiment_parents": True,
         "future_or_independent_reference_files_read": False, "all_original_cvpr_experiments_completed": False}
    existing = DEST / "engineering_preflight.json"
    if existing.exists():
        save(DEST / "engineering_preflights" / (sha(existing) + ".json"), json.loads(existing.read_text()))
    save(existing, record)


def run():
    torch.set_num_threads(2)
    sources = source_hashes()
    engineering = json.loads((DEST / "engineering_preflight.json").read_text())
    if not engineering["complete"] or engineering["source_files_sha256"] != sources:
        raise ValueError("A passing engineering preflight of the current code is required")
    if (FOLDER / "manifest.json").exists():
        from native_scope_score_v33loco import validate_predictions
        validate_predictions()
        result = FOLDER / "development_results.json"
        if result.exists():
            if json.loads(result.read_text())["prediction_manifest_sha256"] != sha(FOLDER / "manifest.json"):
                raise ValueError("Previously scored prediction manifest changed")
        else:
            subprocess.run([sys.executable, str(Path(__file__).with_name("native_scope_score_v33loco.py"))], check=True)
        update("development_scoring_complete", completed_prediction_files=1512)
        return
    tasks, inventory, packs, policies, provenance = load_data()
    if engineering["provenance"] != provenance:
        raise ValueError("Inputs changed since the full-input engineering preflight")
    index = {t["task_id"]: i for i, t in enumerate(tasks)}
    companies = sorted(policies[POLICIES[0]])
    counts = {m: sum(p.numel() for p in ScopeComposer(m, packs[0]["context"].numel()).parameters()) for m in TRAINED_METHODS}
    if max(counts.values()) / min(counts.values()) > 1.01:
        raise ValueError("Active parameter counts differ by more than one percent")
    design = {"methods": list(METHODS), "trained_methods": list(TRAINED_METHODS), "seeds": list(SEEDS),
        "policies": list(POLICIES), "automatic_feedback_budgets": list(BUDGETS), "parameter_counts": counts,
        "parent_optimizer_steps": PRETRAIN_STEPS, "maximum_local_optimizer_steps": LOCAL_STEPS,
        "optimizer": "AdamW", "learning_rate": .001, "weight_decay": .01, "gradient_clip": 5,
        "parent_cases_per_step": 8, "local_cases_per_step": 4, "replay_cases_per_step": 4,
        "hybrid_memory_neighbors_per_observed_role_or_pair": 8,
        "same_complete_witness_bank_for_scope_and_strong_memory": True,
        "same_full_semantic_candidates_public_relations_and_exact_composition": True,
        "no_teacher_component_is_converted_into_a_false_unknown_target": True,
        "scope_and_memory_control_differ_in_target_independent_range_vs_free_geometry": True,
        "geometry_zeroing_is_weight_tied_inference_ablation_not_retraining": True,
        "frozen_memory_adds_local_feedback_without_local_gradient_updates": True,
        "own_company_excluded_from_parent_training": True,
        "training_case_sequences_and_optimizer_budgets_matched": True,
        "actual_runtime_and_memory_work_are_measured_not_assumed_equal": True,
        "strict_flop_and_wall_time_equivalence_not_claimed": True,
        "source_files_sha256": sources, "provenance": provenance,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "development_after_v21_not_independent_test": True, "native_visual_scope_teacher_certified": False,
        "future_or_independent_reference_files_read": False}
    design_file = FOLDER / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Frozen full-binding design changed")
    save(design_file, design)
    design_sha = sha(design_file)
    save(FOLDER / "feedback_prefixes.json", policies)
    for name in SOURCE_NAMES:
        destination = FOLDER / "source_snapshot" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(Path(__file__).with_name(name).read_bytes())
    update("training_and_prediction", parameter_counts=counts, completed_prediction_files=len(list((FOLDER / "predictions").rglob("*.json"))))
    for company in companies:
        global_ids = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
        future = [i for i, t in enumerate(tasks) if t["source_year"] != 2023 and t["company"] == company]
        for seed in SEEDS:
            parents, parent_exposures = {}, {}
            for method in TRAINED_METHODS:
                torch.manual_seed(seed)
                model = ScopeComposer(method, packs[0]["context"].numel())
                folder = FOLDER / "parents" / company / str(seed) / method
                weight, metadata = folder / "weights.pt", folder / "metadata.json"
                if metadata.exists():
                    previous = json.loads(metadata.read_text())
                    if previous["weights_sha256"] != sha(weight) or previous["design_sha256"] != design_sha or previous["excluded_company"] != company:
                        raise ValueError("Frozen parent differs")
                    model.load_state_dict(torch.load(weight, weights_only=True))
                    exposure = previous["exposure"]
                else:
                    exposure = fit(model, global_ids, [], packs, PRETRAIN_STEPS, seed, folder / "optimizer.resume.pt", design_sha,
                        lambda step, exp, m=method, c=company, s=seed: update("parent_training", company=c, seed=s, method=m, optimizer_step=step, exposure=exp))
                    folder.mkdir(parents=True, exist_ok=True)
                    torch.save(model.state_dict(), weight)
                    save(metadata, {"design_sha256": design_sha, "weights_sha256": sha(weight), "excluded_company": company,
                                    "training_task_ids": [tasks[i]["task_id"] for i in global_ids], "exposure": exposure})
                parents[method], parent_exposures[method] = model, exposure
                print("Native full-graph parent", company, seed, method, exposure["wall_seconds"], flush=True)
            if len({e["case_sequence_sha256"] for e in parent_exposures.values()}) != 1:
                raise ValueError("Parent case budgets differ across matched controls")
            for policy in POLICIES:
                for budget in BUDGETS:
                    local = [index[ident] for ident in policies[policy][company][str(budget)]]
                    if len(local) != budget or any(packs[i]["year"] != 2023 or packs[i]["company"] != company for i in local):
                        raise ValueError("Automatic feedback crossed its source/year budget")
                    bank = bank_for(global_ids + local, packs)
                    exposures = {}
                    for method in METHODS:
                        output = FOLDER / "predictions" / policy / company / method / str(seed) / (str(budget) + ".json")
                        if output.exists():
                            r = json.loads(output.read_text())
                            if r["design_sha256"] != design_sha or [p["task_id"] for p in r["predictions"]] != [tasks[i]["task_id"] for i in future]:
                                raise ValueError("Frozen prediction configuration differs")
                            continue
                        base = "semantic_memory" if method == "frozen_semantic_memory" else "scope_relational" if method == "scope_geometry_zeroed" else method
                        model = None if base == "public_prior" else deepcopy(parents[base])
                        exposure = {"optimizer_steps": 0, "sampled_local_cases": 0, "sampled_replay_cases": 0,
                                    "sampled_witness_factor_pairs": 0, "wall_seconds": 0.0, "case_sequence_sha256": None}
                        if model is not None and budget and method != "frozen_semantic_memory":
                            checkpoint = FOLDER / "local" / policy / company / base / str(seed) / (str(budget) + ".pt")
                            meta_file = checkpoint.with_suffix(".json")
                            if meta_file.exists():
                                previous = json.loads(meta_file.read_text())
                                if previous["design_sha256"] != design_sha or previous["weights_sha256"] != sha(checkpoint):
                                    raise ValueError("Frozen local checkpoint differs")
                                model.load_state_dict(torch.load(checkpoint, weights_only=True))
                                exposure = previous["exposure"]
                            else:
                                exposure = fit(model, local, global_ids, packs, LOCAL_STEPS, seed, checkpoint.with_suffix(".resume.pt"), design_sha,
                                    lambda step, exp, m=method, c=company, s=seed, p=policy, b=budget: update("local_training", company=c, seed=s, method=m, policy=p,
                                                                      automatic_feedback_cases=b, optimizer_step=step, exposure=exp))
                                checkpoint.parent.mkdir(parents=True, exist_ok=True)
                                torch.save(model.state_dict(), checkpoint)
                                save(meta_file, {"design_sha256": design_sha, "weights_sha256": sha(checkpoint), "exposure": exposure,
                                                 "local_task_ids": [tasks[i]["task_id"] for i in local]})
                            exposures[base] = exposure
                        begin = time.perf_counter()
                        predictions = []
                        with torch.no_grad():
                            if model is not None:
                                model.eval()
                            cache = model.memory_cache(bank) if model is not None else None
                            for i in future:
                                predictions.append(predict(model, packs[i], tasks[i], inventory[i], cache, method == "scope_geometry_zeroed"))
                        save(output, {"design_sha256": design_sha, "company": company, "seed": seed, "method": method, "policy": policy,
                                      "automatic_feedback_cases": budget, "exposure": exposure, "predictions": predictions,
                                      "inference_wall_seconds": time.perf_counter() - begin,
                                      "inference_witness_factor_pairs": sum(packs[i]["factor_pairs"] for i in future),
                                      "same_complete_candidates_and_evidence": True, "future_reference_files_read": False})
                    if exposures and len({e["case_sequence_sha256"] for e in exposures.values()}) != 1:
                        raise ValueError("Local case budgets differ across matched controls")
            update("training_and_prediction", company=company, seed=seed,
                   completed_prediction_files=len(list((FOLDER / "predictions").rglob("*.json"))))
    files = sorted((FOLDER / "predictions").rglob("*.json"))
    if len(files) != 1512 or sources != source_hashes():
        raise ValueError("All 1512 configurations and unchanged code required before scoring")
    save(FOLDER / "manifest.json", {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
         "design_sha256": design_sha, "prediction_files": len(files),
         "predictions_sha256": {str(p.relative_to(FOLDER)): sha(p) for p in files},
         "future_or_independent_reference_files_read": False})
    update("all_predictions_frozen", completed_prediction_files=len(files))
    subprocess.run([sys.executable, str(Path(__file__).with_name("native_scope_score_v33loco.py"))], check=True)
    update("development_scoring_complete", completed_prediction_files=len(files))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    try:
        preflight() if args.preflight else run()
    except Exception as error:
        if not args.preflight and (FOLDER / "design.json").exists():
            update("failed", error_type=type(error).__name__, error=str(error)[:300])
        raise

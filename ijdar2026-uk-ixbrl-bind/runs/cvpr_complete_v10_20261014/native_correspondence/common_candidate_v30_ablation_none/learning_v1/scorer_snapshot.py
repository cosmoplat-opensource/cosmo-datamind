"""v30 ablation none scorer: v23 scoring plus per-component paired comparisons and availability-conditioned selection accuracy."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from native_scope_learning_v30_ablation_none import (
    BUDGETS,
    FOLDER,
    METHODS,
    POLICIES,
    SEEDS,
    source_hashes,
)
from native_v30_bank import DEST as BANK
from prepare import save, sha
from decimal import Decimal

from native_binding_features_v17 import quantity_value
from score_native_binding_v17 import component_correct, scoring_references


def validate_predictions():
    manifest_file = FOLDER / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    design_file = FOLDER / "design.json"
    design = json.loads(design_file.read_text())
    if not manifest["complete"] or manifest["prediction_files"] != 1512 or sha(design_file) != manifest["design_sha256"]:
        raise ValueError("All 1512 configurations must freeze before reference reads")
    if design["source_files_sha256"] != source_hashes():
        raise ValueError("Full-binding code changed after the design freeze")
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    companies = sorted({t["company"] for t in future.values()})
    if len(future) != 560 or len(companies) != 14 or len(manifest["predictions_sha256"]) != 1512:
        raise ValueError("Complete future development denominator required")
    data = {}
    for name, digest in manifest["predictions_sha256"].items():
        path = FOLDER / name
        if sha(path) != digest:
            raise ValueError("Frozen prediction changed")
        record = json.loads(path.read_text())
        expected = {ident for ident, task in future.items() if task["company"] == record["company"]}
        if record["design_sha256"] != manifest["design_sha256"] or len(record["predictions"]) != len(expected) or {p["task_id"] for p in record["predictions"]} != expected:
            raise ValueError("Duplicate/missing targets in a prediction configuration")
        key = (record["policy"], record["method"], record["seed"], record["automatic_feedback_cases"])
        if record["company"] in data.setdefault(key, {}):
            raise ValueError("Duplicate configuration")
        if any(p["visual_header_ownership_certified"] or p["scope_assignment"] != "unknown" for p in record["predictions"]):
            raise ValueError("Semantic predictions must not certify visual header ownership")
        data[key][record["company"]] = record
    for policy in POLICIES:
        for method in METHODS:
            for seed in SEEDS:
                for budget in BUDGETS:
                    if set(data.get((policy, method, seed, budget), {})) != set(companies):
                        raise ValueError("Missing matched configuration")
    for company in companies:
        for method in METHODS:
            for seed in SEEDS:
                a, b = [data[p, method, seed, 0][company]["predictions"] for p in POLICIES]
                if a != b:
                    raise ValueError("Zero-feedback predictions depend on selection policy")
        for policy in POLICIES:
            for seed in SEEDS:
                if len({json.dumps(data[policy, "public_prior", seed, b][company]["predictions"], sort_keys=True) for b in BUDGETS}) != 1:
                    raise ValueError("Static public prior changed with feedback")
    prefixes = json.loads((FOLDER / "feedback_prefixes.json").read_text())
    for policy in prefixes.values():
        for row in policy.values():
            if row["0"] or row["5"] != row["10"][:5] or len(set(row["10"])) != 10:
                raise ValueError("Non-nested or duplicate feedback cases")
    return manifest_file, manifest, future, companies, data


def availability(task, reference):
    """Whether the reference value is present in the common candidate bank (reference-derived; scoring only)."""
    field = any("{" + c["namespace"] + "}" + c["name"] == reference["field"] for c in task["field_candidates"])
    period = any(p["period"].get("complete") and {k: v for k, v in p["period"].items() if k != "complete"} == reference["period"]
                 for p in task["period_candidates"])
    dims = any(sorted(d["dimensions"], key=lambda x: x["axis"]) == sorted(reference["dimensions"], key=lambda x: x["axis"]) for d in task["dimension_tuple_candidates"])
    target = Decimal(reference["quantity"])
    unit_quantity = any(u.get("unit") == reference["unit"] and u.get("multiplier") is not None
                        and Decimal(quantity_value(q["printed_value"], u["multiplier"], s)) == target
                        for q in task["quantity_candidates"] for u in task["unit_candidates"] for s in q["semantic_sign_candidates"])
    return {"field": field, "period": period, "dimensions": dims, "unit_quantity": unit_quantity,
            "complete": field and period and dims and unit_quantity}


def run():
    output = FOLDER / "development_results.json"
    if output.exists():
        raise RuntimeError("Full-binding development results already frozen")
    manifest_file, _manifest, future, companies, data = validate_predictions()
    # This is the first use of future semantic references in this program.
    references, reference_hashes = scoring_references(future)
    results, outcomes, means, changes, component_outcomes = {}, {}, {}, {}, {}
    bank_tasks = {t["task_id"]: t for t in json.loads((BANK / "public/tasks.json").read_text())}
    available = {ident: availability(bank_tasks[ident], references[ident]) for ident in future}
    available_counts = {k: sum(a[k] for a in available.values()) for k in next(iter(available.values()))}
    for policy in POLICIES:
        for method in METHODS:
            for seed in SEEDS:
                for budget in BUDGETS:
                    configurations = data[policy, method, seed, budget]
                    predictions = {p["task_id"]: p for record in configurations.values() for p in record["predictions"]}
                    if set(predictions) != set(future):
                        raise ValueError("Full-binding scorer denominator changed")
                    rows = {ident: component_correct(p["predicted_binding"], references[ident]) for ident, p in predictions.items()}
                    label = f"{policy}/{method}/{seed}/{budget}"
                    outcomes[label] = {ident: row["semantic_tuple_exact"] for ident, row in rows.items()}
                    component_outcomes[label] = {c: {ident: row[c] for ident, row in rows.items()} for c in ("field", "period", "dimensions", "unit", "quantity")}
                    zero = [ident for ident, p in predictions.items() if p["quantity_is_uncertain_dash_zero_hypothesis"]]
                    results[label] = {
                        "correct": sum(outcomes[label].values()), "total": len(future),
                        "semantic_tuple_exact_accuracy": sum(outcomes[label].values()) / len(future),
                        "component_accuracy": {c: np.mean([r[c] for r in rows.values()]).item() for c in next(iter(rows.values())) if c != "semantic_tuple_exact"},
                        "complete_tuple_returned_coverage": np.mean([p["semantic_tuple_complete"] for p in predictions.values()]).item(),
                        "dash_zero_hypotheses_returned": len(zero), "incorrect_dash_zero_quantity": sum(not rows[i]["quantity"] for i in zero),
                        "actual_local_optimizer_steps": sum(r["exposure"]["optimizer_steps"] for r in configurations.values()),
                        "local_training_wall_seconds": sum(r["exposure"]["wall_seconds"] for r in configurations.values()),
                        "inference_wall_seconds": sum(r["inference_wall_seconds"] for r in configurations.values()),
                        "sampled_training_witness_factor_pairs": sum(r["exposure"]["sampled_witness_factor_pairs"] for r in configurations.values()),
                        "inference_witness_factor_pairs": sum(r["inference_witness_factor_pairs"] for r in configurations.values()),
                        "per_company": {c: sum(v for i, v in outcomes[label].items() if future[i]["company"] == c) for c in companies},
                        "selection_accuracy_given_complete_candidate_available": sum(outcomes[label][i] for i in future if available[i]["complete"]) / max(1, available_counts["complete"])}
    for policy in POLICIES:
        for method in METHODS:
            for budget in BUDGETS:
                values = [results[f"{policy}/{method}/{seed}/{budget}"] for seed in SEEDS]
                means[f"{policy}/{method}/{budget}"] = {k: float(np.mean([v[k] for v in values])) for k in values[0] if k not in {"per_company", "component_accuracy"}}
                means[f"{policy}/{method}/{budget}"]["component_accuracy"] = {c: float(np.mean([v["component_accuracy"][c] for v in values])) for c in values[0]["component_accuracy"]}
                if budget:
                    changes[f"{policy}/{method}/{budget}"] = [{"seed": seed,
                        "fixed": sum(not outcomes[f"{policy}/{method}/{seed}/0"][i] and outcomes[f"{policy}/{method}/{seed}/{budget}"][i] for i in future),
                        "newly_wrong": sum(outcomes[f"{policy}/{method}/{seed}/0"][i] and not outcomes[f"{policy}/{method}/{seed}/{budget}"][i] for i in future)} for seed in SEEDS]
    rng, paired = np.random.default_rng(20261016), {}
    for policy in POLICIES:
        for budget in BUDGETS:
            for baseline in (m for m in METHODS if m != "scope_relational"):
                grouped = defaultdict(list)
                for seed in SEEDS:
                    a, b = [outcomes[f"{policy}/{method}/{seed}/{budget}"] for method in ("scope_relational", baseline)]
                    for ident in future:
                        grouped[future[ident]["company"]].append(int(a[ident]) - int(b[ident]))
                values = np.array([np.mean(grouped[c]) for c in companies])
                draws = values[rng.integers(0, len(values), size=(4000, len(values)))].mean(1)
                paired[f"{policy}/{budget}/{baseline}"] = {
                    "mean_semantic_tuple_accuracy_difference": float(values.mean()),
                    "company_identifier_cluster_bootstrap_95_ci": np.quantile(draws, [.025, .975]).tolist(),
                    "development_only_no_multiple_testing_corrected_or_independent_test_claim": True}
    component_paired = {}
    for policy in POLICIES:
        for budget in BUDGETS:
            for baseline in (m for m in METHODS if m != "scope_relational"):
                for component in ("field", "period", "dimensions", "unit", "quantity"):
                    grouped = defaultdict(list)
                    for seed in SEEDS:
                        a, b = [component_outcomes[f"{policy}/{method}/{seed}/{budget}"][component] for method in ("scope_relational", baseline)]
                        for ident in future:
                            grouped[future[ident]["company"]].append(int(a[ident]) - int(b[ident]))
                    values = np.array([np.mean(grouped[c]) for c in companies])
                    draws = values[rng.integers(0, len(values), size=(4000, len(values)))].mean(1)
                    component_paired[f"{policy}/{budget}/{baseline}/{component}"] = {
                        "mean_difference": float(values.mean()), "company_cluster_bootstrap_95_ci": np.quantile(draws, [.025, .975]).tolist()}
    snapshot = FOLDER / "scorer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(), "unique_future_targets": 560,
        "company_identifiers": 14, "per_configuration": results, "seed_means": means, "paired_comparisons": paired,
        "feedback_error_propagation": changes, "prediction_manifest_sha256": sha(manifest_file),
        "component_paired_comparisons": component_paired,
        "candidate_availability_counts_reference_derived": available_counts,
        "candidate_availability_per_target": available,
        "scoring_reference_and_source_sha256": reference_hashes, "scorer_snapshot_sha256": sha(snapshot),
        "all_controls_share_complete_semantic_candidates_witnesses_and_exact_composition": True,
        "candidate_expansion_and_numeric_parse_gains_are_common_not_ontology_attribution": True,
        "scope_and_strong_memory_both_use_the_same_complete_partial_prototype_bank": True,
        "scope_geometry_zeroing_is_weight_tied_inference_ablation": True,
        "runtime_is_reported_and_not_claimed_strictly_equal": True,
        "entity_is_common_report_metadata_assistance": True, "human_runtime_confirmations": 0,
        "semantic_correctness_does_not_certify_visual_ownership": True,
        "independent_native_test_completed": False, "ontology_unique_advantage_established": False,
        "all_original_cvpr_experiments_completed": False})
    print("Full-value witnessed native development scoring complete", flush=True)


if __name__ == "__main__":
    run()

"""Post-freeze DEVELOPMENT diagnosis of value availability and geometry effects.

No predictor or training feature is changed. Full candidate ceilings are not
model accuracies. Period-year errors, same-year interval errors and missing
values are separated before deciding whether year-link supervision can help.
"""

import json
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from native_binding_learning_v17 import prediction
from native_scope_learning_v23 import BUDGETS, FOLDER, POLICIES, SEEDS
from native_scope_score_v23 import validate_predictions
from native_witness_inventory_v23 import DEST as WITNESSES
from prepare import save, sha
from score_native_binding_v17 import component_correct, scoring_references

DEST = WITNESSES / "posthoc_development_v24"
ROLES = ("field", "period", "dimensions", "unit_quantity")
NONPERIOD = ("field", "entity", "unit", "dimensions", "quantity")


def availability(task, item, reference):
    result = {key: False for key in (*ROLES, "unit", "quantity")}
    empty = dict.fromkeys(ROLES)
    for role in ROLES:
        for group in item["components"][role]:
            choices = {**empty, role: group["value"]}
            correct = component_correct(prediction(task, choices)["predicted_binding"], reference)
            if role == "unit_quantity":
                result[role] |= correct["unit"] and correct["quantity"]
                result["unit"] |= correct["unit"]
                result["quantity"] |= correct["quantity"]
            else:
                result[role] |= correct[role]
    result["entity"] = component_correct(prediction(task, empty)["predicted_binding"], reference)["entity"]
    result["complete_binding_available"] = result["entity"] and all(result[role] for role in ROLES)
    return result


def end_year(period):
    if not period:
        return None
    value = period.get("instant") if period.get("kind") == "instant" else period.get("end_date")
    return value[:4] if isinstance(value, str) and re.match(r"^\d{4}-", value) else None


def period_error(selected, expected, correct_value_available):
    if selected == expected:
        return "correct"
    if not correct_value_available:
        return "correct_complete_period_absent_from_public_candidates"
    if selected is None:
        return "unknown_returned_despite_correct_period_available"
    a, b = end_year(selected), end_year(expected)
    if a is None or b is None:
        return "unclassified_period_representation"
    if a != b:
        return "selected_wrong_declared_end_year"
    if selected["kind"] != expected["kind"]:
        return "same_end_year_but_wrong_instant_or_duration_kind"
    return "same_end_year_and_kind_but_wrong_complete_date_or_interval"


def nonperiod_state(binding):
    result = {key: binding.get(key) for key in NONPERIOD}
    if result["dimensions"] is not None:
        result["dimensions"] = sorted(result["dimensions"], key=lambda d: d["axis"])
    if result["quantity"] is not None:
        result["quantity"] = str(Decimal(result["quantity"]).normalize())
    return result


def literal_formats(period):
    result = Counter()
    for key in ("instant", "start_date", "end_date"):
        value = period.get(key)
        if not isinstance(value, str):
            continue
        result["date_time" if "T" in value else "date_only"] += 1
        result["explicit_timezone" if re.search(r"Z$|[+-]\d{2}:\d{2}$", value) else "no_explicit_timezone"] += 1
    return result


def run():
    output = DEST / "selection_diagnosis.json"
    if output.exists():
        raise RuntimeError("Completed post-freeze development diagnosis is frozen")
    manifest_file, _, future, companies, configurations = validate_predictions()
    scores_file = FOLDER / "development_results.json"
    scores = json.loads(scores_file.read_text())
    if not scores["complete"] or scores["prediction_manifest_sha256"] != sha(manifest_file):
        raise ValueError("All development predictions and scores must freeze before diagnosis")
    # No semantic reference is read before complete prediction verification.
    references, hashes = scoring_references(future)
    if hashes != scores["scoring_reference_and_source_sha256"]:
        raise ValueError("Post-hoc references differ from the frozen original score")
    inventory_file = WITNESSES / "public/witnesses.json"
    inventory_manifest_file = WITNESSES / "public/manifest.json"
    inventory_manifest = json.loads(inventory_manifest_file.read_text())
    if sha(inventory_file) != inventory_manifest["witness_inventory_sha256"]:
        raise ValueError("Original public candidate inventory changed")
    inventory = {row["task_id"]: row for row in json.loads(inventory_file.read_text())}
    available = {ident: availability(task, inventory[ident], references[ident]) for ident, task in future.items()}
    counts = Counter()
    reference_formats, candidate_formats = Counter(), Counter()
    for ident, flags in available.items():
        counts.update({key: int(value) for key, value in flags.items()})
        reference_formats.update(literal_formats(references[ident]["period"]))
        for row in inventory[ident]["components"]["period"]:
            if row["value"]:
                candidate_formats.update(literal_formats(row["value"]))
    comparisons = {}
    for policy in POLICIES:
        for budget in BUDGETS:
            totals = Counter()
            per_seed = []
            for seed in SEEDS:
                records = {}
                for method in ("scope_relational", "scope_geometry_zeroed"):
                    records[method] = {p["task_id"]: p["predicted_binding"] for record in configurations[policy, method, seed, budget].values()
                                       for p in record["predictions"]}
                local = Counter()
                for ident in future:
                    on, off = records["scope_relational"][ident], records["scope_geometry_zeroed"][ident]
                    a, b = component_correct(on, references[ident]), component_correct(off, references[ident])
                    changed_period = on["period"] != off["period"]
                    same_other = nonperiod_state(on) == nonperiod_state(off)
                    local["period_value_changed"] += changed_period
                    local["nonperiod_semantic_values_changed"] += not same_other
                    other_on, other_off = all(a[k] for k in NONPERIOD), all(b[k] for k in NONPERIOD)
                    local["full_tuple_corrected_by_geometry"] += a["semantic_tuple_exact"] and not b["semantic_tuple_exact"]
                    local["full_tuple_damaged_by_geometry"] += b["semantic_tuple_exact"] and not a["semantic_tuple_exact"]
                    if other_on and other_off:
                        local["period_corrected_other_components_correct"] += a["period"] and not b["period"]
                        local["period_damaged_other_components_correct"] += b["period"] and not a["period"]
                    elif not other_on and not other_off:
                        local["period_corrected_other_components_already_wrong"] += a["period"] and not b["period"]
                        local["period_damaged_other_components_already_wrong"] += b["period"] and not a["period"]
                    else:
                        local["other_component_correctness_changed"] += 1
                totals.update(local)
                per_seed.append({"seed": seed, "counts": dict(local)})
            comparisons[f"{policy}/{budget}"] = {"seed_target_evaluations": 560 * len(SEEDS), "counts": dict(totals), "per_seed": per_seed}
    selection = {}
    for method in ("generic_relational", "semantic_memory", "scope_relational"):
        counts_missing, period_errors = Counter(), Counter()
        counts_select = Counter()
        for seed in SEEDS:
            for record in configurations["public_information_prefix", method, seed, 10].values():
                for row in record["predictions"]:
                    ident, binding = row["task_id"], row["predicted_binding"]
                    correct = component_correct(binding, references[ident])
                    flags = available[ident]
                    if correct["semantic_tuple_exact"]:
                        if not flags["complete_binding_available"]:
                            raise ValueError("A correct prediction is absent from its claimed common candidate pool")
                        counts_select["complete_binding_correct"] += 1
                    elif flags["complete_binding_available"]:
                        counts_select["complete_binding_available_but_selected_wrong"] += 1
                    else:
                        counts_select["complete_binding_unavailable_in_public_pool"] += 1
                        counts_missing["+".join(role for role in (*ROLES, "entity") if not flags[role])] += 1
                    period_errors[period_error(binding["period"], references[ident]["period"], flags["period"])] += 1
        selection[method] = {"policy": "public_information_prefix", "automatic_feedback_cases": 10,
                             "seed_target_evaluations": 560 * len(SEEDS), "counts": dict(counts_select),
                             "unavailable_component_combinations": dict(counts_missing), "period_error_categories": dict(period_errors)}
    snapshot = DEST / "diagnoser_snapshot.py"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "unique_development_targets": 560, "company_identifiers": companies, "seeds": list(SEEDS),
        "public_candidate_availability_counts_not_accuracies": dict(counts),
        "availability_per_target": available, "geometry_effects_conditioned_on_other_components": comparisons,
        "primary_policy_10_feedback_selection_and_period_errors": selection,
        "reference_period_literal_formats": dict(reference_formats), "candidate_period_literal_formats": dict(candidate_formats),
        "period_diagnostic_uses_frozen_source_lexical_metric_without_changing_scores": True,
        "source_year_metadata_not_assumed_to_be_the_fact_reporting_period": True,
        "all_1512_prediction_hashes_validated_before_reference_access": True,
        "predictions_or_training_inputs_modified": False,
        "prediction_manifest_sha256": sha(manifest_file), "scores_sha256": sha(scores_file),
        "public_inventory_sha256": sha(inventory_file), "reference_and_source_sha256": hashes,
        "diagnoser_snapshot_sha256": sha(snapshot),
        "post_hoc_reused_development_diagnosis_not_independent_advantage_evidence": True,
        "new_independent_or_calibration_sources_or_answers_opened": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Post-freeze development selection diagnosis complete", dict(counts), flush=True)


if __name__ == "__main__":
    run()

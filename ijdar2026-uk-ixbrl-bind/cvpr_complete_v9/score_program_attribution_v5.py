"""Full-matrix exploratory attribution; these are not independent test claims."""

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from prepare import save, sha
from program_attribution_v5 import COMMON, DEST, METHODS, NEW_METHODS, SEEDS
from score_program_scope_v3 import equal_quantity, registered_prediction


def main():
    manifest = json.loads((DEST / "inference_manifest.json").read_text())
    prediction_file = DEST / "predictions.json"
    task_file = COMMON / "public/tasks.json"
    design_file = DEST / "design.json"
    if (
        not manifest["complete"]
        or sha(prediction_file) != manifest["predictions_sha256"]
        or sha(design_file) != manifest["design_sha256"]
        or sha(task_file) != manifest["public_tasks_sha256"]
    ):
        raise RuntimeError("Frozen complete attribution predictions required")
    design = json.loads(design_file.read_text())
    if design["methods"] != list(METHODS) or design["seeds"] != list(SEEDS):
        raise RuntimeError("Registered attribution conditions changed")
    for path, digest in (
        (DEST / "runner_snapshot.py", design["runner_sha256"]),
        (
            Path(__file__).with_name("program_training_inputs.py"),
            design["shared_input_loader_sha256"],
        ),
        (
            Path(__file__).with_name("program_scope_learning_v3.py"),
            design["shared_training_runner_sha256"],
        ),
        (COMMON / "features/manifest.json", design["feature_manifest_sha256"]),
        (
            COMMON / "program_correction_learning/predictions.json",
            design["frozen_v3_predictions_sha256"],
        ),
    ):
        if sha(path) != digest:
            raise RuntimeError("Frozen attribution provenance changed")
    tasks = {
        t["task_id"]: t
        for t in json.loads(task_file.read_text())
        if t["source_year"] in (2024, 2025)
    }
    expected = {
        (i, m, b, s) for i in tasks for m in METHODS for b in (0, 5, 10) for s in SEEDS
    } | {(i, "common_visual", 0, None) for i in tasks}
    rows = json.loads(prediction_file.read_text())
    indexed = {}
    for row in rows:
        key = row["task_id"], row["method"], row["budget"], row["seed"]
        if key in indexed or key not in expected:
            raise RuntimeError("Duplicate/unregistered attribution matrix entry")
        registered_prediction(row, tasks[row["task_id"]])
        indexed[key] = row
    if set(indexed) != expected or len(rows) != manifest["expected_predictions"]:
        raise RuntimeError("Incomplete attribution matrix")
    # No scoring-only data is opened before checking the complete matrix.
    reference_file = COMMON / "scoring_only/references.json"
    refs = json.loads(reference_file.read_text())
    scores = {
        k: int(
            equal_quantity(
                r["prediction"]["forced_answer"], refs[k[0]]["canonical_value"]
            )
        )
        for k, r in indexed.items()
    }
    summaries = []
    per_task = []
    for method, budget, seeds in [("common_visual", 0, (None,))] + [
        (m, b, SEEDS) for m in METHODS for b in (0, 5, 10)
    ]:
        runs = []
        for seed in seeds:
            by_company = defaultdict(Counter)
            count = answered = 0
            for ident, task in tasks.items():
                key = ident, method, budget, seed
                right = scores[key]
                present = indexed[key]["prediction"]["forced_answer"] is not None
                count += right
                answered += present
                by_company[task["company"]]["questions"] += 1
                by_company[task["company"]]["correct"] += right
                by_company[task["company"]]["answered"] += present
                per_task.append(
                    {
                        "task_id": ident,
                        "method": method,
                        "budget": budget,
                        "seed": seed,
                        "quantity_correct": bool(right),
                        "answer_missing": not present,
                    }
                )
            runs.append(
                {
                    "seed": seed,
                    "correct": count,
                    "questions": len(tasks),
                    "forced_quantity_accuracy": count / len(tasks),
                    "answered": answered,
                    "wrong_answer": answered - count,
                    "no_answer": len(tasks) - answered,
                    "by_legal_entity": dict(by_company),
                }
            )
        summaries.append(
            {
                "method": method,
                "local_automatic_feedback": budget,
                "mean_forced_quantity_accuracy": float(
                    np.mean([r["forced_quantity_accuracy"] for r in runs])
                ),
                "runs": runs,
            }
        )
    companies = sorted({t["company"] for t in tasks.values()})
    groups = {c: [i for i, t in tasks.items() if t["company"] == c] for c in companies}
    samples = np.random.default_rng(20261014).integers(
        0, len(companies), (10000, len(companies))
    )
    denominator = np.array([len(groups[c]) for c in companies])[samples].sum(1)
    comparisons = []
    for reference in NEW_METHODS:
        for budget in (0, 5, 10):
            differences = {
                i: np.mean(
                    [
                        scores[i, "factor_composition", budget, s]
                        - scores[i, reference, budget, s]
                        for s in SEEDS
                    ]
                )
                for i in tasks
            }
            sums = np.array([sum(differences[i] for i in groups[c]) for c in companies])
            bootstrap = sums[samples].sum(1) / denominator
            comparisons.append(
                {
                    "method": "factor_composition",
                    "reference": reference,
                    "budget": budget,
                    "mean_difference": float(sum(differences.values()) / len(tasks)),
                    "descriptive_legal_entity_bootstrap_95ci": np.quantile(
                        bootstrap, (0.025, 0.975)
                    ).tolist(),
                    "not_independent_hypothesis_test": True,
                }
            )
    feedback_effects = []
    for method in METHODS:
        for budget in (5, 10):
            seed_changes = []
            for seed in SEEDS:
                improved = sum(
                    scores[i, method, 0, seed] == 0
                    and scores[i, method, budget, seed] == 1
                    for i in tasks
                )
                harmed = sum(
                    scores[i, method, 0, seed] == 1
                    and scores[i, method, budget, seed] == 0
                    for i in tasks
                )
                seed_changes.append(
                    {
                        "seed": seed,
                        "incorrect_to_correct": improved,
                        "correct_to_incorrect": harmed,
                    }
                )
            feedback_effects.append(
                {
                    "method": method,
                    "budget": budget,
                    "paired_against_local_zero": seed_changes,
                }
            )
    result = {
        "exploratory_development_attribution": True,
        "future_development_targets": len(tasks),
        "legal_entity_identifiers": len(companies),
        "predictions": len(rows),
        "candidate_only_ablation_still_has_common_visual_base_prior_and_OCR_candidate_features": True,
        "crop_ablation_retains_common_perception_text_and_visual_role_proposals": True,
        "shared_other_legal_entity_2023_pretraining_for_every_trainable_condition": True,
        "local_zero_does_not_mean_zero_total_training_labels": True,
        "new_mechanism_advantage_established": False,
        "full_scope_ownership_or_independent_native_QA_certified": False,
        "exclusive_ontology_capability_claimed": False,
        "human_confirmations": 0,
        "summaries": summaries,
        "comparisons": comparisons,
        "feedback_effects": feedback_effects,
        "training_design": design,
        "candidate_ceiling": {
            "correct": sum(
                any(
                    equal_quantity(c["computed_answer"], refs[i]["canonical_value"])
                    for c in t["candidates"]
                )
                for i, t in tasks.items()
            ),
            "questions": len(tasks),
        },
        "all_empty_candidate_and_observation_failure_records_retained": True,
        "bootstrap_samples": 10000,
        "bootstrap_seed": 20261014,
        "hashes": {
            "predictions": sha(prediction_file),
            "design": sha(design_file),
            "tasks": sha(task_file),
            "references": sha(reference_file),
            "scorer": sha(__file__),
        },
    }
    snapshot = DEST / "scorer_snapshot.py"
    if snapshot.exists() and sha(snapshot) != sha(__file__):
        raise RuntimeError("Frozen attribution scoring implementation changed")
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "development_results.json", result)
    save(DEST / "per_task_scores.json", per_task)
    for row in summaries:
        print(
            row["method"],
            row["local_automatic_feedback"],
            round(100 * row["mean_forced_quantity_accuracy"], 2),
            flush=True,
        )


if __name__ == "__main__":
    main()

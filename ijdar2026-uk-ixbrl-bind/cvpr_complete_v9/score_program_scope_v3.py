"""Score the complete frozen assisted program-correction development matrix.

Local 0/5/10 denotes company-specific automatic feedback, not zero total
training labels. Fourteen legal entities are clusters; publisher/template-family
independence and complete visible ownership have not been certified.
"""

import itertools
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import numpy as np
from crop_candidates_v3 import DEST
from prepare import save, sha
from program_scope_learning_v3 import METHODS, SEEDS
from scipy.stats import binomtest

BUDGETS = (0, 5, 10)


def equal_quantity(value, reference):
    if value is None:
        return False
    predicted, expected = Decimal(str(value)), Decimal(str(reference))
    return predicted.is_finite() and expected.is_finite() and predicted == expected


def registered_prediction(row, task):
    prediction = row["prediction"]
    candidate = prediction["candidate_index"]
    answer = prediction["forced_answer"]
    if candidate is None:
        if answer is not None:
            raise ValueError("Null program cannot supply an answer")
    elif (
        type(candidate) is not int
        or not 0 <= candidate < len(task["candidates"])
        or answer != task["candidates"][candidate]["computed_answer"]
    ):
        raise ValueError("Unregistered candidate/quantity pair")
    if row["company"] != task["company"]:
        raise ValueError("Prediction legal entity differs")
    probabilities = prediction.get("probabilities")
    if row["method"] != "common_visual":
        vector = np.asarray(probabilities, dtype=float)
        if (
            vector.shape != (len(task["candidates"]) + 1,)
            or not np.isfinite(vector).all()
            or (vector < 0).any()
            or abs(vector.sum() - 1) > 1e-5
        ):
            raise ValueError("Invalid registered probability vector")
        selected = int(vector.argmax())
        if candidate != (selected if selected < len(task["candidates"]) else None):
            raise ValueError("Candidate differs from frozen argmax")


def validate_matrix(tasks, rows):
    indexed = {}
    expected = {
        (ident, method, budget, seed)
        for ident in tasks
        for method in METHODS
        for budget in BUDGETS
        for seed in SEEDS
    } | {(ident, "common_visual", 0, None) for ident in tasks}
    for row in rows:
        key = row["task_id"], row["method"], row["budget"], row["seed"]
        if key not in expected or key in indexed:
            raise ValueError("Duplicate or unregistered matrix entry")
        registered_prediction(row, tasks[row["task_id"]])
        indexed[key] = row
    if set(indexed) != expected:
        raise ValueError("Incomplete future prediction matrix")
    return indexed


def holm(pvalues):
    order = np.argsort(pvalues, kind="stable")
    adjusted = [0.0] * len(pvalues)
    previous = 0.0
    for rank, position in enumerate(order):
        previous = max(previous, min(1.0, pvalues[position] * (len(pvalues) - rank)))
        adjusted[position] = previous
    return adjusted


def main():
    dest = DEST / "program_correction_learning"
    manifest_file = dest / "inference_manifest.json"
    manifest = json.loads(manifest_file.read_text())
    prediction_file = dest / "predictions.json"
    task_file = DEST / "public/tasks.json"
    design_file = dest / "design.json"
    if (
        not manifest["complete"]
        or sha(prediction_file) != manifest["predictions_sha256"]
        or sha(task_file) != manifest["public_tasks_sha256"]
        or sha(design_file) != manifest["design_sha256"]
    ):
        raise RuntimeError("Frozen complete inputs/design/predictions required")
    design = json.loads(design_file.read_text())
    if design["methods"] != list(METHODS) or design["seeds"] != list(SEEDS):
        raise RuntimeError("Registered comparison conditions changed")
    for path, digest in (
        (DEST / "features/manifest.json", design["feature_manifest_sha256"]),
        (DEST / "training_only/past_feedback.json", design["past_feedback_sha256"]),
        (DEST / "feedback_prefixes.json", design["feedback_prefix_sha256"]),
        (dest / "runner_snapshot.py", design["runner_sha256"]),
    ):
        if sha(path) != digest:
            raise RuntimeError("Frozen training input or runner snapshot differs")
    all_tasks = json.loads(task_file.read_text())
    tasks = {t["task_id"]: t for t in all_tasks if t["source_year"] in (2024, 2025)}
    rows = json.loads(prediction_file.read_text())
    indexed = validate_matrix(tasks, rows)
    if len(rows) != manifest["expected_predictions"]:
        raise RuntimeError("Frozen matrix size changed")
    # This is the first scoring-only read. Complete matrix checks above never
    # inspect future reference quantities or select favorable runs.
    reference_file = DEST / "scoring_only/references.json"
    references = json.loads(reference_file.read_text())
    if not set(tasks) <= set(references):
        raise RuntimeError("Missing future scoring identities")
    correct = {
        key: int(
            equal_quantity(
                row["prediction"]["forced_answer"],
                references[key[0]]["canonical_value"],
            )
        )
        for key, row in indexed.items()
    }
    conditions = [("common_visual", 0, (None,))] + [
        (method, budget, SEEDS) for method in METHODS for budget in BUDGETS
    ]
    summaries = []
    per_task = []
    for method, budget, seeds in conditions:
        runs = []
        for seed in seeds:
            groups = {name: defaultdict(Counter) for name in ("company", "source_year")}
            count = 0
            missing = 0
            for ident, task in tasks.items():
                key = ident, method, budget, seed
                right = correct[key]
                no_answer = indexed[key]["prediction"]["forced_answer"] is None
                count += right
                missing += no_answer
                for name, group in groups.items():
                    group[str(task[name])]["questions"] += 1
                    group[str(task[name])]["correct"] += right
                per_task.append(
                    {
                        "task_id": ident,
                        "method": method,
                        "budget": budget,
                        "seed": seed,
                        "quantity_correct": bool(right),
                        "no_answer": no_answer,
                    }
                )
            runs.append(
                {
                    "seed": seed,
                    "correct": count,
                    "questions": len(tasks),
                    "forced_quantity_accuracy": count / len(tasks),
                    "no_answer": missing,
                    "by_legal_entity": dict(groups["company"]),
                    "by_source_year": dict(groups["source_year"]),
                }
            )
        summaries.append(
            {
                "method": method,
                "local_automatic_feedback": budget,
                "runs": runs,
                "mean_forced_quantity_accuracy": float(
                    np.mean([r["forced_quantity_accuracy"] for r in runs])
                ),
            }
        )

    companies = sorted({t["company"] for t in tasks.values()})
    groups = {c: [i for i, t in tasks.items() if t["company"] == c] for c in companies}
    rng = np.random.default_rng(20261014)
    samples = rng.integers(0, len(companies), (10000, len(companies)))
    sizes = np.array([len(groups[c]) for c in companies])
    denominator = sizes[samples].sum(1)
    signs = np.asarray(
        list(itertools.product((-1, 1), repeat=len(companies))), dtype=float
    )
    comparisons = []
    for method, reference in (
        ("factor_composition", "generic_joint"),
        ("factor_composition", "learned_semantic_memory"),
        ("generic_joint", "learned_semantic_memory"),
        ("factor_composition", "common_visual"),
        ("generic_joint", "common_visual"),
        ("learned_semantic_memory", "common_visual"),
    ):
        for budget in BUDGETS:
            rb = 0 if reference == "common_visual" else budget
            repetitions = (None,) if reference == "common_visual" else SEEDS
            differences = {
                i: np.mean([correct[i, method, budget, s] for s in SEEDS])
                - np.mean([correct[i, reference, rb, s] for s in repetitions])
                for i in tasks
            }
            sums = np.array([sum(differences[i] for i in groups[c]) for c in companies])
            mean = float(sum(differences.values()) / len(tasks))
            bootstrap = sums[samples].sum(1) / denominator
            observed = abs(float(sums.sum()))
            permutation_p = float(np.mean(np.abs(signs @ sums) >= observed - 1e-10))
            per_seed = []
            for seed in SEEDS:
                rs = None if reference == "common_visual" else seed
                gained = sum(
                    correct[i, method, budget, seed] == 1
                    and correct[i, reference, rb, rs] == 0
                    for i in tasks
                )
                lost = sum(
                    correct[i, method, budget, seed] == 0
                    and correct[i, reference, rb, rs] == 1
                    for i in tasks
                )
                per_seed.append(
                    {
                        "seed": seed,
                        "method_only_correct": gained,
                        "reference_only_correct": lost,
                        "question_level_exact_mcnemar_p_secondary": float(
                            binomtest(gained, gained + lost).pvalue
                        )
                        if gained + lost
                        else 1.0,
                    }
                )
            comparisons.append(
                {
                    "method": method,
                    "reference": reference,
                    "budget": budget,
                    "mean_difference": mean,
                    "legal_entity_cluster_bootstrap_95ci": np.quantile(
                        bootstrap, (0.025, 0.975)
                    ).tolist(),
                    "legal_entity_cluster_sign_flip_p": permutation_p,
                    "paired_per_seed": per_seed,
                    "primary_mechanism_comparison": method == "factor_composition"
                    and reference != "common_visual",
                }
            )
    primary = [c for c in comparisons if c["primary_mechanism_comparison"]]
    adjusted = holm([c["legal_entity_cluster_sign_flip_p"] for c in primary])
    for c, p in zip(primary, adjusted):
        c["holm_adjusted_cluster_p_six_primary_comparisons"] = p
    ceiling = sum(
        any(
            equal_quantity(c["computed_answer"], references[i]["canonical_value"])
            for c in t["candidates"]
        )
        for i, t in tasks.items()
    )
    result = {
        "development_only": True,
        "future_targets": len(tasks),
        "legal_entity_identifiers": len(companies),
        "predictions": len(rows),
        "oracle_field_region_multiplier_given_to_all_controls": True,
        "scope_boxes_and_quantity_roles_are_common_uncertified_hypotheses": True,
        "local_feedback_is_not_total_training_label_budget": True,
        "pretraining_is_other_legal_entity_2023_only": True,
        "human_confirmations": 0,
        "full_native_scope_or_semantic_ownership_certified": False,
        "independent_source_family_or_template_generalization_established": False,
        "candidate_quantity_ceiling": {"correct": ceiling, "questions": len(tasks)},
        "no_empty_candidate_or_common_observation_failure_excluded": True,
        "summaries": summaries,
        "comparisons": comparisons,
        "training_design": design,
        "bootstrap_samples": 10000,
        "bootstrap_seed": 20261014,
        "cluster_sign_flip_permutations": len(signs),
        "source_family_correlations_not_accounted_for_beyond_legal_entity_clusters": True,
        "probabilities_not_calibrated_selective_coverage_not_claimed": True,
        "not_native_QA_public_test_or_SOTA": True,
        "new_mechanism_advantage_established": False,
        "hashes": {
            "predictions": sha(prediction_file),
            "design": sha(design_file),
            "tasks": sha(task_file),
            "references": sha(reference_file),
            "scorer": sha(__file__),
        },
    }
    snapshot = dest / "scorer_snapshot.py"
    if snapshot.exists() and sha(snapshot) != sha(__file__):
        raise RuntimeError("Frozen scoring implementation changed")
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    save(dest / "development_results.json", result)
    save(dest / "per_task_scores.json", per_task)
    for row in summaries:
        print(
            row["method"],
            row["local_automatic_feedback"],
            round(100 * row["mean_forced_quantity_accuracy"], 2),
            flush=True,
        )
    print("Candidate quantity ceiling", ceiling, "/", len(tasks), flush=True)


if __name__ == "__main__":
    main()

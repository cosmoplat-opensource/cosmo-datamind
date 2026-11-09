"""Complete-matrix scoring against capacity-matched and bilinear controls."""

import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from prepare import save, sha
from program_controls_v4 import COMMON, DEST, METHODS, SEEDS
from scipy.stats import binomtest
from score_program_scope_v3 import equal_quantity, holm, registered_prediction


def validate_matrix(tasks, rows):
    expected = {
        (i, m, b, s) for i in tasks for m in METHODS for b in (0, 5, 10) for s in SEEDS
    } | {(i, "common_visual", 0, None) for i in tasks}
    indexed = {}
    for row in rows:
        key = row["task_id"], row["method"], row["budget"], row["seed"]
        if key not in expected or key in indexed:
            raise ValueError("Duplicate or unregistered stronger-control prediction")
        registered_prediction(row, tasks[row["task_id"]])
        indexed[key] = row
    if set(indexed) != expected:
        raise ValueError("Incomplete stronger-control future matrix")
    return indexed


def main():
    manifest = json.loads((DEST / "inference_manifest.json").read_text())
    prediction_file = DEST / "predictions.json"
    task_file = COMMON / "public/tasks.json"
    design_file = DEST / "design.json"
    if (
        not manifest["complete"]
        or sha(prediction_file) != manifest["predictions_sha256"]
        or sha(task_file) != manifest["public_tasks_sha256"]
        or sha(design_file) != manifest["design_sha256"]
    ):
        raise RuntimeError("Frozen complete stronger-control matrix required")
    design = json.loads(design_file.read_text())
    if design["methods"] != list(METHODS) or design["seeds"] != list(SEEDS):
        raise RuntimeError("Registered stronger-control conditions changed")
    for path, digest in (
        (COMMON / "features/manifest.json", design["feature_manifest_sha256"]),
        (COMMON / "training_only/past_feedback.json", design["past_feedback_sha256"]),
        (COMMON / "feedback_prefixes.json", design["feedback_prefix_sha256"]),
        (DEST / "runner_snapshot.py", design["runner_sha256"]),
        (
            Path(__file__).with_name("program_scope_learning_v3.py"),
            design["shared_training_runner_sha256"],
        ),
        (
            COMMON / "program_correction_learning/predictions.json",
            design["frozen_v3_predictions_sha256"],
        ),
    ):
        if sha(path) != digest:
            raise RuntimeError("Frozen stronger-control provenance changed")
    tasks = {
        t["task_id"]: t
        for t in json.loads(task_file.read_text())
        if t["source_year"] in (2024, 2025)
    }
    rows = json.loads(prediction_file.read_text())
    indexed = validate_matrix(tasks, rows)
    if len(rows) != manifest["expected_predictions"]:
        raise RuntimeError("Frozen stronger-control matrix length differs")
    # Only now read future development quantities; no condition is dropped.
    ref_file = COMMON / "scoring_only/references.json"
    refs = json.loads(ref_file.read_text())
    scores = {
        k: int(
            equal_quantity(
                r["prediction"]["forced_answer"], refs[k[0]]["canonical_value"]
            )
        )
        for k, r in indexed.items()
    }
    conditions = [("common_visual", 0, (None,))] + [
        (m, b, SEEDS) for m in METHODS for b in (0, 5, 10)
    ]
    summaries = []
    for method, budget, seeds in conditions:
        runs = []
        for seed in seeds:
            by_company = defaultdict(Counter)
            by_year = defaultdict(Counter)
            correct = missing = 0
            for ident, task in tasks.items():
                key = ident, method, budget, seed
                right = scores[key]
                correct += right
                missing += indexed[key]["prediction"]["forced_answer"] is None
                for group, value in (
                    (by_company, task["company"]),
                    (by_year, str(task["source_year"])),
                ):
                    group[value]["questions"] += 1
                    group[value]["correct"] += right
            runs.append(
                {
                    "seed": seed,
                    "correct": correct,
                    "questions": len(tasks),
                    "forced_quantity_accuracy": correct / len(tasks),
                    "no_answer": missing,
                    "by_legal_entity": dict(by_company),
                    "by_source_year": dict(by_year),
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
    sizes = np.array([len(groups[c]) for c in companies])
    samples = np.random.default_rng(20261014).integers(
        0, len(companies), (10000, len(companies))
    )
    signs = np.array(list(itertools.product((-1, 1), repeat=len(companies))))
    comparisons = []
    for reference in (
        "capacity_matched_joint",
        "generic_bilinear_joint",
        "learned_semantic_memory",
        "additive_composition",
        "common_visual",
    ):
        for budget in (0, 5, 10):
            rb, rseeds = (
                (0, (None,)) if reference == "common_visual" else (budget, SEEDS)
            )
            differences = {
                i: np.mean([scores[i, "factor_composition", budget, s] for s in SEEDS])
                - np.mean([scores[i, reference, rb, s] for s in rseeds])
                for i in tasks
            }
            sums = np.array([sum(differences[i] for i in groups[c]) for c in companies])
            bootstrap = sums[samples].sum(1) / sizes[samples].sum(1)
            primary = reference in (
                "capacity_matched_joint",
                "generic_bilinear_joint",
                "learned_semantic_memory",
            )
            paired = []
            for seed in SEEDS:
                rs = None if reference == "common_visual" else seed
                gained = sum(
                    scores[i, "factor_composition", budget, seed] == 1
                    and scores[i, reference, rb, rs] == 0
                    for i in tasks
                )
                lost = sum(
                    scores[i, "factor_composition", budget, seed] == 0
                    and scores[i, reference, rb, rs] == 1
                    for i in tasks
                )
                paired.append(
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
                    "method": "factor_composition",
                    "reference": reference,
                    "budget": budget,
                    "mean_difference": float(sum(differences.values()) / len(tasks)),
                    "legal_entity_cluster_bootstrap_95ci": np.quantile(
                        bootstrap, (0.025, 0.975)
                    ).tolist(),
                    "legal_entity_cluster_sign_flip_p": float(
                        np.mean(np.abs(signs @ sums) >= abs(sums.sum()) - 1e-10)
                    ),
                    "paired_per_seed": paired,
                    "primary_mechanism_comparison": primary,
                }
            )
    primary = [c for c in comparisons if c["primary_mechanism_comparison"]]
    for c, adjusted in zip(
        primary, holm([c["legal_entity_cluster_sign_flip_p"] for c in primary])
    ):
        c["holm_adjusted_cluster_p_nine_primary_comparisons"] = adjusted
    result = {
        "development_only": True,
        "future_targets": len(tasks),
        "legal_entities": len(companies),
        "predictions": len(rows),
        "oracle_field_region_multiplier_given_to_all_controls": True,
        "full_visible_scope_ownership_certified": False,
        "independent_source_family_or_template_generalization_established": False,
        "human_confirmations": 0,
        "local_feedback_is_not_total_training_label_budget": True,
        "shared_global_other_company_2023_pretraining": True,
        "all_previous_and_new_negative_results_retained": True,
        "new_mechanism_advantage_established": False,
        "exclusive_ontology_capability_claimed": False,
        "summaries": summaries,
        "comparisons": comparisons,
        "candidate_quantity_ceiling": {
            "correct": sum(
                any(
                    equal_quantity(c["computed_answer"], refs[i]["canonical_value"])
                    for c in t["candidates"]
                )
                for i, t in tasks.items()
            ),
            "questions": len(tasks),
        },
        "training_design": design,
        "not_native_QA_independent_test_or_SOTA": True,
        "bootstrap_samples": 10000,
        "bootstrap_seed": 20261014,
        "cluster_sign_flip_permutations": len(signs),
        "source_family_correlations_not_accounted_for_beyond_legal_entity_clusters": True,
        "hashes": {
            "predictions": sha(prediction_file),
            "design": sha(design_file),
            "tasks": sha(task_file),
            "references": sha(ref_file),
            "scorer": sha(__file__),
            "shared_validation_scorer": sha(
                Path(__file__).with_name("score_program_scope_v3.py")
            ),
        },
    }
    snapshot = DEST / "scorer_snapshot.py"
    if snapshot.exists() and sha(snapshot) != sha(__file__):
        raise RuntimeError("Frozen stronger-control scoring implementation changed")
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "development_results.json", result)
    for row in summaries:
        print(
            row["method"],
            row["local_automatic_feedback"],
            round(100 * row["mean_forced_quantity_accuracy"], 2),
            flush=True,
        )


if __name__ == "__main__":
    main()

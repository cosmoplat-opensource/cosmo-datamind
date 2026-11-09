"""Post-inference scoring of the complete assisted polarity development matrix."""

from collections import Counter, defaultdict
from decimal import Decimal
import json

import numpy as np
from scipy.stats import binomtest

from canonicalization_prepare import DEST
from canonicalization_train import METHODS, SEEDS
from prepare import save, sha


def exact(value, reference):
    return value is not None and Decimal(str(value)) == Decimal(str(reference))


def main():
    dest = DEST / "training"
    mf = dest / "inference_manifest.json"
    manifest = json.loads(mf.read_text())
    protocol = json.loads((DEST / "protocol.json").read_text())
    tasks_file = DEST / "public/tasks.json"
    pred_file = dest / "predictions.json"
    if not manifest["complete"] or sha(pred_file) != manifest["predictions_sha256"]:
        raise RuntimeError(
            "Frozen complete predictions required before opening references"
        )
    if (
        sha(tasks_file) != manifest["public_tasks_sha256"]
        or sha(dest / "design.json") != manifest["design_sha256"]
    ):
        raise RuntimeError("Frozen diagnostic input/design differs")
    tasks = {
        t["task_id"]: t
        for t in json.loads(tasks_file.read_text())
        if t["source_year"] in (2024, 2025)
    }
    rows = json.loads(pred_file.read_text())
    indexed = {}
    for row in rows:
        key = row["task_id"], row["method"], row["budget"], row["seed"]
        if key in indexed or row["task_id"] not in tasks:
            raise RuntimeError("Duplicate or unknown diagnostic prediction")
        indexed[key] = row
    expected = {
        (ident, method, budget, seed)
        for ident in tasks
        for method in METHODS
        for budget in (0, 5, 10)
        for seed in (SEEDS if method == "generic_joint" else (None,))
    }
    if set(indexed) != expected or len(rows) != manifest["expected_predictions"]:
        raise RuntimeError(
            "Incomplete diagnostic condition matrix; cannot selectively score"
        )
    # Reference quantities are opened only after the complete future-prediction
    # matrix, control names and task identity have passed integrity checks.
    reference_file = DEST / "scoring_only/references.json"
    refs = json.loads(reference_file.read_text())
    scores = {}
    summaries = []
    companies = sorted({t["company"] for t in tasks.values()})
    for method in METHODS:
        for budget in (0, 5, 10):
            repetitions = SEEDS if method == "generic_joint" else (None,)
            per_seed = []
            partial = []
            for seed in repetitions:
                correct = 0
                by_company = defaultdict(Counter)
                nulls = 0
                for ident, task in tasks.items():
                    row = indexed[ident, method, budget, seed]
                    prediction = row["prediction"]
                    value = prediction["forced_answer"]
                    index = prediction["candidate_index"]
                    if index is not None and (
                        type(index) is not int
                        or not 0 <= index < len(task["candidates"])
                        or value != task["candidates"][index]["computed_answer"]
                    ):
                        raise RuntimeError("Unregistered computed diagnostic answer")
                    right = exact(value, refs[ident]["canonical_value"])
                    scores[ident, method, budget, seed] = int(right)
                    correct += right
                    nulls += value is None
                    by_company[task["company"]]["questions"] += 1
                    by_company[task["company"]]["correct"] += right
                    partial.append(
                        {
                            "task_id": ident,
                            "method": method,
                            "budget": budget,
                            "seed": seed,
                            "quantity_correct": right,
                            "answer_missing": value is None,
                        }
                    )
                per_seed.append(
                    {
                        "seed": seed,
                        "correct": correct,
                        "questions": len(tasks),
                        "forced_quantity_accuracy": correct / len(tasks),
                        "no_answer": nulls,
                        "by_legal_entity": {c: dict(n) for c, n in by_company.items()},
                    }
                )
            summaries.append(
                {
                    "method": method,
                    "budget": budget,
                    "runs": per_seed,
                    "mean_forced_quantity_accuracy": sum(
                        r["forced_quantity_accuracy"] for r in per_seed
                    )
                    / len(per_seed),
                }
            )
    # This equality is an intentional representation control. Different outputs
    # here are an implementation error, not evidence of an ontology advantage.
    if any(
        indexed[i, "scoped_pooling", b, None]["prediction"]
        != indexed[i, "typed_memory_equivalent", b, None]["prediction"]
        for i in tasks
        for b in (0, 5, 10)
    ):
        raise RuntimeError("Storage-equivalent controls disagree")
    groups = {c: [i for i, t in tasks.items() if t["company"] == c] for c in companies}
    rng = np.random.default_rng(20261013)
    samples = rng.integers(0, len(companies), size=(5000, len(companies)))
    denom = np.array([len(groups[c]) for c in companies])[samples].sum(1)
    comparisons = []
    for budget in (0, 5, 10):
        for method in ("scoped_pooling", "generic_joint"):
            repetitions = SEEDS if method == "generic_joint" else (None,)
            differences = {
                i: sum(scores[i, method, budget, s] for s in repetitions)
                / len(repetitions)
                - scores[i, "semantic_memory", budget, None]
                for i in tasks
            }
            sums = np.array([sum(differences[i] for i in groups[c]) for c in companies])
            bootstrap = sums[samples].sum(1) / denom
            paired = []
            for seed in repetitions:
                gained = sum(
                    scores[i, method, budget, seed] == 1
                    and scores[i, "semantic_memory", budget, None] == 0
                    for i in tasks
                )
                lost = sum(
                    scores[i, method, budget, seed] == 0
                    and scores[i, "semantic_memory", budget, None] == 1
                    for i in tasks
                )
                paired.append(
                    {
                        "seed": seed,
                        "method_only_correct": gained,
                        "memory_only_correct": lost,
                        "question_level_exact_mcnemar_p": float(
                            binomtest(gained, gained + lost).pvalue
                        )
                        if gained + lost
                        else 1.0,
                    }
                )
            comparisons.append(
                {
                    "method": method,
                    "reference": "semantic_memory",
                    "budget": budget,
                    "mean_difference": sum(differences.values()) / len(tasks),
                    "legal_entity_cluster_bootstrap_95ci": np.quantile(
                        bootstrap, [0.025, 0.975]
                    ).tolist(),
                    "paired_per_seed": paired,
                }
            )
    ceiling = sum(
        any(
            exact(c["computed_answer"], refs[i]["canonical_value"])
            for c in t["candidates"]
        )
        for i, t in tasks.items()
    )
    result = {
        "development_only": True,
        "evaluation_tasks": len(tasks),
        "legal_entity_identifiers": len(companies),
        "source_family_independence_not_certified": True,
        "oracle_field_region_and_publisher_scale_given_to_all_controls": True,
        "candidate_quantity_ceiling": {"correct": ceiling, "questions": len(tasks)},
        "no_missing_candidates_excluded": True,
        "summaries": summaries,
        "comparisons": comparisons,
        "storage_equivalent_scoped_and_typed_memory_predictions_identical": True,
        "full_scope_or_semantic_binding_ownership_certified": False,
        "not_native_QA_public_test_or_SOTA": True,
        "human_confirmations": 0,
        "feedback_and_schema_limits": protocol,
        "predictions_sha256": sha(pred_file),
        "references_sha256": sha(reference_file),
        "bootstrap_samples": 5000,
        "bootstrap_seed": 20261013,
        "runner_sha256": sha(__file__),
    }
    save(DEST / "development_results.json", result)
    for row in summaries:
        print(
            row["method"],
            row["budget"],
            round(row["mean_forced_quantity_accuracy"] * 100, 2),
            flush=True,
        )
    print("Common numeric candidate ceiling:", ceiling, "/", len(tasks), flush=True)


if __name__ == "__main__":
    main()

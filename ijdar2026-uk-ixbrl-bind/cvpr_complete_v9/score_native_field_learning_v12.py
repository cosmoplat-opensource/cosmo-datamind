"""Score all field-classification controls after predictions are frozen."""

from collections import defaultdict
from datetime import datetime, timezone
import json
import time

import numpy as np

from native_correspondence_prepare_v10 import DEST as NATIVE
from native_field_learning_v12 import METHODS, POLICIES, SEEDS
from native_field_retrieval_v12 import DEST
from prepare import save, sha


def correct(prediction, reference):
    return prediction is not None and prediction["name"] == reference["field_name"] and prediction["namespace"] == reference["field_namespace"]


def run(wait=False):
    folder = DEST / "learning_v1"
    manifest_file = folder / "manifest.json"
    start = time.monotonic()
    while not manifest_file.exists():
        if not wait or time.monotonic() - start > 7200:
            raise RuntimeError("All native field predictions must complete before reference scoring")
        print("Awaiting all native field learning controls; scoring references remain closed", flush=True)
        time.sleep(30)
    manifest = json.loads(manifest_file.read_text())
    if not manifest["complete"]:
        raise ValueError("Incomplete field learning manifest")
    public = json.loads((DEST / "public/tasks.json").read_text())
    future = {t["task_id"]: t for t in public if t["source_year"] != 2023}
    companies = sorted({t["company"] for t in future.values()})
    # Development references, never the reserved independent test cohort.
    reference_file = NATIVE / "scoring_only/references.json"
    references = json.loads(reference_file.read_text())
    records, outcomes, file_hashes = {}, {}, {}
    for policy in POLICIES:
        for method in METHODS:
            for seed in SEEDS:
                for budget in (0, 5, 10):
                    label = f"{policy}/{method}/{seed}/{budget}"
                    combined, usable, steps = {}, 0, 0
                    for company in companies:
                        file = folder / "predictions" / policy / company / method / str(seed) / f"{budget}.json"
                        data = json.loads(file.read_text())
                        if not data["complete"] or data["design_sha256"] != manifest["design_sha256"]:
                            raise ValueError("Changed or incomplete field configuration")
                        expected = {i for i, t in future.items() if t["company"] == company}
                        if len(data["predictions"]) != len(expected) or {p["task_id"] for p in data["predictions"]} != expected:
                            raise ValueError("Missing/duplicate future field prediction")
                        file_hashes[str(file.relative_to(folder))] = sha(file)
                        combined.update({p["task_id"]: p["field"] for p in data["predictions"]})
                        usable += data["usable_local_feedback"]
                        steps += data["actual_local_gradient_steps"]
                    if set(combined) != set(future):
                        raise ValueError("Field-scoring future denominator changed")
                    rows = {i: correct(p, references[i]) for i, p in combined.items()}
                    selected = sum(v is not None for v in combined.values())
                    hits = sum(rows.values())
                    records[label] = {"correct": hits, "total": len(future), "accuracy": hits/len(future),
                                      "returned_field_coverage": selected/len(future),
                                      "precision_when_returned": hits/selected if selected else 0,
                                      "usable_local_feedback_total": usable, "consumed_local_feedback_total": budget*len(companies),
                                      "actual_local_gradient_steps_total": steps,
                                      "per_company": {c: sum(v for i, v in rows.items() if future[i]["company"] == c) for c in companies}}
                    outcomes[label] = rows
    means, changes = {}, {}
    for policy in POLICIES:
        for method in METHODS:
            for budget in (0, 5, 10):
                values = [records[f"{policy}/{method}/{s}/{budget}"] for s in SEEDS]
                means[f"{policy}/{method}/{budget}"] = {k: float(np.mean([v[k] for v in values]))
                                                        for k in values[0] if k != "per_company"}
                if budget:
                    deltas = []
                    for seed in SEEDS:
                        a = outcomes[f"{policy}/{method}/{seed}/0"]
                        b = outcomes[f"{policy}/{method}/{seed}/{budget}"]
                        deltas.append({"seed": seed, "fixed": sum(not a[i] and b[i] for i in a),
                                       "newly_wrong": sum(a[i] and not b[i] for i in a),
                                       "still_correct": sum(a[i] and b[i] for i in a)})
                    changes[f"{policy}/{method}/{budget}"] = deltas
    rng, paired = np.random.default_rng(20261014), {}
    for policy in POLICIES:
        for budget in (0, 5, 10):
            for baseline in ("generic_joint", "learned_semantic_memory", "frozen_semantic_memory"):
                cluster = defaultdict(list)
                for seed in SEEDS:
                    a = outcomes[f"{policy}/factor_field/{seed}/{budget}"]
                    b = outcomes[f"{policy}/{baseline}/{seed}/{budget}"]
                    for ident in future:
                        cluster[future[ident]["company"]].append(int(a[ident])-int(b[ident]))
                draws = [float(np.mean([v for c in rng.choice(companies, len(companies), replace=True) for v in cluster[c]]))
                         for _ in range(4000)]
                paired[f"{policy}/{budget}/{baseline}"] = {
                    "mean_accuracy_difference": float(np.mean([v for values in cluster.values() for v in values])),
                    "company_cluster_bootstrap_95_ci": np.quantile(draws, [.025, .975]).tolist(),
                    "not_an_independent_source_test_or_multiple_testing_significance_claim": True}
    save(folder / "development_results.json", {
        "complete": True, "future_targets": len(future), "companies": len(companies),
        "per_configuration": records, "seed_means": means, "feedback_error_propagation": changes,
        "paired_comparisons": paired, "prediction_file_sha256": file_hashes,
        "reference_sha256": sha(reference_file), "scorer_sha256": sha(__file__),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "human_runtime_confirmations": 0, "feedback_is_automatic_publisher_annotation": True,
        "full_unit_period_dimension_quantity_or_scope_binding_certified": False,
        "independent_native_test_completed": False, "ontology_unique_advantage_established": False,
    })
    print("Native field classification development scores", json.dumps(means), flush=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", action="store_true")
    args = parser.parse_args()
    run(args.wait)

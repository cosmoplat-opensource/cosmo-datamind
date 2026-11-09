"""Score replay development after every prediction is frozen."""

from collections import defaultdict
from datetime import datetime, timezone
import json

import numpy as np

from native_field_replay_v15 import DEST, SOURCE, NATIVE, METHODS, POLICIES, SEEDS
from prepare import save, sha


def run():
    manifest = json.loads((DEST / "manifest.json").read_text())
    design_file = DEST / "design.json"
    if not manifest["complete"] or manifest["design_sha256"] != sha(design_file):
        raise ValueError("All frozen replay predictions required before scoring")
    design = json.loads(design_file.read_text())
    public_file = SOURCE / "public/tasks.json"
    if sha(public_file) != design["public_tasks_sha256"]:
        raise ValueError("Public replay inventory changed")
    future = {t["task_id"]: t for t in json.loads(public_file.read_text()) if t["source_year"] != 2023}
    companies = sorted({t["company"] for t in future.values()})
    # Already evaluated development cohort; no reserved independent test data.
    reference_file = NATIVE / "scoring_only/references.json"
    references = json.loads(reference_file.read_text())
    outcomes, records, hashes = {}, {}, {}
    for policy in POLICIES:
        for method in METHODS:
            for seed in SEEDS:
                for budget in (0, 5, 10):
                    label = f"{policy}/{method}/{seed}/{budget}"
                    combined, local_exp, history_exp, steps = {}, 0, 0, 0
                    for company in companies:
                        file = DEST / "predictions" / policy / company / method / str(seed) / f"{budget}.json"
                        body = json.loads(file.read_text())
                        if not body["complete"] or body["design_sha256"] != manifest["design_sha256"]:
                            raise ValueError("Incomplete replay configuration")
                        expected = {i for i, t in future.items() if t["company"] == company}
                        rows = body["predictions"]
                        if len(rows) != len(expected) or {r["task_id"] for r in rows} != expected:
                            raise ValueError("Replay prediction inventory changed")
                        if "reused_v12_prediction_file" in body:
                            original_file = body["reused_v12_prediction_file"]
                            if sha(original_file) != body["reused_v12_prediction_sha256"]:
                                raise ValueError("Original comparison prediction changed")
                            if rows != json.loads(open(original_file).read())["predictions"]:
                                raise ValueError("Reused comparison predictions are not identical")
                        if "local_checkpoint" in body and sha(body["local_checkpoint"]) != body["local_checkpoint_sha256"]:
                            raise ValueError("Replay checkpoint changed")
                        hashes[str(file.relative_to(DEST))] = sha(file)
                        combined.update({r["task_id"]: r["field"] for r in rows})
                        local_exp += body["replay_exposures"]["local_example_exposures"]
                        history_exp += body["replay_exposures"]["historical_example_exposures"]
                        steps += body["actual_local_gradient_steps"]
                    if set(combined) != set(future):
                        raise ValueError("Replay scoring denominator changed")
                    correct = {i: p is not None and p["name"] == references[i]["field_name"]
                               and p["namespace"] == references[i]["field_namespace"] for i, p in combined.items()}
                    outcomes[label] = correct
                    records[label] = {"correct": sum(correct.values()), "total": len(future),
                                      "accuracy": sum(correct.values())/len(future),
                                      "actual_local_gradient_steps": steps,
                                      "replay_local_example_exposures": local_exp,
                                      "replay_historical_example_exposures": history_exp,
                                      "per_company": {c: sum(v for i, v in correct.items() if future[i]["company"] == c) for c in companies}}
    means, changes, paired = {}, {}, {}
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
                                       "newly_wrong": sum(a[i] and not b[i] for i in a)})
                    changes[f"{policy}/{method}/{budget}"] = deltas
        for budget in (0, 5, 10):
            for focal, comparator in (("replay_joint", "legacy_joint"), ("replay_factor", "legacy_factor"),
                                       ("replay_factor", "replay_joint"), ("replay_factor", "frozen_semantic_memory"),
                                       ("replay_factor", "public_retrieval"), ("replay_factor", "public_soft_prior")):
                cluster = defaultdict(list)
                for seed in SEEDS:
                    a = outcomes[f"{policy}/{focal}/{seed}/{budget}"]
                    b = outcomes[f"{policy}/{comparator}/{seed}/{budget}"]
                    for ident in future:
                        cluster[future[ident]["company"]].append(int(a[ident])-int(b[ident]))
                totals = np.array([sum(cluster[c]) for c in companies])
                sizes = np.array([len(cluster[c]) for c in companies])
                rng = np.random.default_rng(20261015)
                sample = rng.integers(0, len(companies), size=(4000, len(companies)))
                draws = totals[sample].sum(1)/sizes[sample].sum(1)
                paired[f"{policy}/{budget}/{focal}_vs_{comparator}"] = {
                    "mean_accuracy_difference": float(totals.sum()/sizes.sum()),
                    "company_cluster_bootstrap_95_ci": np.quantile(draws, [.025, .975]).tolist(),
                    "not_independent_test_or_multiple_testing_significance_claim": True}
    save(DEST / "development_results.json", {
        "complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "future_targets": len(future), "companies": len(companies), "per_configuration": records,
        "seed_means": means, "feedback_error_propagation": changes, "paired_comparisons": paired,
        "prediction_file_sha256": hashes, "reference_sha256": sha(reference_file), "scorer_sha256": sha(__file__),
        "primary_denominator_preserves_all_560_future_targets": True,
        "reused_control_predictions_are_identical_to_v12": True,
        "feedback_is_automatic_not_human": True, "human_runtime_confirmations": 0,
        "field_channel_only_not_full_binding": True, "independent_native_test_completed": False,
        "ontology_unique_advantage_established": False,
    })
    print("Replay study development scores", json.dumps({k: round(v["accuracy"]*100, 2) for k, v in means.items()}), flush=True)


if __name__ == "__main__":
    run()

"""Compare every frozen joint control; references stay out of fitting/selection."""

from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path

import numpy as np

from native_binding_candidates_v18 import DEST
from native_joint_learning_v18 import FOLDER,METHODS,POLICIES
from native_joint_features_v18 import SEEDS
from score_native_binding_v17 import scoring_references,component_correct
from prepare import save,sha


def run():
    output = FOLDER/"development_results.json"
    if output.exists():
        raise RuntimeError("Joint development score inventory already frozen")
    manifest_file = FOLDER/"manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if not manifest["complete"] or manifest["prediction_files"] != 1512 or sha(FOLDER/"design.json") != manifest["design_sha256"]:
        raise ValueError("Every joint prediction must freeze before reference reads")
    feature_meta = json.loads((DEST/"features/manifest.json").read_text())
    public_file = DEST/"candidates/tasks.json"
    if sha(public_file) != feature_meta["public_candidate_sha256"]:
        raise ValueError("Frozen public target inventory changed")
    future = {t["task_id"]:t for t in json.loads(public_file.read_text()) if t["source_year"] != 2023}
    companies = sorted({t["company"] for t in future.values()})
    if len(future) != 560 or len(companies) != 14:
        raise ValueError("Full future development denominator required")
    data = {}
    for name,digest in manifest["predictions_sha256"].items():
        path = FOLDER/name
        if sha(path) != digest:
            raise ValueError("Frozen joint prediction changed")
        r = json.loads(path.read_text())
        expected = {ident for ident,t in future.items() if t["company"] == r["company"]}
        if r["design_sha256"] != manifest["design_sha256"] or len(r["predictions"]) != len(expected) or {p["task_id"] for p in r["predictions"]} != expected:
            raise ValueError("Missing/duplicate or changed joint configuration")
        key = (r["policy"],r["method"],r["seed"],r["automatic_feedback_cases"])
        if r["company"] in data.setdefault(key,{}):
            raise ValueError("Duplicate joint company configuration")
        data[key][r["company"]] = r
    # Only after all 1512 files and their complete target lists are verified.
    references,reference_hashes = scoring_references(future)
    results,outcomes,means,changes = {},{},{},{}
    for policy in POLICIES:
        for method in METHODS:
            for seed in SEEDS:
                for budget in (0,5,10):
                    key = (policy,method,seed,budget)
                    if set(data[key]) != set(companies):
                        raise ValueError("Joint configuration is missing a company")
                    predictions = {p["task_id"]:p for r in data[key].values() for p in r["predictions"]}
                    if set(predictions) != set(future):
                        raise ValueError("Joint scorer denominator changed")
                    rows = {ident:component_correct(p["predicted_binding"],references[ident]) for ident,p in predictions.items()}
                    label = f"{policy}/{method}/{seed}/{budget}"
                    outcomes[label] = {ident:r["semantic_tuple_exact"] for ident,r in rows.items()}
                    zero = [ident for ident,p in predictions.items() if p.get("quantity_is_uncertain_dash_zero_hypothesis")]
                    results[label] = {"correct":sum(outcomes[label].values()),"total":560,
                        "semantic_tuple_exact_accuracy":sum(outcomes[label].values())/560,
                        "component_accuracy":{c:sum(r[c] for r in rows.values())/560 for c in next(iter(rows.values())) if c != "semantic_tuple_exact"},
                        "complete_tuple_returned_coverage":sum(p["semantic_tuple_complete"] for p in predictions.values())/560,
                        "dash_zero_hypotheses_returned":len(zero),"incorrect_dash_zero_quantity":sum(not rows[i]["quantity"] for i in zero),
                        "actual_local_optimizer_steps":sum(r["exposure"]["optimizer_steps"] for r in data[key].values()),
                        "per_company":{c:sum(v for i,v in outcomes[label].items() if future[i]["company"] == c) for c in companies}}
    for policy in POLICIES:
        for method in METHODS:
            for budget in (0,5,10):
                values = [results[f"{policy}/{method}/{s}/{budget}"] for s in SEEDS]
                means[f"{policy}/{method}/{budget}"] = {k:float(np.mean([v[k] for v in values])) for k in values[0] if k not in {"per_company","component_accuracy"}}
                means[f"{policy}/{method}/{budget}"]["component_accuracy"] = {c:float(np.mean([v["component_accuracy"][c] for v in values])) for c in values[0]["component_accuracy"]}
                if budget:
                    changes[f"{policy}/{method}/{budget}"] = [{"seed":seed,
                        "fixed":sum(not outcomes[f"{policy}/{method}/{seed}/0"][i] and outcomes[f"{policy}/{method}/{seed}/{budget}"][i] for i in future),
                        "newly_wrong":sum(outcomes[f"{policy}/{method}/{seed}/0"][i] and not outcomes[f"{policy}/{method}/{seed}/{budget}"][i] for i in future)} for seed in SEEDS]
    rng,paired = np.random.default_rng(20261015),{}
    for policy in POLICIES:
        for budget in (0,5,10):
            for baseline in ("joint_prior","dense_joint","joint_memory","frozen_joint_memory","coupled_no_pair"):
                grouped = defaultdict(list)
                for seed in SEEDS:
                    a,b = outcomes[f"{policy}/coupled_joint/{seed}/{budget}"],outcomes[f"{policy}/{baseline}/{seed}/{budget}"]
                    for ident in future:
                        grouped[future[ident]["company"]].append(int(a[ident])-int(b[ident]))
                values = np.array([np.mean(grouped[c]) for c in companies])
                draws = values[rng.integers(0,len(companies),size=(4000,len(companies)))].mean(1)
                paired[f"{policy}/{budget}/{baseline}"] = {"mean_semantic_tuple_accuracy_difference":float(values.mean()),
                    "company_identifier_cluster_bootstrap_95_ci":np.quantile(draws,[.025,.975]).tolist(),
                    "development_only_no_multiple_testing_corrected_or_independent_test_claim":True}
    snapshot = FOLDER/"scorer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output,{"complete":True,"completed_utc":datetime.now(timezone.utc).isoformat(),"unique_future_targets":560,"company_identifiers":14,
        "per_configuration":results,"seed_means":means,"paired_comparisons":paired,"feedback_error_propagation":changes,
        "prediction_manifest_sha256":sha(manifest_file),"scoring_reference_and_source_sha256":reference_hashes,"scorer_snapshot_sha256":sha(snapshot),
        "all_methods_selected_whole_states_from_the_same_beam":True,"numeric_candidate_or_heuristic_gains_are_common_not_ontology_attribution":True,
        "coupling_ablation_is_weight_tied_inference_only":True,"entity_is_common_report_metadata_assistance":True,
        "human_runtime_confirmations":0,"semantic_match_does_not_certify_visual_header_ownership":True,
        "independent_native_test_completed":False,"ontology_unique_advantage_established":False})
    print("Complete joint-state development scoring finished",flush=True)


if __name__ == "__main__":
    run()

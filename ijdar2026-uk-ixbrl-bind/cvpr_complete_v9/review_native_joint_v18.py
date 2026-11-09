"""Verify completed v18 controls and provenance without reading score labels."""

import json
from datetime import datetime, timezone

from native_binding_candidates_v18 import DEST
from native_joint_features_v18 import SEEDS
from native_joint_learning_v18 import FOLDER, METHODS, POLICIES
from prepare import save, sha


def same_predictions(records):
    """Compare predictions, retaining uncertainty flags and output order."""
    values = [row["predictions"] for row in records]
    return bool(values) and all(value == values[0] for value in values[1:])


def review():
    manifest_file = FOLDER / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    design_file = FOLDER / "design.json"
    design = json.loads(design_file.read_text())
    if not manifest["complete"] or sha(design_file) != manifest["design_sha256"]:
        raise ValueError("Completed frozen prediction manifest required")
    if sha(FOLDER / "runner_snapshot.py") != design["runner_sha256"]:
        raise ValueError("Joint training snapshot differs from frozen design")
    feature_file = DEST / "features/manifest.json"
    features = json.loads(feature_file.read_text())
    if sha(feature_file) != design["candidate_feature_manifest_sha256"]:
        raise ValueError("Shared feature freeze changed")
    for name, digest in features["files"].items():
        if sha(DEST / "features" / name) != digest:
            raise ValueError("Shared feature artifact changed")
    prefixes_file = FOLDER / "feedback_prefixes.json"
    if sha(prefixes_file) != design["feedback_prefix_sha256"]:
        raise ValueError("Automatic feedback prefixes changed")
    prefixes = json.loads(prefixes_file.read_text())
    tasks = json.loads((DEST / "candidates/tasks.json").read_text())
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    companies = sorted({t["company"] for t in future.values()})
    expected_count = len(companies) * len(SEEDS) * len(METHODS) * len(POLICIES) * 3
    if len(future) != 560 or len(companies) != 14 or manifest["prediction_files"] != expected_count:
        raise ValueError("Complete common development denominator required")
    data = {}
    for name, digest in manifest["predictions_sha256"].items():
        path = FOLDER / name
        if sha(path) != digest:
            raise ValueError("Prediction artifact changed")
        row = json.loads(path.read_text())
        key = (row["policy"], row["company"], row["method"], row["seed"], row["automatic_feedback_cases"])
        if key in data or row["design_sha256"] != manifest["design_sha256"]:
            raise ValueError("Duplicate or changed prediction configuration")
        expected_ids = {i for i, t in future.items() if t["company"] == row["company"]}
        got = row["predictions"]
        if len(got) != len(expected_ids) or {p["task_id"] for p in got} != expected_ids:
            raise ValueError("Missing or duplicate future target")
        if row["future_reference_files_read"] or not row["whole_joint_state_was_selected"]:
            raise ValueError("Reference boundary or whole-state inference changed")
        if any(p["visual_header_ownership_certified"] or p["scope_assignment"] != "unknown" or not p["entity_from_common_report_metadata_not_learned_visual_identity"] for p in got):
            raise ValueError("Uncertified scope or source-assisted entity was misrepresented")
        if row["method"] in {"joint_prior", "frozen_joint_memory"} or not row["automatic_feedback_cases"]:
            if any(row["exposure"].values()):
                raise ValueError("An untrained or frozen control received local optimizer steps")
        elif row["exposure"]["optimizer_steps"] not in {0, design["maximum_local_optimizer_steps"]}:
            raise ValueError("Local optimizer budget changed")
        data[key] = row
    if len(data) != expected_count:
        raise ValueError("Prediction configuration inventory incomplete")
    parent_files, local_files = [], []
    for company in companies:
        for policy in POLICIES:
            budgets = prefixes[policy][company]
            if budgets["0"] or budgets["5"] != budgets["10"][:5] or len(budgets["10"]) != 10:
                raise ValueError("Feedback prefixes are not nested 0/5/10")
        for seed in SEEDS:
            for method in METHODS:
                if not same_predictions([data[(p, company, method, seed, 0)] for p in POLICIES]):
                    raise ValueError("Zero-feedback predictions depend on the selection policy")
            for policy in POLICIES:
                if not same_predictions([data[(policy, company, "joint_prior", seed, b)] for b in (0, 5, 10)]):
                    raise ValueError("Untrained joint prior changed with feedback budget")
            for method in ("dense_joint", "coupled_joint", "joint_memory"):
                folder = FOLDER / "parents" / company / str(seed) / method
                meta_file = folder / "metadata.json"
                meta = json.loads(meta_file.read_text())
                if meta["excluded_company"] != company or meta["design_sha256"] != sha(design_file) or meta["weights_sha256"] != sha(folder / "weights.pt"):
                    raise ValueError("Parent exclusion or weights provenance changed")
                if meta["exposure"]["optimizer_steps"] != design["pretrain_optimizer_steps"]:
                    raise ValueError("Parent optimizer budget differs")
                parent_files.append(meta_file)
                for policy in POLICIES:
                    for budget in (5, 10):
                        weight = FOLDER / "local" / policy / company / method / str(seed) / (str(budget) + ".pt")
                        local_file = weight.with_suffix(".json")
                        local = json.loads(local_file.read_text())
                        if local["design_sha256"] != sha(design_file) or local["weights_sha256"] != sha(weight):
                            raise ValueError("Local checkpoint provenance changed")
                        if local["exposure"] != data[(policy, company, method, seed, budget)]["exposure"]:
                            raise ValueError("Prediction exposure differs from local checkpoint")
                        if method == "coupled_joint" and local["exposure"] != data[(policy, company, "coupled_no_pair", seed, budget)]["exposure"]:
                            raise ValueError("Weight-tied coupling ablation exposure differs")
                        local_files.append(local_file)
    result = {
        "complete": True,
        "reviewed_utc": datetime.now(timezone.utc).isoformat(),
        "prediction_configurations_checked": len(data),
        "unique_future_development_targets": len(future),
        "parent_checkpoints_checked": len(parent_files),
        "local_checkpoints_checked": len(local_files),
        "zero_feedback_policy_invariance": True,
        "static_prior_feedback_invariance": True,
        "nested_automatic_feedback_prefixes": True,
        "scope_stays_unknown_and_entity_metadata_assistance_disclosed": True,
        "future_or_independent_score_reference_files_read_by_review": False,
        "prediction_manifest_sha256": sha(manifest_file),
        "reviewer_sha256": sha(__file__),
        "all_original_cvpr_experiments_completed": False,
        "ontology_unique_advantage_established_by_this_review": False,
    }
    save(DEST / "control_integrity_review.json", result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    review()

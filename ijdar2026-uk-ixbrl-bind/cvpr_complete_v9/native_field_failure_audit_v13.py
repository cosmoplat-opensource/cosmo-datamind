"""Post-score development audit of retrieval ceilings and ranking errors.

This audit neither trains a model nor changes a prediction or denominator.
Publisher field labels cannot certify which visual reading was incorrect.
Reserved independent-source references are never read.
"""

from collections import Counter
from datetime import datetime, timezone
import json

from native_correspondence_prepare_v10 import DEST as NATIVE
from prepare import save, sha


def exact(field, reference):
    return (field is not None and field["name"] == reference["field_name"]
            and field["namespace"] == reference["field_namespace"])


def audit(root, references):
    folder = root / "learning_v1"
    score_file = folder / "development_results.json"
    scores = json.loads(score_file.read_text())
    manifest = json.loads((folder / "manifest.json").read_text())
    if not scores["complete"] or not manifest["complete"]:
        raise ValueError("All predictions and development scoring must be frozen first")
    if scores["reference_sha256"] != sha(NATIVE / "scoring_only/references.json"):
        raise ValueError("Development reference file changed after scoring")
    tasks_file = root / "public/tasks.json"
    tasks = json.loads(tasks_file.read_text())
    design = json.loads((folder / "design.json").read_text())
    if sha(folder / "design.json") != manifest["design_sha256"]:
        raise ValueError("Frozen learning design changed")
    if design["public_tasks_sha256"] != sha(tasks_file):
        raise ValueError("Frozen public inputs changed")
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    if len(future) != scores["future_targets"]:
        raise ValueError("Future development denominator changed")
    counts, by_year = Counter(), {}
    candidates_available = set()
    failed_ids = set()
    for ident, task in future.items():
        ref = references[ident]
        available = any(exact(c, ref) for c in task["field_candidates"])
        if available:
            candidates_available.add(ident)
        if task["common_observation_failed"]:
            failed_ids.add(ident)
        count = Counter({
            "targets": 1,
            "exact_gold_field_in_candidates": int(available),
            "public_retrieval_top1_correct": int(exact(task["field_candidates"][0], ref)),
            "common_visual_observation_failed": int(task["common_observation_failed"]),
        })
        counts.update(count)
        by_year.setdefault(str(task["source_year"]), Counter()).update(count)
    configurations = {}
    for label, score in scores["per_configuration"].items():
        policy, method, seed, budget = label.split("/")
        predictions = {}
        for company in sorted({t["company"] for t in future.values()}):
            file = folder / "predictions" / policy / company / method / seed / (budget + ".json")
            relative = str(file.relative_to(folder))
            if sha(file) != scores["prediction_file_sha256"][relative]:
                raise ValueError("Frozen prediction changed")
            body = json.loads(file.read_text())
            if not body["complete"] or body["design_sha256"] != manifest["design_sha256"]:
                raise ValueError("Incomplete or altered field configuration")
            expected = {i for i, t in future.items() if t["company"] == company}
            rows = body["predictions"]
            if len(rows) != len(expected) or {p["task_id"] for p in rows} != expected:
                raise ValueError("Incomplete prediction inventory")
            predictions.update({p["task_id"]: p["field"] for p in rows})
        if set(predictions) != set(future):
            raise ValueError("Incomplete combined prediction inventory")
        correct = {i for i, p in predictions.items() if exact(p, references[i])}
        if len(correct) != score["correct"] or not correct <= candidates_available:
            raise ValueError("Score or candidate-availability audit is inconsistent")
        configurations[label] = {
            "correct": len(correct),
            "retrieval_missing_gold": len(future) - len(candidates_available),
            "ranking_wrong_despite_gold_available": len(candidates_available - correct),
            "correct_on_visual_failure_targets": len(correct & failed_ids),
            "visual_failure_targets": len(failed_ids),
            "correct_on_valid_visual_targets": len(correct - failed_ids),
            "valid_visual_targets": len(future) - len(failed_ids),
            "subgroup_counts_are_diagnostic_not_replacement_primary_metrics": True,
        }
    return {
        "complete": True,
        "future_targets": len(future),
        "counts": dict(counts),
        "by_year": {k: dict(v) for k, v in by_year.items()},
        "exact_field_candidate_ceiling": len(candidates_available) / len(future),
        "per_configuration": configurations,
        "public_tasks_sha256": sha(tasks_file),
        "frozen_development_scores_sha256": sha(score_file),
        "all_scored_prediction_files_verified": True,
    }


def run():
    references_file = NATIVE / "scoring_only/references.json"
    references = json.loads(references_file.read_text())
    roots = {
        "v11": NATIVE / "field_retrieval_report_labels_v3",
        "v12": NATIVE / "native_context_crops_v12/field_retrieval_v1",
    }
    result = {
        "complete": True,
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "post_score_development_failure_diagnosis",
        "experiments": {label: audit(root, references) for label, root in roots.items()},
        "reference_sha256": sha(references_file),
        "runner_sha256": sha(__file__),
        "trained_or_modified_any_model_or_prediction": False,
        "independent_native_test_references_read": False,
        "field_gold_does_not_certify_visual_reading_or_header_ownership_errors": True,
        "cross_version_comparison_is_not_an_isolated_visual_context_ablation": True,
    }
    output = NATIVE / "failure_audit_v13/development_retrieval_ranking_audit.json"
    save(output, result)
    for label, evidence in result["experiments"].items():
        print(label, "retrieval and observation counts", evidence["counts"], flush=True)


if __name__ == "__main__":
    run()

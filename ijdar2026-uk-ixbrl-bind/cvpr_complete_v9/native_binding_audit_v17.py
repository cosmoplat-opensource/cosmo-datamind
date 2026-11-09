"""2023-only candidate audit; public expansion was frozen before label reads."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from native_binding_candidate_audit_v16 import matching_components
from native_binding_expand_v17 import DEST
from native_binding_prepare_v14 import DEST as TEACHERS
from native_binding_schema_v16 import DEST as ORIGINAL
from prepare import save, sha


def audit():
    output = DEST / "training_only/historical_candidate_audit.json"
    if output.exists():
        raise RuntimeError("Historical expansion audit already frozen")
    public_file = DEST / "public/tasks.json"
    public_manifest = json.loads((DEST / "manifest.json").read_text())
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    teacher_manifest = json.loads((TEACHERS / "manifest.json").read_text())
    if not public_manifest["complete"] or sha(public_file) != public_manifest["candidate_inventory_sha256"]:
        raise ValueError("Public expansion must be frozen before historical audit")
    if not teacher_manifest["complete"] or sha(teacher_file) != teacher_manifest["teacher_sha256"]:
        raise ValueError("Historical teacher source differs")
    tasks = [t for t in json.loads(public_file.read_text()) if t["source_year"] == 2023]
    teachers = json.loads(teacher_file.read_text())
    baseline_file = ORIGINAL / "training_only/historical_candidate_audit.json"
    baseline = json.loads(baseline_file.read_text())
    if len(tasks) != 280 or {t["task_id"] for t in tasks} != set(teachers):
        raise ValueError("Historical candidate inventory differs")
    results, counts, nonempty = {}, Counter({"historical_targets": len(tasks)}), Counter()
    for task in tasks:
        ident = task["task_id"]
        result = matching_components(task, teachers[ident])
        for key, value in baseline["per_target"][ident].items():
            if value and not result[key]:
                raise ValueError("Expanded candidate space lost a previously representable teacher component")
        results[ident] = result
        for key, value in result.items():
            counts[key] += value
        if (teachers[ident].get("teacher_binding") or {}).get("dimensions"):
            nonempty["targets"] += 1
            nonempty["tuple_representable"] += result.get("dimensions", False)
    snapshot = DEST / "historical_auditor_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {
        "complete": True, "audited_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "historical_candidate_feasibility_not_model_accuracy",
        "counts": dict(counts), "baseline_counts": baseline["counts"],
        "nonempty_historical_dimensions": dict(nonempty), "per_target": results,
        "public_candidate_sha256": sha(public_file), "historical_teacher_sha256": sha(teacher_file),
        "baseline_audit_sha256": sha(baseline_file), "auditor_snapshot_sha256": sha(snapshot),
        "no_future_or_reserved_independent_reference_files_read": True,
        "all_previous_component_positives_preserved": True,
        "new_period_boundaries_include_uncertain_calendar_hypotheses": True,
        "candidate_presence_does_not_certify_header_ownership_model_accuracy_or_ontology_gain": True,
        "human_runtime_confirmations": 0,
    })
    print("Historical expanded candidate feasibility", dict(counts), "nonempty dimensions", dict(nonempty), flush=True)


if __name__ == "__main__":
    audit()

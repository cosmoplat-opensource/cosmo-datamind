"""Historical candidate feasibility audit after the public v16 freeze.

Only already-authorized 2023 semantic teachers are opened. Component presence
is an upper bound for the candidate space, not model accuracy or proof of
visible header ownership. Missing teachers/candidates never become null labels.
"""

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import json
from pathlib import Path

from native_binding_prepare_v14 import DEST as TEACHERS
from native_binding_schema_v16 import DEST
from prepare import save, sha


def matching_components(task, teacher):
    if task["source_year"] != 2023 or teacher["source_year"] != 2023 or task["task_id"] != teacher["task_id"]:
        raise ValueError("Only matching historical teachers can be audited")
    truth = teacher.get("teacher_binding")
    if not truth:
        return {"teacher_available": False}
    def period_value(value):
        return {k: v for k, v in value.items() if k in {"kind", "instant", "start_date", "end_date"}}
    def dimensions_value(value):
        return sorted(value, key=lambda row: row["axis"])
    fields = ["{" + c["namespace"] + "}" + c["name"] for c in task["field_candidates"]]
    unit_rows = [u for u in task["unit_candidates"] if u.get("unit") == truth["unit"]]
    quantity, joint = False, False
    expected = Decimal(truth["quantity"])
    if not expected.is_finite():
        raise ValueError("Nonfinite historical semantic quantity")
    for q in task["quantity_candidates"]:
        literal = Decimal(q["printed_value"])
        for u in task["unit_candidates"]:
            if u.get("multiplier") is None:
                continue
            for sign in q["semantic_sign_candidates"]:
                multiplier = Decimal(u["multiplier"])
                with localcontext() as arithmetic:
                    arithmetic.prec = max(64, len(literal.as_tuple().digits) + len(multiplier.as_tuple().digits) + 2)
                    scaled = literal * multiplier * sign
                if scaled == expected:
                    quantity = True
                    joint |= u.get("unit") == truth["unit"]
    result = {
        "teacher_available": True,
        "field": truth["field"] in fields,
        "entity": any(e["entity"] == truth["entity"] for e in task["entity_candidates"]),
        "unit_identity": bool(unit_rows),
        "period": any(p["period"].get("complete") and period_value(p["period"]) == truth["period"] for p in task["period_candidates"]),
        "dimensions": any(dimensions_value(d["dimensions"]) == dimensions_value(truth["dimensions"]) for d in task["dimension_tuple_candidates"]),
        "quantity_any_proposed_unit_scale": quantity,
        "quantity_and_unit_same_candidate": joint,
    }
    result["all_components_representable_without_certifying_scope"] = all(result[k] for k in ("field", "entity", "period", "dimensions", "quantity_and_unit_same_candidate"))
    return result


def audit():
    output = DEST / "training_only/historical_candidate_audit.json"
    if output.exists():
        raise RuntimeError("Historical candidate feasibility audit already frozen")
    public_file = DEST / "public/tasks.json"
    manifest_file = DEST / "manifest.json"
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    teacher_manifest_file = TEACHERS / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    teacher_manifest = json.loads(teacher_manifest_file.read_text())
    if not manifest["complete"] or sha(public_file) != manifest["candidate_inventory_sha256"]:
        raise ValueError("Complete frozen shared candidate inventory required")
    if not teacher_manifest["complete"] or sha(teacher_file) != teacher_manifest["teacher_sha256"]:
        raise ValueError("Immutable complete historical teacher inventory required")
    historical = [t for t in json.loads(public_file.read_text()) if t["source_year"] == 2023]
    feedback = json.loads(teacher_file.read_text())
    if len(historical) != 280 or {t["task_id"] for t in historical} != set(feedback):
        raise ValueError("Historical candidate inventory differs from feedback")
    records, counts, nonempty = {}, Counter({"historical_targets": len(historical)}), Counter()
    for task in historical:
        ident = task["task_id"]
        result = matching_components(task, feedback[ident])
        records[ident] = result
        for key, value in result.items():
            counts[key] += int(value)
        if (feedback[ident].get("teacher_binding") or {}).get("dimensions"):
            nonempty["targets"] += 1
            nonempty["tuple_representable"] += result.get("dimensions", False)
    snapshot = DEST / "historical_auditor_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {
        "complete": True, "audited_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "historical_candidate_feasibility_not_model_accuracy",
        "counts": dict(counts), "nonempty_historical_dimensions": dict(nonempty), "per_target": records,
        "public_candidate_sha256": sha(public_file), "public_manifest_sha256": sha(manifest_file),
        "historical_teacher_sha256": sha(teacher_file), "historical_teacher_manifest_sha256": sha(teacher_manifest_file),
        "auditor_snapshot_sha256": sha(snapshot),
        "future_or_reserved_independent_reference_files_read": False,
        "public_candidate_inventory_changed_or_teacher_inserted_into_candidates": False,
        "any_new_model_accuracy_or_visible_header_scope_certified": False,
        "entity_candidate_is_common_metadata_assistance_not_visual_identity_learning": True,
        "human_runtime_confirmations": 0,
    })
    print("Historical candidate feasibility", dict(counts), "nonempty dimensions", dict(nonempty), flush=True)


if __name__ == "__main__":
    audit()

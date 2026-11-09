"""Post-freeze development audit of shared field and period omissions.

This auditor reads already-used development references only after verifying
all v23 predictions. It never supplies a reference value to a generator or
changes any prediction, candidate pool, training case or original score.
"""

import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from native_field_retrieval_v12 import DEST as FIELDS
from native_field_retrieval_v12 import SCHEMA, report_card
from native_scope_learning_v23 import FOLDER
from native_scope_score_v23 import validate_predictions
from native_visible_periods_v17 import calendar_start, date_mentions
from native_witness_inventory_v23 import DEST as WITNESSES
from prepare import save, sha
from score_native_binding_v17 import scoring_references

DEST = WITNESSES / "posthoc_candidate_omissions_v25/v2_original_public_text"


def field_name(card):
    return "{" + card["namespace"] + "}" + card["name"]


def field_status(expected, report, namespaces, catalog, returned):
    """Separate a ranking cutoff from an ineligible public declaration."""
    if expected in returned:
        return "returned_candidate"
    field = catalog.get(expected)
    if field is None:
        return "absent_from_global_public_catalog"
    if field["namespace"] not in namespaces:
        return "namespace_not_declared_for_report"
    if report_card(field, report) is None:
        return "no_report_local_or_public_standard_declaration"
    return "eligible_report_local_field_omitted_by_retrieval_cutoff"


def visible_sources(task):
    """The same text channels used by the frozen visible-period generator."""
    observation = task.get("common_visual_observation") or {}
    sources = [("native_top_text", task.get("public_top_text", "")),
               ("native_row_text", task.get("public_row_text", ""))]
    sources += [("observed_" + header["kind"], header["text"])
                for header in observation.get("headers", [])
                if header["kind"] in {"period", "version", "other"}]
    sources.append(("observed_section_text", observation.get("section_text", "")))
    return sources


def source_evidence_task(binding_task, original_public_task):
    """A reduced binding/scoring task is not a substitute for public text."""
    required = {"task_id", "report", "public_top_text", "public_row_text", "common_visual_observation"}
    if not required.issubset(original_public_task):
        raise ValueError("Original full public observation/text record is required")
    if any(binding_task[key] != original_public_task[key] for key in ("task_id", "report")):
        raise ValueError("Original public text belongs to another task or report")
    return original_public_task


def period_status(expected, values, task):
    """Describe available public clues without claiming they own the cell."""
    sources = visible_sources(task)
    mentions = [mention for _, text in sources for mention in date_mentions(text)]
    dates = {mention["date"].isoformat() for mention in mentions}
    years = {year for _, text in sources for year in re.findall(r"(?<!\d)(?:18|19|20|21|22)\d{2}(?!\d)", text)}
    end = expected.get("instant") if expected["kind"] == "instant" else expected.get("end_date")
    ends = {row.get("instant") if row["kind"] == "instant" else row.get("end_date")
            for row in values if row}
    kinds = sorted({row["kind"] for row in values if row and
                    (row.get("instant") if row["kind"] == "instant" else row.get("end_date")) == end})
    try:
        expected_end = date.fromisoformat(end)
    except (ValueError, TypeError):
        expected_end = None
    same_month_day = expected_end is not None and any(
        mention["date"].month == expected_end.month and mention["date"].day == expected_end.day
        for mention in mentions)
    annual = (expected["kind"] == "duration" and expected_end is not None and
              (start := calendar_start(expected_end)) is not None and start.isoformat() == expected["start_date"])
    return {
        "expected_kind": expected["kind"],
        "complete_period_available": expected in values,
        "expected_end_available_with_any_period_kind": end in ends,
        "candidate_kinds_with_expected_end": kinds,
        "expected_end_explicitly_visible_in_generator_text": end in dates,
        "expected_end_year_printed_in_generator_text": isinstance(end, str) and end[:4] in years,
        "a_full_date_with_expected_month_day_visible": same_month_day,
        "expected_start_explicitly_visible": expected.get("start_date") in dates,
        "expected_duration_matches_calendar_annual_hypothesis": annual,
        "any_visible_annual_end_anchor": any(m["annual_end_anchor"] for m in mentions),
        "any_visible_opening_or_at_anchor": any(m["opening_anchor"] for m in mentions),
        "visible_full_date_count": len(mentions),
        "visible_year_count": len(years),
        "clue_presence_does_not_certify_visible_scope_ownership": True,
    }


def checked_json(file, expected):
    if sha(file) != expected:
        raise ValueError(f"Frozen audit input changed: {file}")
    return json.loads(file.read_text())


def run():
    output = DEST / "candidate_omission_audit.json"
    if output.exists():
        raise RuntimeError("Completed candidate omission audit is frozen")
    manifest_file, _, future, companies, _ = validate_predictions()
    scores_file = FOLDER / "development_results.json"
    scores = json.loads(scores_file.read_text())
    if not scores["complete"] or scores["prediction_manifest_sha256"] != sha(manifest_file):
        raise ValueError("Complete source-matched frozen development scores required")
    schema_manifest = json.loads((SCHEMA / "manifest.json").read_text())
    field_manifest = json.loads((FIELDS / "manifest.json").read_text())
    witness_manifest = json.loads((WITNESSES / "public/manifest.json").read_text())
    if not all(m["complete"] for m in (schema_manifest, field_manifest, witness_manifest)):
        raise ValueError("Complete public source inventories required")
    catalog_file = SCHEMA / "field_catalog.json"
    namespaces_file = SCHEMA / "report_namespaces.json"
    field_file = FIELDS / "public/tasks.json"
    witness_file = WITNESSES / "public/witnesses.json"
    catalog_list = checked_json(catalog_file, schema_manifest["catalog_sha256"])
    namespaces = checked_json(namespaces_file, schema_manifest["report_namespaces_sha256"])
    retrieved = {row["task_id"]: row for row in checked_json(field_file, field_manifest["public_target_sha256"])}
    inventory = {row["task_id"]: row for row in checked_json(witness_file, witness_manifest["witness_inventory_sha256"])}
    retriever_file = Path(__file__).with_name("native_field_retrieval_v12.py")
    if sha(retriever_file) != field_manifest["retriever_snapshot_sha256"]:
        raise ValueError("Field audit helper differs from the frozen original retriever")
    catalog = {field_name(row): row for row in catalog_list}
    if len(catalog) != len(catalog_list):
        raise ValueError("Duplicate global field QName")
    # No new semantic reference is read before the frozen predictions and
    # original public inputs have been verified above.
    references, reference_hashes = scoring_references(future)
    if reference_hashes != scores["scoring_reference_and_source_sha256"]:
        raise ValueError("Reference provenance differs from the frozen v23 score")
    counts, period_counts, missing_kind_clues = Counter(), Counter(), Counter()
    records = []
    for ident, task in future.items():
        reference = references[ident]
        returned = {field_name(row) for row in retrieved[ident]["field_candidates"]}
        actual = {row["value"] for row in inventory[ident]["components"]["field"] if row["value"] is not None}
        if actual != returned:
            raise ValueError("Final field pool differs from the original 64-card retrieval")
        field = field_status(reference["field"], task["report"], namespaces[task["report"]], catalog, returned)
        values = [row["value"] for row in inventory[ident]["components"]["period"]]
        period = period_status(reference["period"], values, source_evidence_task(task, retrieved[ident]))
        counts[field] += 1
        if not period["complete_period_available"]:
            period_counts.update({key: int(value) for key, value in period.items() if isinstance(value, bool)})
            period_counts["missing_complete_periods"] += 1
            missing_kind_clues[period["expected_kind"]] += 1
            clue = ("same_end_in_another_candidate_kind" if period["expected_end_available_with_any_period_kind"] else
                    "full_end_date_visible_but_not_proposed" if period["expected_end_explicitly_visible_in_generator_text"] else
                    "printed_year_and_visible_month_day_anchor" if period["expected_end_year_printed_in_generator_text"] and period["a_full_date_with_expected_month_day_visible"] else
                    "printed_year_without_matching_month_day_anchor" if period["expected_end_year_printed_in_generator_text"] else
                    "no_expected_end_year_in_current_generator_text")
            missing_kind_clues[period["expected_kind"] + "/" + clue] += 1
        records.append({"task_id": ident, "report": task["report"], "field_status": field,
                        "returned_fields": len(returned), "period_diagnosis": period})
    snapshot = DEST / "auditor_snapshot.py"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    previous = DEST.parent / "candidate_omission_audit.json"
    save(output, {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "development_only": True, "unique_development_targets": len(future), "company_identifiers": companies,
        "field_omission_counts": dict(counts), "missing_period_overlapping_clue_counts": dict(period_counts),
        "missing_period_disjoint_clue_counts_by_kind": dict(missing_kind_clues), "records": records,
        "prediction_manifest_sha256": sha(manifest_file), "scores_sha256": sha(scores_file),
        "public_source_sha256": {str(p): sha(p) for p in (catalog_file, namespaces_file, field_file, witness_file, retriever_file)},
        "reference_and_source_sha256": reference_hashes, "auditor_snapshot_sha256": sha(snapshot),
        "all_predictions_validated_before_reference_access": True,
        "candidate_generators_predictions_or_original_scores_modified": False,
        "missing_fields_are_not_assumed_to_be_eligible_from_global_catalog_membership_alone": True,
        "missing_period_clues_are_diagnostics_not_added_reference_candidates": True,
        "original_public_observation_records_joined_by_task_id_and_report_before_text_audit": True,
        "supersedes_text_clue_statistics_only_of": str(previous),
        "superseded_audit_sha256": sha(previous),
        "superseded_issue": "The reduced binding tasks omit public observation/text; v1 treated absent keys as empty evidence. Field eligibility and candidate availability counts were unaffected. The original v1 artifact is preserved.",
        "independent_or_calibration_report_assets_or_answers_opened": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "ontology_unique_advantage_established": False})
    print("Frozen development candidate omissions", dict(counts), dict(missing_kind_clues), flush=True)


if __name__ == "__main__":
    run()

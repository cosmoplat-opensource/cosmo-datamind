"""Reference-blind common field banks and full-page OCR calendar hypotheses.

Report-local fields are stored by public catalog index without a top-k cut.
OCR dates are shared uncertain hypotheses, never a private answer or a proven
header-to-cell association. Existing v23 values remain in an external frozen
pool and are never discarded. This is preparation, not an accuracy result.
"""

import json
import re
from collections import Counter
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from native_field_retrieval_v12 import DEST as FIELDS
from native_field_retrieval_v12 import SCHEMA, field_text, report_card
from native_header_alignment_v21 import DEST as HEADERS
from native_header_alignment_v21 import text_lines
from native_visible_periods_v17 import date_mentions, visible_period_candidates
from native_witness_inventory_v23 import DEST as WITNESSES
from prepare import opaque, save, sha

DEST = WITNESSES.parent / "common_candidate_bank_v25"


def qname(card):
    return "{" + card["namespace"] + "}" + card["name"]


def report_indices(fields, report, namespaces):
    """Keep every eligible local/standard declaration, including ambiguity."""
    return [index for index, field in enumerate(fields) if field["namespace"] in namespaces
            and report_card(field, report) is not None]


def word_source(words, indices, kind):
    indices = list(dict.fromkeys(indices))
    boxes = [words[index]["box"] for index in indices]
    return {"kind": kind, "word_indices": indices,
            "text": " ".join(words[index]["text"] for index in indices),
            "box": [min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes)]}


def ocr_calendar_bank(words):
    """All locations survive; administrative source year is not an input."""
    lines = [word_source(words, [index for _, index in line], "original_ocr_line")
             for line in text_lines(words)]
    sources = list(lines)
    for previous, current in pairwise(lines):
        # A nearby preceding line may contain 'year ended'. This is evidence
        # adjacency only; it does not establish table or header ownership.
        gap = current["box"][1] - previous["box"][3]
        if -.01 <= gap <= .04:
            sources.append(word_source(words, previous["word_indices"] + current["word_indices"],
                                       "adjacent_original_ocr_lines_uncertain_context"))
    years = []
    for index, word in enumerate(words):
        for match in re.finditer(r"(?<!\d)(?:18|19|20|21|22)\d{2}(?!\d)", word["text"]):
            years.append({"year": int(match.group(0)), "word_indices": [index], "box": word["box"]})
    result = {}

    def add(period, support, basis, assumption):
        # Equality is defined on the original source lexical dates. This
        # preparer does not rewrite metrics or introduce datetime conversions.
        value = {key: val for key, val in period.items() if key != "complete"}
        key = json.dumps(value, sort_keys=True)
        entry = result.setdefault(key, {"period": value,
            "candidate_id": opaque(["common-ocr-period-v25", value]),
            "calendar_convention_assumed": True, "supporting_ocr_evidence": [],
            "uncertain": True, "visible_ownership_certified": False,
            "source_year_used": False})
        entry["calendar_convention_assumed"] &= assumption
        evidence = {"basis": basis, **support}
        if evidence not in entry["supporting_ocr_evidence"]:
            entry["supporting_ocr_evidence"].append(evidence)

    for source in sources:
        proposals, omitted = visible_period_candidates({"public_top_text": source["text"]}, maximum=10000)
        if omitted:
            raise ValueError("Unexpected calendar overflow; no silent candidate truncation")
        for proposal in proposals:
            add(proposal["period"], source, "calendar_parse_of_original_ocr_context",
                proposal["calendar_convention_assumed"])
        if source["kind"] != "original_ocr_line":
            continue
        for mention in date_mentions(source["text"]):
            for year in years:
                if not 0 < abs(year["year"] - mention["date"].year) <= 2:
                    continue
                try:
                    comparative = mention["date"].replace(year=year["year"])
                except ValueError:
                    continue
                add({"kind": "instant", "instant": comparative.isoformat()},
                    {"full_date_anchor": source, "printed_year_anchor": year},
                    "printed_year_plus_page_month_day_hypothesis_not_verified_scope", True)
    return {"period_hypotheses": [result[key] for key in sorted(result)], "printed_year_anchors": years,
            "bare_year_does_not_supply_month_day_or_complete_period": True,
            "every_location_kept_without_nearest_target_selection": True,
            "ocr_lexical_match_is_not_pixel_or_scope_certification": True}


def checked_json(path, digest):
    if sha(path) != digest:
        raise ValueError(f"Frozen common input changed: {path}")
    return json.loads(path.read_text())


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Completed common candidate bank is frozen")
    schema_manifest = json.loads((SCHEMA / "manifest.json").read_text())
    field_manifest = json.loads((FIELDS / "manifest.json").read_text())
    header_manifest = json.loads((HEADERS / "localization/manifest.json").read_text())
    witness_manifest = json.loads((WITNESSES / "public/manifest.json").read_text())
    if not all(m["complete"] for m in (schema_manifest, field_manifest, header_manifest, witness_manifest)):
        raise ValueError("Complete common inventories required")
    catalog_file, namespaces_file = SCHEMA / "field_catalog.json", SCHEMA / "report_namespaces.json"
    task_file = FIELDS / "public/tasks.json"
    page_file = HEADERS / "localization/public_pages.json"
    witness_file = WITNESSES / "public/witnesses.json"
    fields = checked_json(catalog_file, schema_manifest["catalog_sha256"])
    namespaces = checked_json(namespaces_file, schema_manifest["report_namespaces_sha256"])
    tasks = checked_json(task_file, field_manifest["public_target_sha256"])
    pages = checked_json(page_file, header_manifest["public_pages_sha256"])
    witnesses = checked_json(witness_file, witness_manifest["witness_inventory_sha256"])
    if len(tasks) != 840 or [row["task_id"] for row in tasks] != [row["task_id"] for row in witnesses]:
        raise ValueError("Full ordered common target inventory required")
    embedding_file = FIELDS / "shared_embeddings/catalog.npz"
    embedding_meta_file = FIELDS / "shared_embeddings/catalog_metadata.json"
    embeddings = json.loads(embedding_meta_file.read_text())
    if sha(embedding_file) != embeddings["cache_sha256"] or sha(catalog_file) != embeddings["catalog_sha256"]:
        raise ValueError("Frozen public catalog embeddings changed")
    text_index = {text: index for index, text in enumerate(embeddings["strings"])}
    if len(text_index) != len(embeddings["strings"]):
        raise ValueError("Ambiguous frozen text vector indexing")
    reports = sorted({row["report"] for row in tasks})
    banks = {}
    for report in reports:
        indices = report_indices(fields, report, namespaces[report])
        banks[report] = {"catalog_indices": indices,
            "semantic_embedding_indices": [text_index[field_text(report_card(fields[index], report))] for index in indices],
            "unknown_field_alternative_retained": True, "top_k_truncation_applied": False,
            "visible_field_or_dimension_role_ownership_certified": False}
    calendar_banks, records = {}, []
    counts = Counter({"targets": len(tasks), "reports": len(reports), "global_public_fields": len(fields)})
    for task, old in zip(tasks, witnesses, strict=True):
        report = task["report"]
        eligible = {qname(fields[index]) for index in banks[report]["catalog_indices"]}
        original = {row["value"] for row in old["components"]["field"] if row["value"] is not None}
        if not original.issubset(eligible):
            raise ValueError("Full local field bank lost an original candidate")
        digest = task["image_sha256"]
        page = pages[digest]
        if page["image_sha256"] != digest:
            raise ValueError("Original OCR image mapping changed")
        if digest not in calendar_banks:
            original_ocr = Path(page["ocr_path"])
            if sha(original_ocr) != page["ocr_sha256"]:
                raise ValueError("Original common OCR changed")
            calendar_banks[digest] = ocr_calendar_bank(page["words"])
            calendar_banks[digest]["ocr_sha256"] = page["ocr_sha256"]
            counts["shared_page_period_hypotheses"] += len(calendar_banks[digest]["period_hypotheses"])
            counts["printed_year_locations"] += len(calendar_banks[digest]["printed_year_anchors"])
        records.append({"task_id": task["task_id"], "report_field_bank": report,
                        "additional_calendar_page_bank": digest, "preserved_original_witness_task": task["task_id"],
                        "all_original_semantic_values_and_unresolved_alternatives_retained": True})
        counts["uncut_field_alternatives_across_tasks"] += len(eligible)
        counts["previous_field_alternatives_across_tasks"] += len(original)
    counts["original_ocr_pages"] = len(calendar_banks)
    DEST.mkdir(parents=True, exist_ok=True)
    save(DEST / "public/report_field_banks.json", banks)
    save(DEST / "public/page_calendar_banks.json", calendar_banks)
    save(DEST / "public/tasks.json", records)
    snapshot = DEST / "builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    helper_sources = [Path(__file__).with_name(name) for name in (
        "native_field_retrieval_v12.py", "native_header_alignment_v21.py", "native_visible_periods_v17.py")]
    public_sources = (catalog_file, namespaces_file, task_file, page_file, witness_file, embedding_file, embedding_meta_file)
    save(DEST / "manifest.json", {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "common_reference_blind_candidate_preparation_not_training_or_accuracy",
        "counts": dict(counts), "builder_snapshot_sha256": sha(snapshot),
        "source_sha256": {str(p): sha(p) for p in (*public_sources, *helper_sources)},
        "output_sha256": {name: sha(DEST / "public" / name) for name in (
            "report_field_banks.json", "page_calendar_banks.json", "tasks.json")},
        "all_controls_receive_the_same_full_report_field_and_ocr_calendar_banks": True,
        "original_v23_values_unknowns_and_scores_unchanged": True,
        "no_per_task_nearest_header_or_reference_selection": True,
        "scope_ownership_canonical_period_or_full_semantic_correspondence_certified": False,
        "publisher_instances_contexts_teacher_or_scoring_references_read": False,
        "new_reserved_or_calibration_reports_or_assets_opened": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "candidate_recall_evaluation_and_training_integration_complete": False,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Frozen uncut common field/OCR candidate bank", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

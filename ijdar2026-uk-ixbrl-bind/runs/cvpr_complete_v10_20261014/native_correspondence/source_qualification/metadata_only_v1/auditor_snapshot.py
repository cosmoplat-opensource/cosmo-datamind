"""Audit the frozen source roster without opening reserved report assets.

Exact LEI/name-string overlaps and portal timestamp ordering are risk checks.
They do not certify legal-group independence, templates, original publication
times, rendered visibility or correctness of financial semantic labels.
"""

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import pairwise
from pathlib import Path

from prepare import EXP, save, sha

SOURCE = EXP / "runs/visual_scope_relations_v8_20261009/native_source_reservation_v1/metadata_reservation.json"
PLAN = EXP / "runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1/download_plan.json"
DEST = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/source_qualification/metadata_only_v1"


def name_key(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value or "").casefold() if c.isalnum())


def portal_order(reports, field):
    """Compare a metadata field in period order without inventing a timezone."""
    try:
        ordered = sorted(reports, key=lambda r: r["attributes"]["period_end"])
        values = [datetime.fromisoformat(r["attributes"][field].replace("Z", "+00:00")) for r in ordered]
    except (KeyError, TypeError, ValueError, AttributeError):
        return {"comparable": False, "reason": "missing_or_unparseable_portal_timestamp"}
    if len({v.tzinfo is not None for v in values}) != 1:
        return {"comparable": False, "reason": "mixed_timezone_awareness_no_timezone_assumed"}
    return {"comparable": True, "nondecreasing_in_report_period_order": all(a <= b for a, b in pairwise(values)),
            "timestamps_have_explicit_timezones": all(v.tzinfo is not None for v in values),
            "original_publication_chronology_certified": False}


def run():
    output = DEST / "audit.json"
    if output.exists():
        raise RuntimeError("Metadata-only source audit already frozen")
    roster, plan = json.loads(SOURCE.read_text()), json.loads(PLAN.read_text())
    if not roster["metadata_only"] or sha(SOURCE) != plan["reservation_sha256"]:
        raise ValueError("Frozen metadata reservation/download provenance changed")
    entities = roster["entities"]
    leis, names = defaultdict(list), defaultdict(list)
    for entity in entities:
        leis[entity["lei"]].append(entity["tentative_role"])
        if name_key(entity.get("name")):
            names[name_key(entity["name"])].append((entity["lei"], entity["tentative_role"]))
    counts, role_counts, rows = Counter(), defaultdict(Counter), []
    for entity in entities:
        role, lei, reports = entity["tentative_role"], entity["lei"], entity["reports"]
        local = Counter({"entities": 1, "report_metadata_records": len(reports)})
        local["identifier_matches_provided_20_character_format"] += bool(re.fullmatch(r"[A-Z0-9]{20}", lei))
        local["duplicate_exact_identifier_in_roster"] += len(leis[lei]) > 1
        collision = names.get(name_key(entity.get("name")), [])
        local["same_normalized_provided_name_as_another_identifier"] += any(other != lei for other, _ in collision)
        local["reports_with_multiple_same_entity_period_versions_in_index"] += sum(r.get("same_entity_period_versions_in_index", 1) > 1 for r in reports)
        local["filing_identifier_prefix_differs_from_entity_identifier"] += sum(not r["attributes"].get("fxo_id", "").startswith(lei + "-") for r in reports)
        ordering = {field: portal_order(reports, field) for field in ("date_added", "processed")}
        for field, status in ordering.items():
            local[field + "/unknown_order"] += not status["comparable"]
            local[field + "/nonmonotonic_period_order"] += status["comparable"] and not status["nondecreasing_in_report_period_order"]
        rows.append({"identifier": lei, "provided_name": entity.get("name"), "tentative_role": role,
                     "portal_timestamp_ordering": ordering, "metadata_risk_counts": dict(local),
                     "legal_group_and_name_aliases_certified": False, "template_independence_certified": False})
        counts.update(local)
        role_counts[role].update(local)
    if len(entities) != 402 or sum(e["tentative_role"] == "reserved_independent_test" for e in entities) != 60:
        raise ValueError("The original frozen source roster denominator changed")
    snapshot = DEST / "auditor_snapshot.py"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "audited_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "existing metadata only; not a certification of independent source eligibility",
        "counts": dict(counts), "counts_by_tentative_role": {k: dict(v) for k, v in role_counts.items()},
        "entities": rows, "metadata_reservation_sha256": sha(SOURCE), "development_plan_sha256": sha(PLAN),
        "auditor_snapshot_sha256": sha(snapshot),
        "provided_identifier_format_check_is_not_registry_or_checksum_validation": True,
        "name_string_similarity_is_not_legal_entity_resolution": True,
        "portal_time_order_does_not_certify_original_publication_or_version_chronology": True,
        "no_cohort_roles_reassigned_and_no_sources_replaced": True,
        "reserved_or_calibration_report_images_instance_facts_or_labels_opened": False,
        "independent_source_eligibility_certified": False, "native_visual_scope_teacher_certified": False,
        "human_runtime_confirmations": 0, "ontology_unique_advantage_established": False,
        "all_original_cvpr_experiments_completed": False})
    print("Metadata-only source roster audit", dict(counts), flush=True)


if __name__ == "__main__":
    run()

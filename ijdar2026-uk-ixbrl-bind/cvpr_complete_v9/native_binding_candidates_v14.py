"""Reference-blind shared component candidates for native binding development.

Only frozen public OCR, pixel observations and report-local field cards enter
this builder. Source year never supplies a period or missing boundary. These
are factorized hypotheses, not certified bindings or header ownership labels.
Canonical dimension/entity resolution remains a subsequent required stage.
"""

from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

from native_field_retrieval_v12 import DEST as SOURCE
from native_binding_prepare_v14 import DEST
from prepare import opaque, save, sha


XI = "http://www.xbrl.org/2003/instance"
ISO = "http://www.xbrl.org/2003/iso4217"
MAX_LITERAL_VALUES = 12
MAX_SCALE = 9


def literal_values(text):
    """Preserve grouping/decimal ambiguity; a dash never supplies zero."""
    if not isinstance(text, str) or len(text) > 80:
        return []
    value = text.strip().replace("−", "-").replace("–", "-")
    value = re.sub(r"^[$£€¥]\s*", "", value)
    value = re.sub(r"\s*(?:%|million|millions|thousand|thousands|billion|billions)\s*$", "", value, flags=re.I)
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = value[1:-1].strip()
    if re.fullmatch(r"[+-]?\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?", value):
        value = re.sub(r"[ \u00a0\u202f]", "", value)
    elif re.search(r"\s", value):
        return []
    parses = set()
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", value):
        parses.add(Decimal(value))
    if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
        parses.add(Decimal(value.replace(",", "")))
    if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?", value):
        parses.add(Decimal(value.replace(".", "").replace(",", ".")))
    if re.fullmatch(r"[+-]?\d+,\d+", value):
        parses.add(Decimal(value.replace(",", ".")))
    if negative:
        if value.startswith(("+", "-")):
            return []
        parses = {-v for v in parses}
    return sorted(str(v) for v in parses)


def unit_identity(proposal):
    kind, currency = proposal.get("kind"), proposal.get("currency")
    if kind in ("rate", "count"):
        return {"numerator": ["{" + XI + "}pure"], "denominator": []}
    if kind == "shares":
        return {"numerator": ["{" + XI + "}shares"], "denominator": []}
    if kind in ("monetary", "currency_per_share") and isinstance(currency, str) and re.fullmatch(r"[A-Z]{3}", currency):
        return {"numerator": ["{" + ISO + "}" + currency],
                "denominator": ["{" + XI + "}shares"] if kind == "currency_per_share" else []}
    return None


def visible_period(proposal):
    kind = proposal.get("kind")
    start, end = proposal.get("start_date"), proposal.get("end_date")
    for item in (start, end):
        if item is not None and (not isinstance(item, str) or len(item) != 10 or date.fromisoformat(item).isoformat() != item):
            raise ValueError("Malformed observed period boundary")
    if start and end and start > end:
        raise ValueError("Inverted observed period")
    if kind == "instant":
        return {"kind": "instant", "instant": end, "complete": end is not None}
    if kind == "duration":
        return {"kind": "duration", "start_date": start, "end_date": end,
                "complete": start is not None and end is not None}
    return {"kind": "unknown", "complete": False}


def component_record(task):
    obs = task.get("common_visual_observation") or {}
    quantities, units, periods = [], [], []
    readings = list(obs.get("literal_readings", [])) + list(task["native_ocr_strings"])
    readings = list(dict.fromkeys(readings))
    values = {}
    for text in readings:
        for value in literal_values(text):
            values.setdefault(value, []).append(text)
    for value in sorted(values, key=lambda v: (Decimal(v), v))[:MAX_LITERAL_VALUES]:
        quantities.append({"candidate_id": opaque(["literal-v14", value]), "printed_value": value,
                           "source_glyph_hypotheses": values[value],
                           "semantic_sign_candidates": [1, -1],
                           "canonical_quantity_not_supplied": True})
    headers = {h["id"]: h for h in obs.get("headers", [])}
    for proposal in obs.get("unit_candidates", []):
        multiplier = proposal.get("multiplier")
        if multiplier is not None:
            try:
                number = Decimal(str(multiplier))
                if not number.is_finite() or number not in {Decimal(10) ** k for k in range(-MAX_SCALE, MAX_SCALE+1)}:
                    raise ValueError("Unknown or malformed observed multiplier")
                multiplier = str(number)
            except InvalidOperation:
                raise ValueError("Malformed observed multiplier") from None
        units.append({"candidate_id": opaque(["unit-v14", proposal]), "unit": unit_identity(proposal),
                      "multiplier": multiplier, "kind": proposal["kind"],
                      "header": headers.get(proposal["header_id"]),
                      "uncertain": proposal["uncertain"], "header_ownership_certified": False,
                      "currency_code_is_model_hypothesis_not_registry_certification": True})
    for proposal in obs.get("period_candidates", []):
        periods.append({"candidate_id": opaque(["period-v14", proposal]), "period": visible_period(proposal),
                        "displayed_year": proposal.get("displayed_year"),
                        "header": headers.get(proposal["header_id"]),
                        "uncertain": proposal["uncertain"], "header_ownership_certified": False})
    return {
        "task_id": task["task_id"], "company": task["company"], "source_year": task["source_year"],
        "report": task["report"], "target_box": task["target_box"],
        "field_candidates": task["field_candidates"],
        "quantity_candidates": quantities, "unit_candidates": units, "period_candidates": periods,
        "literal_parse_candidate_count_before_cap": len(values),
        "literal_parse_candidates_omitted_by_fixed_cap": max(0, len(values)-MAX_LITERAL_VALUES),
        "unknown_candidates": {
            name: {"candidate_id": opaque(["unknown-component-v14", name]), "unknown": True}
            for name in ("field", "quantity", "unit", "period", "entity", "dimension", "scope")
        },
        "entity_visible_text": obs.get("entity_text", "unknown"),
        "dimension_visible_text": obs.get("dimension_text", "unknown"),
        "version_visible_text": obs.get("version_text", "unknown"),
        "canonical_entity_and_dimension_resolution_complete": False,
        "common_observation_failed": task["common_observation_failed"],
        "unknown_candidate_retained_for_each_component": True,
        "all_scope_boxes_are_uncertified_model_hypotheses": True,
        "full_binding_prediction_not_generated": True,
    }


def prepare():
    folder = DEST / "public_component_candidates"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Public component candidate inventory already frozen")
    public_file = SOURCE / "public/tasks.json"
    retrieval_file = SOURCE / "manifest.json"
    meta = json.loads(retrieval_file.read_text())
    if not meta["complete"] or meta["public_target_sha256"] != sha(public_file):
        raise ValueError("Frozen public retrieval input changed")
    tasks = json.loads(public_file.read_text())
    if len(tasks) != 840 or len({t["task_id"] for t in tasks}) != 840:
        raise ValueError("Full public target inventory changed")
    records = [component_record(t) for t in tasks]
    counts = Counter({"targets": len(records)})
    for record in records:
        counts["targets_with_literal_quantity_candidate"] += bool(record["quantity_candidates"])
        counts["targets_with_proposed_unit_identity_and_scale"] += any(
            u["unit"] is not None and u["multiplier"] is not None for u in record["unit_candidates"])
        counts["targets_with_complete_proposed_period"] += any(p["period"]["complete"] for p in record["period_candidates"])
        counts["failed_visual_observations_retained"] += record["common_observation_failed"]
        counts["literal_parse_candidates_omitted_by_fixed_cap"] += record["literal_parse_candidates_omitted_by_fixed_cap"]
    file = folder / "tasks.json"
    save(file, records)
    snapshot = folder / "candidate_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "stage": "public_factorized_component_candidates_not_training_or_prediction",
        "public_input_sha256": sha(public_file), "candidate_inventory_sha256": sha(file),
        "builder_snapshot_sha256": sha(snapshot), "maximum_literal_values": MAX_LITERAL_VALUES,
        "all_controls_receive_the_same_candidate_inventory": True,
        "source_year_used_to_supply_period_or_boundaries": False,
        "any_teacher_or_scoring_reference_file_read": False,
        "private_target_field_or_multiplier_supplied": False,
        "canonical_entity_and_dimension_resolution_complete": False,
        "candidate_presence_counts_are_not_accuracy_or_verified_coverage": True,
        "learned_full_binding_method_or_independent_test_complete": False,
    })
    print("Frozen public binding component candidates", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

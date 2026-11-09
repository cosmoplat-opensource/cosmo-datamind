"""Shared uncertain dash/visible-unit and located-period hypotheses.

No private label is read. A financial dash may encode zero or an unavailable
value: zero is an additional semantic hypothesis, never a literal fact. The
unknown alternative remains. All v17 semantic values are retained unchanged.
"""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from native_binding_expand_v17 import DEST as PREVIOUS
from native_binding_prepare_v14 import DEST as BINDING
from native_binding_candidates_v14 import unit_identity
from native_field_retrieval_v12 import DEST as FIELD
from native_visible_periods_v17 import date_mentions
from prepare import opaque, save, sha


DEST = BINDING.parent / "joint_binding_v18"
DASHES = {"-", "−", "–", "—"}


def visible_unit_hypotheses(source):
    obs = source.get("common_visual_observation") or {}
    evidence = [(source.get("public_top_text", ""), None), (source.get("public_row_text", ""), None)]
    evidence += [(h["text"], h) for h in obs.get("headers", []) if h["kind"] == "unit"]
    result, seen = [], set()
    # Currency symbols are unambiguous here; bare '$' is intentionally excluded.
    currency_re = re.compile(r"(?P<currency>US\$|A\$|C\$|NZ\$|£|€|GBP|EUR|USD|AUD|CAD|NZD)\s*(?P<scale>millions?|thousands?|billions?|000(?:[’']s)?|mn|bn|m|k)?(?![A-Za-z])", re.I)
    codes = {"us$": "USD", "a$": "AUD", "c$": "CAD", "nz$": "NZD", "£": "GBP", "€": "EUR"}
    for text, header in evidence:
        for match in currency_re.finditer(text):
            currency = codes.get(match["currency"].lower(), match["currency"].upper())
            scale = (match["scale"] or "").lower()
            power = 9 if scale in {"billion", "billions", "bn"} else 6 if scale in {"million", "millions", "mn", "m"} else 3 if scale.startswith(("thousand", "000")) or scale == "k" else 0
            kind = "monetary"
            if re.search(r"(?:per\s+share|/\s*share)", text[match.end():match.end()+30], re.I):
                kind = "currency_per_share"
            proposal = {"kind": kind, "currency": currency}
            unit = unit_identity(proposal)
            key = json.dumps([unit, power, (header or {}).get("id")], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            result.append({"candidate_id": opaque(["visible-unit-v18", key]), "unit": unit,
                           "multiplier": str(10**power), "kind": kind, "header": header,
                           "uncertain": True, "header_ownership_certified": False,
                           "basis": "explicit_visible_currency_and_scale_hypothesis",
                           "supporting_visible_text": match.group(0),
                           "no_currency_inferred_from_administrative_company_or_source_year": True})
    return result


def located_periods(old_periods, source):
    obs = source.get("common_visual_observation") or {}
    additions = []
    for row in old_periods:
        period = row["period"]
        end = period.get("instant") or period.get("end_date")
        if not period.get("complete") or not end:
            continue
        for header in obs.get("headers", []):
            if header["kind"] != "period":
                continue
            dates = {m["date"].isoformat() for m in date_mentions(header["text"])}
            years = set(re.findall(r"(?<!\d)(?:18|19|20|21|22)\d{2}(?!\d)", header["text"]))
            if (dates and end not in dates) or (not dates and years != {end[:4]}):
                continue
            additions.append({**row, "candidate_id": opaque(["located-period-v18", row["candidate_id"], header]),
                              "header": header, "uncertain": True, "header_ownership_certified": False,
                              "basis": "matching_visible_date_or_column_year_header_hypothesis",
                              "supporting_header_match_does_not_certify_ownership": True})
    return additions


def expand(task, source):
    # Build from explicit public fields only, never copying arbitrary teacher keys.
    new = {k: v for k, v in task.items() if k not in {"teacher_binding", "canonical_value", "canonical_dimensions", "reference"}}
    quantities = list(task["quantity_candidates"])
    obs = source.get("common_visual_observation") or {}
    visible_dash = [s for s in source["native_ocr_strings"] + obs.get("literal_readings", []) if s.strip() in DASHES]
    if visible_dash:
        quantities.append({"candidate_id": opaque(["possible-dash-zero-v18"]), "printed_value": "0",
                           "source_glyph_hypotheses": sorted(set(visible_dash)), "semantic_sign_candidates": [1],
                           "canonical_quantity_not_supplied": True,
                           "semantic_zero_hypothesis_not_literal_zero": True,
                           "unavailable_or_inapplicable_interpretation_retained_as_unknown": True})
    units = list(task["unit_candidates"])
    unit_keys = {(json.dumps(u["unit"], sort_keys=True), u["multiplier"], (u.get("header") or {}).get("id")) for u in units}
    for u in visible_unit_hypotheses(source):
        key = (json.dumps(u["unit"], sort_keys=True), u["multiplier"], (u.get("header") or {}).get("id"))
        if key not in unit_keys:
            unit_keys.add(key)
            units.append(u)
    return {**new, "quantity_candidates": quantities, "unit_candidates": units,
            "period_candidates": task["period_candidates"] + located_periods(task["period_candidates"], source),
            "unknown_candidates": task["unknown_candidates"], "all_v17_semantic_values_preserved": True,
            "reference_or_teacher_used_to_expand_candidates": False}


def prepare():
    if (DEST / "candidates/manifest.json").exists():
        raise RuntimeError("Shared v18 candidate inventory already frozen")
    previous_file = PREVIOUS / "public/tasks.json"
    previous_manifest = json.loads((PREVIOUS / "manifest.json").read_text())
    source_file = FIELD / "public/tasks.json"
    source_manifest = json.loads((FIELD / "manifest.json").read_text())
    if not previous_manifest["complete"] or sha(previous_file) != previous_manifest["candidate_inventory_sha256"] or sha(source_file) != source_manifest["public_target_sha256"]:
        raise ValueError("Frozen public input differs")
    tasks = json.loads(previous_file.read_text())
    sources = json.loads(source_file.read_text())
    if len(tasks) != 840 or [t["task_id"] for t in tasks] != [t["task_id"] for t in sources]:
        raise ValueError("Full ordered public target inventory required")
    records, counts = [], Counter({"targets": len(tasks)})
    for task, source in zip(tasks, sources, strict=True):
        new = expand(task, source)
        records.append(new)
        counts["targets_with_dash_zero_semantic_alternative"] += len(new["quantity_candidates"]) > len(task["quantity_candidates"])
        counts["additional_visible_unit_hypotheses"] += len(new["unit_candidates"])-len(task["unit_candidates"])
        counts["additional_located_period_hypotheses"] += len(new["period_candidates"])-len(task["period_candidates"])
        counts["failed_visual_observations_retained"] += task["common_observation_failed"]
    folder = DEST / "candidates"
    save(folder / "tasks.json", records)
    snapshot = folder / "candidate_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "candidate_inventory_sha256": sha(folder / "tasks.json"), "runner_snapshot_sha256": sha(snapshot),
        "previous_public_input_sha256": sha(previous_file), "native_public_input_sha256": sha(source_file),
        "dash_zero_is_uncertain_semantic_hypothesis_not_reading_of_digit_zero": True,
        "unknown_alternatives_and_all_old_semantic_values_preserved": True,
        "all_controls_receive_identical_candidate_expansion": True,
        "historical_future_or_reserved_test_reference_files_read": False,
        "development_after_v17_scores_not_independent_test": True,
        "new_generative_visual_api_calls": 0,
    })
    print("v18 shared uncertain candidate expansion", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

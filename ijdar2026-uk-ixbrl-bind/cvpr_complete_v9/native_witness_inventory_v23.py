"""Preserve all public visual witnesses for each proposed semantic value.

This changes representation, not the inventory of proposed answer values.
Repeated dates/currencies stay at all visible OCR locations; they are not
resolved using a reference quantity, calendar answer or nearest-target rule.
Every value retains an unresolved-ownership alternative. This artifact is a
common input for all subsequent controls, never an ontology-only retrieval aid.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

from native_binding_features_v17 import CHANNELS, quantity_value
from native_header_alignment_v21 import DEST as INPUT
from prepare import opaque, save, sha

DEST = INPUT.parent / "witness_composition_v23"


def exact_decimal(value):
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("Nonfinite proposed quantity")
    if not number:
        return "0"
    with localcontext() as context:
        context.prec = max(64, len(number.as_tuple().digits))
        return format(number.normalize(), "f")


def semantic_key(channel, value):
    if channel not in CHANNELS:
        raise ValueError("Unknown component role")
    if value is not None and channel == "unit_quantity":
        value = {**value, "quantity": exact_decimal(value["quantity"])}
    elif value is not None and channel == "dimensions":
        value = sorted(value, key=lambda row: json.dumps(row, sort_keys=True))
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def source_paths(task, records, channel):
    """All semantic aliases, including unit headers dropped by old deduplication."""
    source = defaultdict(dict)
    for i, row in enumerate(records):
        key = semantic_key(channel, row["value"])
        header = row["metadata"].get("header") or {}
        signature = json.dumps([header.get("id"), row["metadata"].get("basis"),
                                row["metadata"].get("printed"), row["metadata"].get("multiplier"),
                                row["metadata"].get("semantic_sign")], sort_keys=True)
        path = source[key].setdefault(signature, {"record_indices": [], "header_id": header.get("id"),
                                                  "basis": row["metadata"].get("basis"),
                                                  "printed": row["metadata"].get("printed"),
                                                  "multiplier": row["metadata"].get("multiplier"),
                                                  "semantic_sign": row["metadata"].get("semantic_sign")})
        path["record_indices"].append(i)
    if channel == "unit_quantity":
        for quantity in task["quantity_candidates"]:
            for unit in task["unit_candidates"]:
                if unit.get("unit") is None or unit.get("multiplier") is None:
                    continue
                for sign in quantity["semantic_sign_candidates"]:
                    value = {"unit": unit["unit"], "quantity": quantity_value(quantity["printed_value"], unit["multiplier"], sign)}
                    key = semantic_key(channel, value)
                    if key not in source:
                        raise ValueError("Witness restoration may not introduce a new semantic value")
                    header = unit.get("header") or {}
                    # Distinct paths are defined by observable interpretation,
                    # not by repeated IDs for the same model reading.
                    signature = json.dumps([header.get("id"), unit.get("basis"), quantity["printed_value"], unit["multiplier"], sign], sort_keys=True)
                    if signature not in source[key]:
                        source[key][signature] = {
                            "record_indices": [], "header_id": header.get("id"), "basis": unit.get("basis"),
                            "printed": quantity["printed_value"], "multiplier": unit["multiplier"], "semantic_sign": sign,
                            "restored_from_public_unit_and_literal_proposals": True,
                        }
    return source


def component_groups(task, records, public_headers, channel):
    paths = source_paths(task, records, channel)
    null_key = semantic_key(channel, None)
    paths.setdefault(null_key, {})
    values, indices = {}, defaultdict(list)
    for i, row in enumerate(records):
        key = semantic_key(channel, row["value"])
        values.setdefault(key, row["value"])
        indices[key].append(i)
    values.setdefault(null_key, None)
    indices[null_key].append(len(records))  # old explicit unknown node
    groups = []
    for number, (key, value) in enumerate(values.items()):
        witnesses, seen = [], set()
        for path in paths[key].values():
            header_id = path["header_id"]
            if header_id is not None and header_id not in public_headers:
                raise ValueError("A proposed header is absent from the frozen public header inventory")
            header = public_headers.get(header_id, {})
            anchors = header.get("anchors", []) if header.get("may_supply_transfer_hypotheses") else []
            for anchor in anchors or [None]:
                signature = json.dumps([path, (anchor or {}).get("anchor_id")], sort_keys=True)
                if signature in seen:
                    continue
                seen.add(signature)
                witnesses.append({
                    **path, "witness_id": opaque([task["task_id"], channel, number, signature]),
                    "anchor": anchor, "anchor_status": header.get("status", "no_proposed_header"),
                    "ownership": "unresolved_hypothesis", "visual_ownership_certified": False,
                })
        # Even a uniquely located text can be the wrong governing header.
        witnesses.append({"witness_id": opaque([task["task_id"], channel, number, "unresolved"]),
                          "record_indices": indices[key], "header_id": None, "anchor": None,
                          "anchor_status": "unresolved_visual_ownership_alternative",
                          "ownership": "unknown", "visual_ownership_certified": False})
        groups.append({"semantic_group": number, "value": value, "semantic_key": key,
                       "source_record_indices": indices[key], "witnesses": witnesses})
    expected = {semantic_key(channel, row["value"]) for row in records} | {null_key}
    if {row["semantic_key"] for row in groups} != expected:
        raise ValueError("Public semantic values changed during witness grouping")
    if channel == "field" and any(len(row["source_record_indices"]) != 1 for row in groups):
        raise ValueError("The central field must have one node per proposed semantic value")
    return groups


def prepare():
    manifest_path = DEST / "public/manifest.json"
    if manifest_path.exists():
        raise RuntimeError("Common witness inventory is already frozen")
    task_file, record_file = INPUT / "candidates/tasks.json", INPUT / "features/records.json"
    anchors_file = INPUT / "localization/public_anchors.json"
    feature_file, localization_file = INPUT / "features/manifest.json", INPUT / "localization/manifest.json"
    feature, localization = [json.loads(p.read_text()) for p in (feature_file, localization_file)]
    if not feature["complete"] or not localization["complete"]:
        raise ValueError("Complete frozen v21 public inputs required")
    if sha(task_file) != feature["public_candidate_sha256"] or sha(record_file) != feature["files"][record_file.name] or sha(anchors_file) != localization["public_anchors_sha256"]:
        raise ValueError("Frozen public candidate/anchor provenance changed")
    tasks, records, anchors = [json.loads(p.read_text()) for p in (task_file, record_file, anchors_file)]
    if len(tasks) != len(records) or len(tasks) != 840 or {t["task_id"] for t in tasks} != set(anchors):
        raise ValueError("All 840 public targets required")
    counts, prepared, per_year = Counter(), [], defaultdict(Counter)
    pair_counts = []
    for task, row in zip(tasks, records, strict=True):
        groups = {c: component_groups(task, row[c], anchors[task["task_id"]]["headers"], c) for c in CHANNELS}
        prepared.append({"task_id": task["task_id"], "company": task["company"], "source_year": task["source_year"],
                         "components": groups, "visible_scope_certified": False})
        local = Counter()
        for channel, candidates in groups.items():
            local[channel + "/semantic_values_including_unknown"] += len(candidates)
            local[channel + "/original_nodes_including_unknown"] += len(row[channel]) + 1
            for group in candidates:
                local[channel + "/witnesses_including_unresolved"] += len(group["witnesses"])
                located = [w for w in group["witnesses"] if w["anchor"] is not None]
                local[channel + "/values_with_located_witness"] += bool(located)
                local[channel + "/values_with_multiple_locations"] += len({w["anchor"]["anchor_id"] for w in located}) > 1
                local[channel + "/values_with_restored_unit_paths"] += any(w.get("restored_from_public_unit_and_literal_proposals") for w in group["witnesses"])
        counts.update(local)
        per_year[str(task["source_year"])].update(local)
        nfield = len(groups["field"])
        pair_counts.append(nfield * sum(len(v["witnesses"]) for c in CHANNELS[1:] for v in groups[c]))
    destination = DEST / "public/witnesses.json"
    save(destination, prepared)
    snapshot = DEST / "public/builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    engine = Path(__file__).with_name("semantic_star_v23.py")
    (DEST / "public/composition_engine_snapshot.py").write_bytes(engine.read_bytes())
    save(manifest_path, {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "targets": len(tasks),
        "counts": dict(counts), "counts_by_source_year": {k: dict(v) for k, v in per_year.items()},
        "maximum_field_leaf_witness_pairs": max(pair_counts),
        "mean_field_leaf_witness_pairs": sum(pair_counts) / len(pair_counts),
        "witness_inventory_sha256": sha(destination), "public_task_sha256": sha(task_file),
        "public_record_sha256": sha(record_file), "public_localization_sha256": sha(anchors_file),
        "feature_manifest_sha256": sha(feature_file), "localization_manifest_sha256": sha(localization_file),
        "builder_snapshot_sha256": sha(snapshot), "composition_engine_sha256": sha(engine),
        "all_original_semantic_value_proposals_preserved_without_teacher_insertions": True,
        "no_answer_values_added_from_reference_files": True,
        "all_controls_must_share_full_candidates_witnesses_and_exact_composition": True,
        "visual_occurrences_retained_as_latent_hypotheses_not_ownership_certificates": True,
        "every_value_retains_unresolved_visual_ownership": True,
        "no_nearest_anchor_selection_or_averaging_before_learning": True,
        "teacher_future_or_reserved_independent_references_read": False,
        "new_generative_visual_calls": 0, "human_runtime_confirmations": 0,
        "trained_model_or_new_accuracy_result": False,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False,
    })
    print("Frozen common semantic/witness inventory", len(tasks), dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

"""Common full-value/witness features, with each visible occurrence retained.

Reuse fixed v21 native OCR/pixels and v19 semantic node vectors. There are no
new visual API requests, answer-derived dates or per-method candidate limits.
Field glyph hypotheses form a shared pool; no field ID is linked to a glyph
using a publisher answer. Scope assignments remain uncertain.
"""

import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from native_binding_features_v17 import CHANNELS, extras
from native_witness_inventory_v23 import DEST, INPUT
from prepare import save, sha

ANCHOR_DIM, NODE_DIM = 112, 224


def anchor_geometry(box, target):
    result = np.zeros(16, dtype="float32")
    result[4:8] = target
    if box is not None:
        result[:4] = box
        result[8:12] = np.array(box) - np.array(target)
        result[12:] = [(box[0] + box[2] - target[0] - target[2]) / 2,
                       (box[1] + box[3] - target[1] - target[3]) / 2,
                       box[2] - box[0], box[3] - box[1]]
    return result


def anchor_vector(anchor, kind, target, words, vision_projection, text_projection, unresolved=False):
    result = np.zeros(ANCHOR_DIM, dtype="float32")
    box = anchor["box"] if anchor else None
    if anchor:
        selected = anchor["word_indices"]
        if not selected or min(selected) < 0 or max(selected) >= len(words):
            raise ValueError("Anchor's original OCR indices changed")
        source = words[selected].mean(0)
        visual = source[:768] @ vision_projection
        result[:32] = visual / max(float(np.linalg.norm(visual)), 1e-12)
        text = source[768:1152]
        result[32:80] = (text / max(float(np.linalg.norm(text)), 1e-12)) @ text_projection
    result[80:96] = anchor_geometry(box, target)
    kinds = {"field": 0, "period": 1, "unit": 2}
    result[96 + kinds.get(kind, 3)] = 1
    result[100] = anchor is not None
    result[101] = unresolved
    result[102] = anchor is None
    result[103] = bool(anchor and anchor.get("full_header_text_aligned"))
    return result


def witness_node(base, witness, group, channel, target, anchor_features, records):
    indices = witness["record_indices"] or group["source_record_indices"]
    if not indices:
        raise ValueError("A semantic witness lost its common base vector")
    original = base[indices[0]].copy()
    # Original boxes sometimes were whole observation windows. Use only a
    # located glyph box, with explicit unavailability for every other path.
    original[84:92] = 0
    if witness["anchor"]:
        box = np.asarray(witness["anchor"]["box"])
        original[84:88], original[88:92] = box, box - np.asarray(target)
    if channel == "unit_quantity" and witness.get("printed") is not None:
        row = records[group["source_record_indices"][0]]
        meta = {**row["metadata"], "printed": witness["printed"], "multiplier": witness["multiplier"],
                "semantic_sign": witness["semantic_sign"]}
        # One canonical amount may have several public literal/scale parses.
        # Keep each parse's numeric features with its own visual occurrence.
        numeric = extras({"value": row["value"], "metadata": meta}, channel, target)
        original[65:69] = numeric[401:405]
    return np.concatenate((original, anchor_features))


def prepare():
    folder = DEST / "features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Common witnessed features already frozen")
    inventory_file = DEST / "public/witnesses.json"
    inventory_meta = json.loads((DEST / "public/manifest.json").read_text())
    if not inventory_meta["complete"] or sha(inventory_file) != inventory_meta["witness_inventory_sha256"]:
        raise ValueError("Frozen common witness inventory required")
    old_file = INPUT / "features/manifest.json"
    old = json.loads(old_file.read_text())
    records_file, task_file = INPUT / "features/records.json", INPUT / "candidates/tasks.json"
    if sha(records_file) != inventory_meta["public_record_sha256"] or sha(task_file) != inventory_meta["public_task_sha256"]:
        raise ValueError("Original public candidates changed")
    localization_file = INPUT / "localization/public_anchors.json"
    if sha(localization_file) != inventory_meta["public_localization_sha256"]:
        raise ValueError("Original public anchor inventory changed")
    page_file = INPUT / "native_word_features/index.json"
    page_meta_file = INPUT / "native_word_features/manifest.json"
    page_meta = json.loads(page_meta_file.read_text())
    if not page_meta["complete"] or sha(page_file) != page_meta["index_sha256"]:
        raise ValueError("Frozen full-page word features required")
    inventory, records, tasks, localization, pages = [json.loads(p.read_text()) for p in (inventory_file, records_file, task_file, localization_file, page_file)]
    if not (len(inventory) == len(records) == len(tasks) == 840):
        raise ValueError("Full public inventory required")
    projection_file = INPUT / "features/projection.npy"
    if sha(projection_file) != old["files"][projection_file.name]:
        raise ValueError("Frozen semantic text projection changed")
    text_projection = np.load(projection_file)
    if text_projection.shape != (384, 48):
        raise ValueError("Pinned shared semantic projection required")
    vision_projection = np.random.default_rng(20261016).normal(0, 1 / np.sqrt(32), (768, 32)).astype("float32")
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "vision_projection.npy", vision_projection)
    shutil.copyfile(projection_file, folder / "text_projection.npy")
    context_file = INPUT / "features/context.npy"
    if sha(context_file) != old["files"][context_file.name]:
        raise ValueError("Common target crop context changed")
    shutil.copyfile(context_file, folder / "context.npy")
    counts, cached_pages = Counter(), {}
    for number, (item, rows, task) in enumerate(zip(inventory, records, tasks, strict=True), 1):
        ident = task["task_id"]
        if item["task_id"] != ident:
            raise ValueError("Public feature ordering differs")
        visible = localization[ident]
        page_hash = visible["page_hash"]
        if page_hash not in cached_pages:
            page = pages[page_hash]
            path = Path(page["feature_file"])
            if sha(path) != page["feature_sha256"]:
                raise ValueError("Original native OCR/pixel feature file changed")
            cached_pages[page_hash] = np.load(path)
        words = cached_pages[page_hash]
        arrays, pool = {}, []
        public_headers = visible["headers"]
        for header in public_headers.values():
            for anchor in header["anchors"] if header["may_supply_transfer_hypotheses"] else []:
                pool.append(anchor_vector(anchor, header["original_header"]["kind"], task["target_box"], words, vision_projection, text_projection))
        pool.append(anchor_vector(None, "unknown", task["target_box"], words, vision_projection, text_projection, unresolved=True))
        arrays["common_header_pool"] = np.stack(pool)
        path = INPUT / "features" / (ident + ".npz")
        if sha(path) != old["files"][path.name]:
            raise ValueError("Original shared semantic node features changed")
        with np.load(path) as original:
            for channel in CHANNELS:
                groups = item["components"][channel]
                base = original["node_" + channel]
                if channel == "field":
                    # Unique central semantic nodes; the shared header pool
                    # stays separate, available to every control's matcher.
                    arrays["field"] = np.stack([base[g["source_record_indices"][0]] for g in groups])
                    counts["field_nodes"] += len(groups)
                    continue
                nodes, group_ids = [], []
                for group in groups:
                    for witness in group["witnesses"]:
                        kind = public_headers[witness["header_id"]]["original_header"]["kind"] if witness["header_id"] else channel
                        encoded = anchor_vector(witness["anchor"], kind, task["target_box"], words,
                                                vision_projection, text_projection, witness["ownership"] == "unknown")
                        nodes.append(witness_node(base, witness, group, channel, task["target_box"], encoded, rows[channel]))
                        group_ids.append(group["semantic_group"])
                arrays[channel] = np.stack(nodes)
                arrays[channel + "_groups"] = np.array(group_ids, dtype="int64")
                counts[channel + "_witness_nodes"] += len(nodes)
        if any(not np.isfinite(v).all() for v in arrays.values()):
            raise ValueError("Nonfinite common witness features")
        np.savez_compressed(folder / (ident + ".npz"), **arrays)
        if number % 140 == 0:
            print("Common witnessed native features", number, "/ 840", flush=True)
    snapshot = folder / "feature_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "targets": 840,
        "field_dimension": 112, "leaf_witness_dimension": NODE_DIM, "header_pool_dimension": ANCHOR_DIM,
        "counts": dict(counts), "witness_inventory_sha256": sha(inventory_file),
        "witness_inventory_manifest_sha256": sha(DEST / "public/manifest.json"),
        "original_node_feature_manifest_sha256": sha(old_file), "native_word_feature_manifest_sha256": sha(page_meta_file),
        "files": {p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.name != "manifest.json"},
        "fixed_common_vision_and_text_encoders_reused_without_fitting": True,
        "raw_original_node_semantic_embedding_is_retained_with_separate_occurrence_evidence": True,
        "same_full_candidates_occurrences_contexts_and_composition_required_for_all_controls": True,
        "source_year_or_company_not_appended_as_feature_values": True,
        "teacher_future_or_independent_reference_files_read": False,
        "new_generative_visual_calls": 0, "human_runtime_confirmations": 0,
        "trained_model_or_accuracy_result": False, "native_visual_scope_certified": False,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False,
    })
    print("Common witnessed features complete", 840, dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

"""Shared weak year-link features and a discarded two-step engineering check.

This does not train/evaluate a full binding method or read future answers.
All 5,595 candidates remain in the frozen OCR input inventory. This feature
bank contains only the 1,308 explicitly labelled year-link cases; uncertain
header locations are never averaged into apparently known training examples.
"""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from native_grid_ocr_witnesses_v24 import DEST as SOURCE
from native_witness_features_v23 import anchor_vector
from native_witness_inventory_v23 import DEST as V23
from prepare import EXP, save, sha
from torch.nn import functional as F
from visual_scope_geometry_v23 import GenericVisualGeometry
from visual_year_scope_v24 import VisualYearScope

DEST = SOURCE / "geometry_features"
WORD_FEATURES = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/header_transfer_v21/native_word_features"


def labelled_anchor(row, word_vectors, visual_projection, text_projection):
    if len(row["header_word_indices"]) != 1 or not row["target_word_indices"] or row["target_box"] is None:
        raise ValueError("Weakly labelled geometry requires one header glyph and an observed target")
    anchor = {"word_indices": row["header_word_indices"], "box": row["header_box"],
              "full_header_text_aligned": False}
    return anchor_vector(anchor, "period", row["target_box"], word_vectors, visual_projection, text_projection)


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Common weak year-link features already frozen")
    manifest_file = SOURCE / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if not manifest["complete"]:
        raise ValueError("Full weak OCR-link inventory required")
    for filename, digest in manifest["files_sha256"].items():
        if sha(SOURCE / filename) != digest:
            raise ValueError("Weak input/teacher provenance differs")
    rows = json.loads((SOURCE / "common_inputs.json").read_text())
    teachers = json.loads((SOURCE / "weak_year_teacher.json").read_text())
    word_manifest = json.loads((WORD_FEATURES / "manifest.json").read_text())
    index_file = WORD_FEATURES / "index.json"
    if sha(index_file) != word_manifest["index_sha256"]:
        raise ValueError("Fixed common word features changed")
    word_index = json.loads(index_file.read_text())
    common = json.loads((V23 / "features/manifest.json").read_text())
    projections = {}
    for filename in ("vision_projection.npy", "text_projection.npy"):
        path = V23 / "features" / filename
        if sha(path) != common["files"][filename]:
            raise ValueError("Frozen common anchor projection changed")
        projections[filename] = np.load(path)
    cache, vectors, labels, metadata = {}, [], [], []
    for row in rows:
        label = teachers[row["pair_id"]]["weak_year_correspondence"]
        if label is None:
            continue
        if label not in (0, 1) or row["source_year"] != 2023:
            raise ValueError("Only observed historical binary year links may supervise")
        page_hash = row["page_hash"]
        if page_hash not in cache:
            record = word_index[page_hash]
            path = Path(record["feature_file"])
            if sha(path) != record["feature_sha256"]:
                raise ValueError("Original fixed native word feature matrix changed")
            cache[page_hash] = np.load(path)
        vectors.append(labelled_anchor(row, cache[page_hash], projections["vision_projection.npy"], projections["text_projection.npy"]))
        labels.append(label)
        metadata.append({k: row[k] for k in ("pair_id", "company", "source_year", "table_key", "page_hash", "header_key", "target_key")})
    inputs = np.stack(vectors)
    if inputs.shape != (1308, 112) or not np.isfinite(inputs).all() or len(labels) != manifest["counts"]["positive_year_edges"] + manifest["counts"]["negative_year_edges"]:
        raise ValueError("Common year-link feature denominator differs")
    DEST.mkdir(parents=True, exist_ok=True)
    np.save(DEST / "anchors.npy", inputs)
    np.save(DEST / "teacher_labels.npy", np.asarray(labels, dtype="int64"))
    save(DEST / "administrative_index.json", metadata)
    snapshot = DEST / "builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "manifest.json", {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "source_candidate_pairs": len(rows), "observed_binary_year_links": len(labels),
        "unknown_pairs_retained_in_source_inventory": len(rows) - len(labels), "anchor_dimension": 112,
        "source_weak_label_manifest_sha256": sha(manifest_file), "word_feature_manifest_sha256": sha(WORD_FEATURES / "manifest.json"),
        "v23_common_projection_manifest_sha256": sha(V23 / "features/manifest.json"),
        "reused_anchor_feature_builder_sha256": sha(Path(__file__).with_name("native_witness_features_v23.py")),
        "builder_snapshot_sha256": sha(snapshot),
        "files_sha256": {n: sha(DEST / n) for n in ("anchors.npy", "teacher_labels.npy", "administrative_index.json")},
        "administrative_company_year_ids_are_never_network_feature_values": True,
        "common_word_pixel_and_text_encoders_reused_without_refitting": True,
        "teacher_dom_coordinates_not_network_inputs": True,
        "weak_year_teacher_is_not_complete_financial_period_or_visual_ownership_certification": True,
        "formal_full_binding_training_started": False, "future_semantic_or_reserved_reference_files_read": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0})
    print("Common weak year-link geometry features", inputs.shape, "unknown candidates retained", len(rows) - len(labels), flush=True)


def preflight():
    output = DEST / "engineering_preflight.json"
    if output.exists():
        raise RuntimeError("Completed engineering preflight is frozen")
    torch.set_num_threads(2)
    manifest = json.loads((DEST / "manifest.json").read_text())
    for filename, digest in manifest["files_sha256"].items():
        if sha(DEST / filename) != digest:
            raise ValueError("Common weak year-link feature freeze differs")
    inputs = torch.from_numpy(np.load(DEST / "anchors.npy"))
    labels = torch.from_numpy(np.load(DEST / "teacher_labels.npy")).float()
    index = json.loads((DEST / "administrative_index.json").read_text())
    excluded_company = sorted({r["company"] for r in index})[0]
    active = [i for i, r in enumerate(index) if r["company"] != excluded_company and r["source_year"] == 2023]
    rng = np.random.default_rng(20261016)
    sequence = [rng.choice(active, 16, replace=False).tolist() for _ in range(2)]
    cases = [[index[i]["pair_id"] for i in batch] for batch in sequence]
    results = {}
    for name, constructor in (("generic_geometry", GenericVisualGeometry), ("oriented_year_membership", VisualYearScope)):
        torch.manual_seed(19)
        model = constructor()
        optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
        losses = []
        begin = time.perf_counter()
        for batch in sequence:
            optimizer.zero_grad()
            loss = F.binary_cross_entropy_with_logits(model(inputs[batch]), labels[batch])
            if not torch.isfinite(loss):
                raise ValueError("Nonfinite auxiliary loss")
            loss.backward()
            if any(p.grad is None or not torch.isfinite(p.grad).all() for p in model.parameters()):
                raise ValueError("Missing/nonfinite auxiliary gradient")
            optimizer.step()
            losses.append(float(loss.detach()))
        results[name] = {"optimizer_steps": 2, "sampled_cases": 32, "wall_seconds": time.perf_counter() - begin,
                         "active_parameters": sum(p.numel() for p in model.parameters()),
                         "sample_sequence_sha256": hashlib.sha256(json.dumps(cases).encode()).hexdigest(),
                         "training_losses_not_accuracy": losses}
    snapshot = DEST / "year_membership_implementation_snapshot.py"
    snapshot.write_bytes(Path(__file__).with_name("visual_year_scope_v24.py").read_bytes())
    save(output, {"complete": True, "checked_utc": datetime.now(timezone.utc).isoformat(),
        "excluded_company": excluded_company, "feature_manifest_sha256": sha(DEST / "manifest.json"),
        "models": results, "preflight_weights_discarded": True,
        "same_training_cases_and_optimizer_budget": True, "strict_flop_or_wall_time_equivalence_certified": False,
        "scope_bounds_do_not_take_target_coordinates_or_per_query_match_flags": True,
        "membership_is_weak_year_link_probability_not_complete_financial_scope_ownership": True,
        "implementation_snapshot_sha256": sha(snapshot), "runner_sha256": sha(__file__),
        "formal_full_binding_training_started": False, "new_accuracy_result": False,
        "future_semantic_or_reserved_reference_files_read": False,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Weak year-link auxiliary engineering check complete; weights discarded", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    if parser.parse_args().preflight:
        preflight()
    else:
        prepare()

"""Frozen FinTabNet donor inference on original-page OCR words and pixels.

Both donor architectures use identical public inputs. Image removal is a
weight-tied inference ablation, not a separately retrained donor. No native
semantic or scope reference is read and no transfer output certifies ownership.
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import fintab_scope_train_v10 as donor
import numpy as np
import torch
from encoder import MODEL
from fintab_scope_features_v10 import clipped, native_contexts
from fintab_scope_prepare_v10 import DEST as FINTAB
from fintab_scope_strong_controls_v2 import best_horizontal_span
from native_header_alignment_v21 import DEST
from PIL import Image
from pilot_features import TEXT_MODEL, encode_texts, pool
from prepare import save, sha
from torch import nn
from transformers import LayoutLMv3ImageProcessor, LayoutLMv3Model

PIXEL_MODES = ("full_pixels", "pixels_zeroed")


def prepare_features():
    torch.set_num_threads(2)
    folder = DEST / "native_word_features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Native transfer features already frozen")
    manifest_file = DEST / "localization/manifest.json"
    manifest = json.loads(manifest_file.read_text())
    pages_file = DEST / "localization/public_pages.json"
    if not manifest["complete"] or sha(pages_file) != manifest["public_pages_sha256"]:
        raise ValueError("Frozen lexical localization required")
    pages = json.loads(pages_file.read_text())
    language = {key: native_contexts(page["words"]) for key, page in pages.items()}
    strings = sorted({s for rows in language.values() for s in rows})
    folder.mkdir(parents=True, exist_ok=True)
    cache, text_meta = folder / "text_embeddings.npz", folder / "text_cache.json"
    if cache.exists() and text_meta.exists():
        meta = json.loads(text_meta.read_text())
        if meta["strings"] != strings or meta["cache_sha256"] != sha(cache):
            raise ValueError("Native transfer text cache changed")
        with np.load(cache) as data:
            embeddings = dict(zip(strings, data["features"], strict=True))
    else:
        embeddings = encode_texts(strings)
        np.savez_compressed(cache, features=np.stack([embeddings[s] for s in strings]))
        save(text_meta, {"strings": strings, "cache_sha256": sha(cache), "native_teacher_files_read": False})
    provenance_file = MODEL / "provenance.json"
    provenance = json.loads(provenance_file.read_text())
    if any(sha(MODEL / name) != digest for name, digest in provenance["files"].items()):
        raise ValueError("Pinned shared visual encoder changed")
    vision = LayoutLMv3Model.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False).eval()
    processor = LayoutLMv3ImageProcessor.from_pretrained(MODEL, local_files_only=True, apply_ocr=False)
    index = {}
    for i, (key, page) in enumerate(sorted(pages.items()), 1):
        if sha(page["image"]) != key or sha(page["ocr_path"]) != page["ocr_sha256"]:
            raise ValueError("Original native page or OCR changed")
        feature_file = folder / "pages" / (key + ".npy")
        patch_file = folder / "patches" / (key + ".npy")
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        if patch_file.exists():
            patches = np.load(patch_file)
        else:
            with Image.open(page["image"]) as image:
                if image.size != (page["width"], page["height"]):
                    raise ValueError("Original page and OCR geometry differ")
                pixels = processor(images=image.convert("RGB"), return_tensors="pt")
            with torch.no_grad():
                patches = vision(pixel_values=pixels["pixel_values"]).last_hidden_state[0].numpy()
            np.save(patch_file, patches.astype("float32"))
        if patches.shape != (197, 768) or not np.isfinite(patches).all():
            raise ValueError("Invalid original-page patch features")
        rows = []
        for word, context in zip(page["words"], language[key], strict=True):
            visual = pool(patches, clipped(word["box"]))
            visual /= max(float(np.linalg.norm(visual)), 1e-8)
            rows.append(np.concatenate((visual, embeddings[context], np.asarray(word["box"], dtype="float32"))))
        values = np.asarray(rows, dtype="float32").reshape(-1, 1156)
        feature_file.parent.mkdir(parents=True, exist_ok=True)
        np.save(feature_file, values)
        index[key] = {"feature_file": str(feature_file), "feature_sha256": sha(feature_file),
                      "patch_sha256": sha(patch_file), "words": len(values), "image_sha256": key}
        save(folder / "progress.json", {"pages_prepared": i, "total": len(pages), "updated_utc": datetime.now(timezone.utc).isoformat()})
        if i % 10 == 0:
            print("Native transfer word features", i, "/", len(pages), flush=True)
    save(folder / "index.json", index)
    snapshot = folder / "feature_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "pages": len(index),
        "words": sum(v["words"] for v in index.values()), "dimension": 1156,
        "index_sha256": sha(folder / "index.json"), "localization_manifest_sha256": sha(manifest_file),
        "public_pages_sha256": sha(pages_file), "text_cache_sha256": sha(text_meta),
        "visual_encoder_provenance_sha256": sha(provenance_file),
        "text_encoder_sha256": {p.name: sha(p) for p in TEXT_MODEL.iterdir() if p.is_file()},
        "feature_builder_sha256": sha(snapshot),
        "whole_original_pages_not_gold_table_boundaries": True,
        "full_page_native_ocr_is_new_common_input_for_all_controls": True,
        "native_or_independent_teacher_files_read": False, "new_generative_visual_calls": 0,
    })


def load_donors():
    if sha(donor.__file__) != sha(FINTAB / "learning_v1/trainer_snapshot.py"):
        raise ValueError("Frozen donor architecture/training code changed")
    result, hashes = {}, {}
    for seed in donor.SEEDS:
        for method in ("generic", "scope"):
            if method == "generic":
                folder = FINTAB / "strong_controls_v2" / str(seed)
                model = donor.CoverageModel("generic_joint")
                model.pair = nn.Sequential(nn.Linear(2 * donor.HIDDEN + 12, 111), nn.GELU(), nn.Linear(111, 1))
            else:
                folder = FINTAB / "learning_v1/learned_rectangle_scope" / str(seed)
                model = donor.CoverageModel("learned_rectangle_scope")
            metadata = json.loads((folder / "manifest.json").read_text())
            weight = folder / "weights.pt"
            if not metadata["complete"] or sha(weight) != metadata["weights_sha256"]:
                raise ValueError("Frozen donor checkpoint changed")
            model.load_state_dict(torch.load(weight, map_location="cpu", weights_only=True))
            result[(seed, method)] = model.eval()
            hashes[str(weight)] = sha(weight)
    return result, hashes


def infer():
    torch.set_num_threads(2)
    folder = DEST / "donor_predictions"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Native donor predictions already frozen")
    feature_meta = json.loads((DEST / "native_word_features/manifest.json").read_text())
    index_file = DEST / "native_word_features/index.json"
    if not feature_meta["complete"] or sha(index_file) != feature_meta["index_sha256"]:
        raise ValueError("Complete frozen common native features required")
    localization_meta = json.loads((DEST / "localization/manifest.json").read_text())
    anchor_file = DEST / "localization/public_anchors.json"
    if sha(anchor_file) != localization_meta["public_anchors_sha256"]:
        raise ValueError("Frozen header anchors changed")
    tasks = json.loads(anchor_file.read_text())
    index = json.loads(index_file.read_text())
    groups = defaultdict(list)
    for ident, task in tasks.items():
        groups[task["page_hash"]].append(ident)
    models, hashes = load_donors()
    design = {"donor_weight_sha256": hashes, "native_feature_manifest_sha256": sha(DEST / "native_word_features/manifest.json"),
              "localization_manifest_sha256": sha(DEST / "localization/manifest.json"),
              "seeds": list(donor.SEEDS), "pixel_modes": list(PIXEL_MODES),
              "generic_has_capacity_matching_and_optimal_span_readout": True,
              "pixel_removal_is_weight_tied_inference_ablation_not_donor_retraining": True,
              "all_lexical_ambiguity_retained_and_eligible_locations_averaged": True,
              "column_header_precedence_is_a_transfer_domain_assumption_not_native_gold": True,
              "donor_and_native_legal_entity_template_overlap_not_certified": True,
              "native_teacher_or_independent_labels_read": False, "runner_sha256": sha(__file__)}
    design_file = folder / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Native transfer donor design changed")
    save(design_file, design)
    counts = Counter()
    for number, (key, identifiers) in enumerate(sorted(groups.items()), 1):
        output = folder / "pages" / (key + ".json")
        if output.exists():
            previous = json.loads(output.read_text())
            if previous["design_sha256"] != sha(design_file) or set(previous["targets"]) != set(identifiers):
                raise ValueError("Frozen transfer predictions changed")
            continue
        record = index[key]
        if sha(record["feature_file"]) != record["feature_sha256"]:
            raise ValueError("Common native word features changed")
        full = torch.from_numpy(np.load(record["feature_file"]))
        boxes = full[:, -4:]
        cy = (boxes[:, 1] + boxes[:, 3]) / 2
        result = {ident: {"by_seed": {str(s): {m: {} for m in PIXEL_MODES} for s in donor.SEEDS},
                           "scope_certified": False} for ident in identifiers}
        with torch.no_grad():
            for (seed, method), model in models.items():
                for mode in PIXEL_MODES:
                    visible = full.clone()
                    if mode == "pixels_zeroed":
                        visible[:, :768] = 0
                    mean = visible.mean(0, keepdim=True)
                    nodes = torch.cat([model.nodes(visible[i:i + donor.WINDOW], mean) for i in range(0, len(visible), donor.WINDOW)])
                    header_probability = torch.sigmoid(model.header(nodes).flatten())
                    edge_cache = {}
                    for ident in identifiers:
                        task = tasks[ident]
                        targets = task["target_word_indices"]
                        for hid, header in task["headers"].items():
                            if header["original_header"]["kind"] not in {"period", "unit"}:
                                continue
                            row = result[ident]["by_seed"][str(seed)][mode].setdefault(hid, {})
                            locations = []
                            if targets and header["may_supply_transfer_hypotheses"]:
                                for anchor in header["anchors"]:
                                    words = anchor["word_indices"]
                                    if min(float(cy[t]) for t in targets) <= max(float(cy[h]) for h in words):
                                        continue
                                    joint, span = [], []
                                    for h in words:
                                        if h not in edge_cache:
                                            eligible = torch.nonzero(cy > cy[h], as_tuple=True)[0].tolist()
                                            pairs = torch.tensor([[h, t] for t in eligible], dtype=torch.long)
                                            logits = model.edges(nodes, boxes, pairs).numpy()
                                            probability = dict(zip(eligible, 1 / (1 + np.exp(-np.clip(logits, -50, 50))), strict=True))
                                            selected = {eligible[j] for j in best_horizontal_span(boxes[eligible].numpy(), logits)} if method == "generic" else set()
                                            edge_cache[h] = probability, selected
                                        probability, selected = edge_cache[h]
                                        hp = float(header_probability[h])
                                        joint.extend(hp * float(probability[t]) for t in targets)
                                        if method == "generic":
                                            span.extend(hp * float(t in selected) for t in targets)
                                    locations.append({"anchor_id": anchor["anchor_id"], "joint_probability": float(np.mean(joint)),
                                                      "optimal_span_joint_probability": float(np.mean(span)) if span else None})
                            row[method] = {"locations": locations, "available_transfer_hypothesis": bool(locations),
                                           "ambiguous_lexical_location": header["status"] == "ambiguous_lexical_locations",
                                           "mean_joint_probability": float(np.mean([v["joint_probability"] for v in locations])) if locations else None,
                                           "mean_optimal_span_joint_probability": float(np.mean([v["optimal_span_joint_probability"] for v in locations])) if method == "generic" and locations else None,
                                           "native_scope_label_or_certificate": False}
        save(output, {"complete": True, "design_sha256": sha(design_file), "targets": result})
        counts["new_pages_inferred"] += 1
        save(folder / "progress.json", {"pages_inferred": number, "total": len(groups), "updated_utc": datetime.now(timezone.utc).isoformat()})
        print("Frozen native scope transfer", number, "/", len(groups), flush=True)
    files = sorted((folder / "pages").glob("*.json"))
    if len(files) != len(groups):
        raise ValueError("Complete original-page prediction inventory required")
    save(folder / "manifest.json", {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "pages": len(files), "targets": len(tasks), "design_sha256": sha(design_file),
        "files_sha256": {p.name: sha(p) for p in files}, "native_scope_certified": False,
        "native_semantic_or_independent_reference_files_read": False, "human_runtime_confirmations": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("features", "inference"), required=True)
    arguments = parser.parse_args()
    prepare_features() if arguments.stage == "features" else infer()

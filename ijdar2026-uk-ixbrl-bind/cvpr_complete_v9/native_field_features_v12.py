"""Same candidate/context features for no-oracle native field learning."""

import json
from pathlib import Path

import numpy as np
import torch

from crop_candidates_v3 import DEST as OLD_CROPS
from native_field_retrieval_v12 import DEST, field_text
from pilot_features import encode_texts
from prepare import save, sha


def context_text(task):
    obs = task.get("common_visual_observation") or {}
    pieces = [obs.get("row_text", "unknown"), task["public_row_text"], obs.get("section_text", "unknown"),
              obs.get("presentation", "unknown"), obs.get("quantity_role", "unknown")]
    pieces += [h["kind"] + " " + h["text"] for h in obs.get("headers", [])]
    return " | ".join(pieces)[:2500]


def properties(card):
    kind = (card.get("type") or "").lower()
    return [float(t in kind) for t in ("monetary", "pershare", "shares", "integer", "decimal")]


def prepare():
    torch.set_num_threads(2)
    folder = DEST / "features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Native field features already frozen")
    file = DEST / "public/tasks.json"
    retrieval = json.loads((DEST / "manifest.json").read_text())
    if not retrieval["complete"] or sha(file) != retrieval["public_target_sha256"]:
        raise ValueError("Frozen native report-local retrieval changed")
    tasks = json.loads(file.read_text())
    texts = sorted({context_text(t) for t in tasks} | {field_text(c) for t in tasks for c in t["field_candidates"]})
    embeddings = encode_texts(texts)
    vectors, contexts, ids = {}, [], []
    crop_manifest = OLD_CROPS / "features/manifest.json"
    old = json.loads(crop_manifest.read_text())
    if not old["actual_crop_pixels_not_full_page_single_patch_proxy"]:
        raise ValueError("Original-pixel shared crop feature provenance missing")
    for task in tasks:
        crop = Path(task["crop_image"])
        if sha(crop) != task["crop_image_sha256"]:
            raise ValueError("Original pointed crop changed")
        patch = OLD_CROPS / "features/crop_patches" / (task["crop_image_sha256"] + ".npy")
        array = np.load(patch)
        if array.shape != (197, 768) or not np.isfinite(array).all():
            raise ValueError("Shared frozen crop patch shape/values changed")
        visual = array.mean(0)
        visual /= max(float(np.linalg.norm(visual)), 1e-8)
        contexts.append(np.concatenate((embeddings[context_text(task)], visual, task["target_box"])).astype("float32"))
        cards = []
        for card in task["field_candidates"]:
            metadata = (properties(card)
                        + [float(card.get("balance") == k) for k in ("debit", "credit", None)]
                        + [float(card.get("period_type") == k) for k in ("instant", "duration", None)]
                        + [float(card.get("abstract") is True), float(card.get("abstract") is None)])
            cards.append(np.concatenate((embeddings[field_text(card)], metadata, card["retrieval_scores"], [0])).astype("float32"))
        null = np.zeros_like(cards[0])
        null[-1] = 1
        vectors[task["task_id"]] = np.stack(cards + [null])
        ids.append(task["task_id"])
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "context.npy", np.stack(contexts))
    np.savez_compressed(folder / "candidates.npz", **vectors)
    save(folder / "ids.json", ids)
    (folder / "feature_snapshot.py").write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "targets": len(tasks), "context_dimension": len(contexts[0]),
        "field_dimension": next(iter(vectors.values())).shape[1],
        "files": {name: sha(folder / name) for name in ("context.npy", "candidates.npz", "ids.json", "feature_snapshot.py")},
        "public_tasks_sha256": sha(file), "retrieval_manifest_sha256": sha(DEST / "manifest.json"),
        "shared_crop_provenance_sha256": sha(crop_manifest),
        "reused_pixel_only_encoder_patches_not_old_field_or_scale_conditioned_responses": True,
        "target_field_period_unit_value_or_teacher_files_read": False,
        "all_models_receive_the_same_source_local_schema_types_and_visible_context": True,
        "administrative_source_year_company_and_target_labels_not_appended_as_features": True,
        "visible_entity_names_and_report_dates_may_remain_in_native_text_features": True,
        "field_semantic_classification_not_full_visible_evidence_binding": True,
    })
    print("Frozen native field features", len(tasks), flush=True)


if __name__ == "__main__":
    prepare()

"""Shared field/context/candidate features, without any reference labels.

Both generic and compositional controls receive exactly the same blocks,
including declared types, local scope proposals, crop pixels and base decisions.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from transformers import LayoutLMv3ImageProcessor, LayoutLMv3Model

from canonicalization_observe import PRESENTATIONS
from crop_candidates_v3 import DEST, observation_hypotheses
from crop_observe_v3 import ROLES
from encoder import MODEL
from pilot_features import encode_texts, TEXT_MODEL
from prepare import save, sha

PRESENTATION_ORDER = sorted(PRESENTATIONS)
QUANTITY_ROLE_ORDER = sorted(ROLES)


def candidate_features(task, observation):
    base = observation.get("candidate_index")
    selected = task["candidates"][base] if base is not None else None
    source_sets = [observation_hypotheses(task["ocr_strings"])]
    source_sets += [
        observation_hypotheses(o["strings"]) for o in task["crop_OCR_hypotheses"]
    ]
    rows = []
    for index, candidate in enumerate(task["candidates"]):
        literal = candidate["literal"]
        value = float(literal)
        rows.append(
            [
                float(candidate["polarity"] == 1),
                float(value < 0),
                float(value == 0),
                float("." in literal and not literal.rstrip("0").endswith(".")),
                *[float(literal in s) for s in source_sets],
                float(base == index),
                float(selected is not None and selected["literal"] == literal),
                float(
                    selected is not None
                    and selected["polarity"] == candidate["polarity"]
                ),
                float(base is None),
                float(observation.get("uncertain", True)),
            ]
        )
    return rows


def field_categorical(props):
    known = props["declaration_status"] == "exact_public_schema"
    return (
        [float(known and props["balance"] == x) for x in ("debit", "credit", None)]
        + [float(not known)]
        + [float(props["period_type"] == x) for x in ("instant", "duration", None)]
        + [
            float(token in (props.get("type") or "").lower())
            for token in ("monetary", "pershare", "percent", "integer", "decimal")
        ]
    )


def context_categorical(task, observation):
    target = task["target_box"]
    box = observation.get("context_box")
    relative = [0.0, 0.0, 0.0, 0.0]
    if box is not None:
        relative = [
            (target[0] - box[0]) / (box[2] - box[0]),
            (target[1] - box[1]) / (box[3] - box[1]),
            (target[2] - box[0]) / (box[2] - box[0]),
            (target[3] - box[1]) / (box[3] - box[1]),
        ]
    return (
        [float(observation["presentation"] == x) for x in PRESENTATION_ORDER]
        + [float(observation["quantity_role"] == x) for x in QUANTITY_ROLE_ORDER]
        + [float(box is not None), float(observation["uncertain"])]
        + relative
        + target
    )


def texts(task, observation):
    props = task["field_properties"]
    field = " ".join(
        str(props.get(k) or "(unknown)")
        for k in (
            "name",
            "namespace",
            "type",
            "balance",
            "period_type",
            "declaration_status",
        )
    )
    context = " ".join(
        [
            observation["row_text"],
            observation["section_text"],
            observation["presentation"],
            observation["quantity_role"],
            task["public_row_text"],
        ]
    )
    return field, context


def prepare():
    torch.set_num_threads(2)
    task_file = DEST / "public/tasks.json"
    tasks = json.loads(task_file.read_text())
    mf = DEST / "common_visual/manifest.json"
    manifest = json.loads(mf.read_text())
    if not manifest["complete"] or manifest["public_tasks_sha256"] != sha(task_file):
        raise RuntimeError("Complete same-crop observations required")
    observations = {}
    for record in manifest["records"]:
        if record["status"] == "completed":
            for row in record["targets"]:
                if row["task_id"] in observations:
                    raise RuntimeError("Duplicate common observed target")
                observations[row["task_id"]] = row
    for task in tasks:
        observations.setdefault(
            task["task_id"],
            {
                "task_id": task["task_id"],
                "candidate_index": None,
                "row_text": "",
                "section_text": "",
                "presentation": "unknown",
                "quantity_role": "unknown",
                "context_box": None,
                "uncertain": True,
                "common_observation_failed": True,
            },
        )
    dest = DEST / "features"
    if (dest / "manifest.json").exists():
        raise RuntimeError("Shared features already frozen")
    language = {t["task_id"]: texts(t, observations[t["task_id"]]) for t in tasks}
    embeddings = encode_texts(sorted({v for pair in language.values() for v in pair}))
    provenance = json.loads((MODEL / "provenance.json").read_text())
    if any(sha(MODEL / name) != digest for name, digest in provenance["files"].items()):
        raise RuntimeError("Pinned common visual weights changed")
    encoder = LayoutLMv3Model.from_pretrained(
        MODEL, local_files_only=True, trust_remote_code=False
    ).eval()
    processor = LayoutLMv3ImageProcessor.from_pretrained(
        MODEL, local_files_only=True, apply_ocr=False
    )
    fields = []
    contexts = []
    candidates = {}
    inputs = {}
    ids = []
    for i, task in enumerate(tasks):
        obs = observations[task["task_id"]]
        field, context = language[task["task_id"]]
        crop = Path(task["crop_image"])
        if sha(crop) != task["crop_image_sha256"]:
            raise RuntimeError("Shared original crop changed")
        cache = dest / "crop_patches" / (task["crop_image_sha256"] + ".npy")
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            patch = np.load(cache)
        else:
            with Image.open(crop) as im:
                x = processor(images=im.convert("RGB"), return_tensors="pt")
            with torch.no_grad():
                patch = (
                    encoder(pixel_values=x["pixel_values"]).last_hidden_state[0].numpy()
                )
            np.save(cache, patch.astype("float32"))
        if patch.shape != (197, 768) or not np.isfinite(patch).all():
            raise RuntimeError("Invalid common crop patch representation")
        visual = patch.mean(0)
        visual /= max(float(np.linalg.norm(visual)), 1e-8)
        f = np.concatenate(
            [
                embeddings[field],
                np.asarray(
                    field_categorical(task["field_properties"]), dtype="float32"
                ),
            ]
        )
        c = np.concatenate(
            [
                embeddings[context],
                visual,
                np.asarray(context_categorical(task, obs), dtype="float32"),
            ]
        )
        fields.append(f)
        contexts.append(c)
        ids.append(task["task_id"])
        candidates[task["task_id"]] = candidate_features(task, obs)
        inputs[str(crop)] = task["crop_image_sha256"]
        if (i + 1) % 100 == 0:
            print(
                "Common typed/local/crop features", i + 1, "/", len(tasks), flush=True
            )
    np.save(dest / "field.npy", np.stack(fields).astype("float32"))
    np.save(dest / "context.npy", np.stack(contexts).astype("float32"))
    save(dest / "ids.json", ids)
    save(dest / "candidates.json", candidates)
    save(dest / "observations.json", observations)
    save(
        dest / "manifest.json",
        {
            "tasks": len(tasks),
            "field_dimension": len(fields[0]),
            "context_dimension": len(contexts[0]),
            "candidate_dimension": 12,
            "files": {
                name: sha(dest / name)
                for name in (
                    "field.npy",
                    "context.npy",
                    "ids.json",
                    "candidates.json",
                    "observations.json",
                )
            },
            "same_blocks_including_typed_and_scope_information_available_to_all_controls": True,
            "actual_crop_pixels_not_full_page_single_patch_proxy": True,
            "visual_encoder_provenance": provenance,
            "text_encoder_files": {
                f.name: sha(f) for f in TEXT_MODEL.iterdir() if f.is_file()
            },
            "public_task_sha256": sha(task_file),
            "common_visual_manifest_sha256": sha(mf),
            "crop_image_hashes": inputs,
            "labels_or_feedback_files_read": False,
            "scope_box_and_presentation_roles_are_uncertified_hypotheses": True,
            "human_confirmations": 0,
            "runner_sha256": sha(__file__),
        },
    )


if __name__ == "__main__":
    prepare()

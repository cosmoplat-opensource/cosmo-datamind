"""Common native-word/text/visual features; no annotated table grid is read."""

import json
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from transformers import LayoutLMv3ImageProcessor, LayoutLMv3Model

from encoder import MODEL
from fintab_scope_prepare_v10 import DEST
from pilot_features import TEXT_MODEL, encode_texts, pool
from prepare import save, sha


def native_contexts(words):
    """Build horizontal visible context without publisher cell/row labels."""
    result = []
    for word in words:
        box = word["box"]
        y = (box[1] + box[3]) / 2
        height = max(box[3] - box[1], 1e-4)
        line = [w for w in words if abs((w["box"][1] + w["box"][3]) / 2 - y)
                < 0.4 * max(height, w["box"][3] - w["box"][1])]
        line.sort(key=lambda w: w["box"][0])
        result.append(word["text"] + " | " + " ".join(w["text"] for w in line)[:800])
    return result


def clipped(box):
    box = [min(1.0, max(0.0, float(v))) for v in box]
    if box[0] >= box[2] or box[1] >= box[3]:
        return None
    return box


def prepare():
    torch.set_num_threads(2)
    manifest = json.loads((DEST / "manifest.json").read_text())
    public_file = DEST / "public/tables.json"
    if not manifest["complete"] or sha(public_file) != manifest["hashes"]["public/tables.json"]:
        raise RuntimeError("Frozen shared FinTabNet native inputs changed")
    folder = DEST / "features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("FinTabNet common features already frozen")
    tables = json.loads(public_file.read_text())
    language = {t["table_id"]: native_contexts(t["native_words"]) for t in tables}
    strings = sorted({s for v in language.values() for s in v})
    text_cache = folder / "text_embeddings.npz"
    strings_file = folder / "text_strings.json"
    folder.mkdir(parents=True, exist_ok=True)
    if text_cache.exists() and strings_file.exists():
        if json.loads(strings_file.read_text()) != strings:
            raise RuntimeError("Frozen native text inventory changed")
        matrix = np.load(text_cache)["features"]
        embeddings = dict(zip(strings, matrix))
    else:
        embeddings = encode_texts(strings)
        np.savez_compressed(text_cache, features=np.stack([embeddings[s] for s in strings]))
        save(strings_file, strings)
    provenance = json.loads((MODEL / "provenance.json").read_text())
    if any(sha(MODEL / f) != h for f, h in provenance["files"].items()):
        raise RuntimeError("Pinned shared visual encoder changed")
    vision = LayoutLMv3Model.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False).eval()
    processor = LayoutLMv3ImageProcessor.from_pretrained(MODEL, local_files_only=True, apply_ocr=False)
    index = []
    for i, table in enumerate(tables, 1):
        image = Path(table["image"])
        if sha(image) != table["image_sha256"]:
            raise RuntimeError("Native table image changed")
        cache = folder / "patches" / (table["image_sha256"] + ".npy")
        cache.parent.mkdir(parents=True, exist_ok=True)
        if cache.exists():
            patches = np.load(cache)
        else:
            with Image.open(image) as im:
                x = processor(images=im.convert("RGB"), return_tensors="pt")
            with torch.no_grad():
                patches = vision(pixel_values=x["pixel_values"]).last_hidden_state[0].numpy()
            np.save(cache, patches.astype("float32"))
        if patches.shape != (197, 768) or not np.isfinite(patches).all():
            raise RuntimeError("Invalid shared native image features")
        features = []
        for word, context in zip(table["native_words"], language[table["table_id"]]):
            box = word["box"]
            visual = pool(patches, clipped(box))
            visual /= max(float(np.linalg.norm(visual)), 1e-8)
            features.append(np.concatenate((visual, embeddings[context], np.array(box, dtype="float32"))))
        file = folder / "tables" / (table["table_id"] + ".npy")
        file.parent.mkdir(parents=True, exist_ok=True)
        np.save(file, np.asarray(features, dtype="float32").reshape(-1, 1156))
        index.append({"table_id": table["table_id"], "split": table["split"],
                      "issuer_id": table["issuer_id"], "words": len(features),
                      "file": str(file), "sha256": sha(file), "image_sha256": table["image_sha256"]})
        save(folder / "progress.json", {"tables": i, "expected": len(tables)})
        if i % 25 == 0:
            print("Shared native header-coverage features", i, "/", len(tables), flush=True)
    save(folder / "index.json", index)
    snapshot = folder / "feature_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "tables": len(index), "words": sum(r["words"] for r in index),
        "dimension": 1156, "public_input_sha256": sha(public_file),
        "index_sha256": sha(folder / "index.json"),
        "feature_snapshot_sha256": sha(snapshot),
        "text_embeddings_sha256": sha(text_cache), "text_strings_sha256": sha(strings_file),
        "visual_encoder": provenance,
        "text_encoder_files": {f.name: sha(f) for f in TEXT_MODEL.iterdir() if f.is_file()},
        "same_visible_features_available_to_all_models": True,
        "annotated_rows_columns_cells_headers_or_coverage_labels_read": False,
        "native_table_pixels_unchanged": True,
        "table_crop_localization_is_shared_publisher_assistance": True,
        "native_words_are_publisher_text_not_pixel_OCR": True,
        "human_runtime_confirmations": 0,
    })


if __name__ == "__main__":
    prepare()

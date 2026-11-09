"""Held-out companies, step B: OCR (2x upscale) and word features for the rendered pages.

Produces heldout_v1/localization/public_pages.json keyed by image sha256 (same page record
layout as the development perception) and heldout_v1/native_word_features/{pages,patches,index.json}
with the same LayoutLMv3 + MiniLM recipe as native_scope_transfer_v21 / native_v30_perception.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from encoder import MODEL
from fintab_scope_features_v10 import clipped, native_contexts
from PIL import Image
from pilot_features import encode_texts, pool
from prepare import save, sha
from strict_native_ocr import process
from transformers import LayoutLMv3ImageProcessor, LayoutLMv3Model

DEST = Path(os.environ.get("HELDOUT_ROOT", str(Path(__file__).resolve().parents[1] / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))


def main():
    if (DEST / "localization/manifest.json").exists():
        raise RuntimeError("heldout perception already frozen")
    renders = json.loads((DEST / "renders/manifest.json").read_text())
    pages, page_of = {}, {}
    (DEST / "ocr").mkdir(parents=True, exist_ok=True)
    for report, info in renders["reports"].items():
        for p in json.loads(Path(info["pages_file"]).read_text()):
            key = p["image_sha256"]
            ocr_file = DEST / "ocr" / report / (p["page_id"] + ".ocr.json")
            ocr_file.parent.mkdir(parents=True, exist_ok=True)
            upscaled = ocr_file.with_suffix(".x2.png")
            with Image.open(p["image"]) as im:
                width, height = im.size
                if not upscaled.exists():
                    im.convert("RGB").resize((im.width * 2, im.height * 2), Image.LANCZOS).save(upscaled)
            result = process(upscaled, ocr_file.with_suffix(".tsv"))
            result["width"], result["height"], result["ocr_upscale_factor"] = width, height, 2
            save(ocr_file, result)
            pages[key] = {"image": p["image"], "image_sha256": key, "ocr_path": str(ocr_file), "ocr_sha256": sha(ocr_file), "width": width, "height": height,
                          "words": result["words"], "report": report, "page_id": p["page_id"]}
            page_of[(report, p["page_id"])] = key
            print("OCR", report, p["page_id"], len(result["words"]), "words", flush=True)
    (DEST / "localization").mkdir(exist_ok=True)
    save(DEST / "localization/public_pages.json", pages)
    save(DEST / "localization/page_keys.json", {report + "/" + pid: key for (report, pid), key in page_of.items()})
    torch.set_num_threads(4)
    folder = DEST / "native_word_features"
    (folder / "pages").mkdir(parents=True, exist_ok=True)
    (folder / "patches").mkdir(parents=True, exist_ok=True)
    language = {key: native_contexts(page["words"]) for key, page in pages.items()}
    strings = sorted({s for rows in language.values() for s in rows})
    print("Encoding", len(strings), "word contexts", flush=True)
    embeddings = encode_texts(strings)
    vision = LayoutLMv3Model.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False).eval()
    processor = LayoutLMv3ImageProcessor.from_pretrained(MODEL, local_files_only=True, apply_ocr=False)
    index = {}
    for key, page in pages.items():
        with Image.open(page["image"]) as image:
            pixels = processor(images=image.convert("RGB"), return_tensors="pt")
        with torch.no_grad():
            patches = vision(pixel_values=pixels["pixel_values"]).last_hidden_state[0].numpy().astype("float32")
        np.save(folder / "patches" / (key + ".npy"), patches)
        rows = []
        for word, context in zip(page["words"], language[key], strict=True):
            visual = pool(patches, clipped(word["box"]))
            visual /= max(float(np.linalg.norm(visual)), 1e-8)
            rows.append(np.concatenate((visual, embeddings[context], np.asarray(word["box"], dtype="float32"))))
        values = np.asarray(rows, dtype="float32").reshape(-1, 1156)
        feature_file = folder / "pages" / (key + ".npy")
        np.save(feature_file, values)
        index[key] = {"feature_file": str(feature_file), "feature_sha256": sha(feature_file), "patch_sha256": sha(folder / "patches" / (key + ".npy")), "words": len(values), "image_sha256": key}
    save(folder / "index.json", index)
    save(DEST / "localization/manifest.json", {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "pages": len(pages),
                                                 "public_pages_sha256": sha(DEST / "localization/public_pages.json"), "word_feature_index_sha256": sha(folder / "index.json"),
                                                 "render_manifest_sha256": sha(DEST / "renders/manifest.json"), "builder_sha256": sha(Path(__file__)),
                                                 "reference_teacher_or_reserved_files_read": False})
    print("heldout perception frozen:", len(pages), "pages", flush=True)


if __name__ == "__main__":
    main()

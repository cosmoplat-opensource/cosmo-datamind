"""v30 perception patch, step 2: OCR and word features for the re-rendered pages.

Builds `perception_v30/` with the same layout the v26 bank and the v27-v29 priors
read from `header_transfer_v21`: localization/public_pages.json (177 pages; the
re-rendered ones carry new words, image and OCR paths under their original key),
localization/public_anchors.json (header hypotheses on re-rendered pages are
marked unusable because their word indices refer to the old OCR), and
native_word_features/ (new LayoutLMv3 + MiniLM word rows for the re-rendered pages,
all other pages pointing at the frozen v21 arrays). candidates/, features/ and
learning_full_transfer/ are symlinks to v21. No reference or teacher file is read.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from encoder import MODEL
from fintab_scope_features_v10 import clipped, native_contexts
from native_header_alignment_v21 import DEST as ALIGN
from PIL import Image
from pilot_features import encode_texts, pool
from prepare import save, sha
from strict_native_ocr import process
from transformers import LayoutLMv3ImageProcessor, LayoutLMv3Model

DEST = ALIGN.parent / "perception_v30"


def main():
    if (DEST / "localization/manifest.json").exists():
        raise RuntimeError("perception_v30 already frozen")
    renders = json.loads((DEST / "renders/manifest.json").read_text())
    bad = [k for k, v in renders["pages"].items() if not v["size_matches_original"]]
    if bad:
        raise ValueError("Re-rendered page size differs from the original for " + str(bad))
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((ALIGN / "localization/public_anchors.json").read_text())
    index = json.loads((ALIGN / "native_word_features/index.json").read_text())
    (DEST / "localization").mkdir(parents=True, exist_ok=True)
    (DEST / "ocr").mkdir(exist_ok=True)
    replaced = {}
    for key, info in renders["pages"].items():
        page = pages[key]
        ocr_file = DEST / "ocr" / info["report"] / (info["page_id"] + ".ocr.json")
        ocr_file.parent.mkdir(parents=True, exist_ok=True)
        # small-font landscape pages: OCR a 2x upscaled copy; parse_tsv normalises boxes so coordinates are unchanged
        upscaled = ocr_file.with_suffix(".x2.png")
        with Image.open(info["new_image"]) as im:
            im.convert("RGB").resize((im.width * 2, im.height * 2), Image.LANCZOS).save(upscaled)
        result = process(upscaled, ocr_file.with_suffix(".tsv"))
        result["width"], result["height"] = page["width"], page["height"]
        result["ocr_upscale_factor"] = 2
        save(ocr_file, result)
        replaced[key] = {"old_words": len(page["words"]), "new_words": len(result["words"]), "image": info["new_image"]}
        pages[key] = {**page, "image": info["new_image"], "image_sha256": info["new_sha256"], "ocr_path": str(ocr_file),
                      "ocr_sha256": sha(ocr_file), "words": result["words"], "replaced_render_v30": True, "original_key": key}
        print("OCR", info["report"], info["page_id"], replaced[key]["old_words"], "->", replaced[key]["new_words"], "words", flush=True)
    for task_id, item in anchors.items():
        if item["page_hash"] in replaced:
            for header in item["headers"].values():
                header["may_supply_transfer_hypotheses"] = False
                header["stale_word_indices_after_rerender_v30"] = True
    save(DEST / "localization/public_pages.json", pages)
    save(DEST / "localization/public_anchors.json", anchors)

    # word features for the replaced pages only (same recipe as native_scope_transfer_v21.prepare_features)
    torch.set_num_threads(4)
    folder = DEST / "native_word_features"
    (folder / "pages").mkdir(parents=True, exist_ok=True)
    (folder / "patches").mkdir(parents=True, exist_ok=True)
    language = {key: native_contexts(pages[key]["words"]) for key in replaced}
    strings = sorted({s for rows in language.values() for s in rows})
    print("Encoding", len(strings), "word contexts", flush=True)
    embeddings = encode_texts(strings)
    vision = LayoutLMv3Model.from_pretrained(MODEL, local_files_only=True, trust_remote_code=False).eval()
    processor = LayoutLMv3ImageProcessor.from_pretrained(MODEL, local_files_only=True, apply_ocr=False)
    new_index = dict(index)
    for key in replaced:
        page = pages[key]
        with Image.open(page["image"]) as image:
            if image.size != (page["width"], page["height"]):
                raise ValueError("Re-rendered page and OCR geometry differ")
            pixels = processor(images=image.convert("RGB"), return_tensors="pt")
        with torch.no_grad():
            patches = vision(pixel_values=pixels["pixel_values"]).last_hidden_state[0].numpy().astype("float32")
        if patches.shape != (197, 768) or not np.isfinite(patches).all():
            raise ValueError("Invalid patch features")
        np.save(folder / "patches" / (key + ".npy"), patches)
        rows = []
        for word, context in zip(page["words"], language[key], strict=True):
            visual = pool(patches, clipped(word["box"]))
            visual /= max(float(np.linalg.norm(visual)), 1e-8)
            rows.append(np.concatenate((visual, embeddings[context], np.asarray(word["box"], dtype="float32"))))
        values = np.asarray(rows, dtype="float32").reshape(-1, 1156)
        feature_file = folder / "pages" / (key + ".npy")
        np.save(feature_file, values)
        new_index[key] = {"feature_file": str(feature_file), "feature_sha256": sha(feature_file), "patch_sha256": sha(folder / "patches" / (key + ".npy")),
                          "words": len(values), "image_sha256": page["image_sha256"], "replaced_render_v30": True}
        print("features", key[:12], values.shape, flush=True)
    save(folder / "index.json", new_index)
    for name in ("candidates", "features", "learning_full_transfer"):
        link = DEST / name
        if not link.exists():
            os.symlink(ALIGN / name, link)
    save(DEST / "localization/manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "base": str(ALIGN),
        "replaced_pages": replaced, "public_pages_sha256": sha(DEST / "localization/public_pages.json"),
        "public_anchors_sha256": sha(DEST / "localization/public_anchors.json"), "word_feature_index_sha256": sha(folder / "index.json"),
        "render_manifest_sha256": sha(DEST / "renders/manifest.json"), "builder_sha256": sha(Path(__file__)),
        "reference_teacher_or_reserved_files_read": False})
    print("perception_v30 frozen:", len(replaced), "pages replaced", flush=True)


if __name__ == "__main__":
    main()

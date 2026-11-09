"""Literal Tesseract TSV parsing: visual quotation marks are ordinary text.

Preserve previous OCR and predictions. Recompute every shared native page under
the same image/PSM settings and retain raw TSV for reproducibility. No source
semantics, region labels, private answers, or method scores are read.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import io
import json
import math
from pathlib import Path
import subprocess

from PIL import Image

from prepare import OUT, save, sha


def parse_tsv(raw, width, height):
    rows = csv.DictReader(io.StringIO(raw), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "left", "top", "width", "height", "conf", "text"}
    if not required.issubset(rows.fieldnames or []):
        raise ValueError("Unexpected native OCR TSV columns")
    words = []
    for row in rows:
        if None in row or any(row[k] is None for k in required):
            raise ValueError("Malformed native OCR TSV record")
        if int(row["level"]) != 5 or not row["text"].strip():
            continue
        x, y, dx, dy = (int(row[k]) for k in ("left", "top", "width", "height"))
        confidence = float(row["conf"])
        if not math.isfinite(confidence) or not -1 <= confidence <= 100:
            raise ValueError("Invalid OCR confidence")
        if x < 0 or y < 0 or dx <= 0 or dy <= 0 or x + dx > width or y + dy > height:
            raise ValueError("OCR word outside original image")
        words.append(
            {
                "text": row["text"],
                "box": [
                    round(x / width, 4),
                    round(y / height, 4),
                    round((x + dx) / width, 4),
                    round((y + dy) / height, 4),
                ],
                "confidence": confidence,
            }
        )
    return words


def process(image, raw_file, psm=3):
    raw_file = Path(raw_file)
    if raw_file.exists():
        raw = raw_file.read_text()
    else:
        proc = subprocess.run(
            ["tesseract", str(image), "stdout", "--psm", str(psm), "tsv"],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        raw = proc.stdout
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_text(raw)
    with Image.open(image) as im:
        width, height = im.size
    return {
        "width": width,
        "height": height,
        "words": parse_tsv(raw, width, height),
        "engine": "tesseract --psm " + str(psm),
        "image_sha256": sha(image),
    }


def main():
    root = OUT / "expanded_development_sources"
    pages_file = root / "public_feature_pages.json"
    pages = json.loads(pages_file.read_text())
    previous_manifest_file = root / "common_perception/manifest.json"
    previous = {
        (r["report"], r["page"]): r
        for r in json.loads(previous_manifest_file.read_text())["pages"]
    }
    dest = root / "common_perception_v2_tsv"
    records = []
    failures = []
    changes = []

    def job(page):
        image = Path(page["image"])
        if sha(image) != page["image_sha256"]:
            raise RuntimeError("Frozen source image changed")
        target = (
            dest / page["report_id"] / ("page-" + str(page["page_index"]) + ".ocr.json")
        )
        raw_file = target.with_suffix(".tsv")
        if target.exists():
            observation = json.loads(target.read_text())
            if observation["image_sha256"] != page["image_sha256"]:
                raise RuntimeError("Cached corrected OCR image changed")
        else:
            observation = process(image, raw_file)
            save(target, observation)
        old_file = Path(previous[page["report_id"], page["page_index"]]["ocr_path"])
        old = json.loads(old_file.read_text())
        changed = observation["words"] != old["words"]
        record = {
            "report": page["report_id"],
            "page": page["page_index"],
            "image_path": str(image),
            "image_sha256": page["image_sha256"],
            "ocr_path": str(target),
            "ocr_sha256": sha(target),
            "raw_tsv_sha256": sha(raw_file),
            "width": observation["width"],
            "height": observation["height"],
            "words": len(observation["words"]),
            "previous_ocr_sha256": sha(old_file),
            "public_words_changed": changed,
            "previous_malformed_merged_words": sum(
                "\t" in w["text"] or "\n" in w["text"] for w in old["words"]
            ),
        }
        return record

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(job, p): p for p in pages}
        for future in as_completed(futures):
            try:
                record = future.result()
                records.append(record)
                if record["public_words_changed"]:
                    changes.append(record)
            except Exception as exc:
                failures.append(
                    {
                        "page_id": futures[future]["page_id"],
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
            save(
                dest / "progress.json",
                {
                    "completed_pages": len(records),
                    "expected_pages": len(pages),
                    "failures": failures,
                },
            )
            if (len(records) + len(failures)) % 20 == 0:
                print(
                    "Strict shared native OCR",
                    len(records),
                    "/",
                    len(pages),
                    "failures",
                    len(failures),
                    flush=True,
                )
    save(
        dest / "manifest.json",
        {
            "pages": sorted(records, key=lambda r: (r["report"], r["page"])),
            "expected_pages": len(pages),
            "completed_pages": len(records),
            "failures": failures,
            "changed_pages": changes,
            "literal_quote_handling": "csv.QUOTE_NONE",
            "raw_TSV_preserved": True,
            "same_native_images_PSM_engine_for_all_methods": True,
            "public_image_manifest_sha256": sha(pages_file),
            "previous_native_ocr_manifest_sha256": sha(previous_manifest_file),
            "private_annotation_or_method_outcomes_opened": False,
            "human_confirmations": 0,
            "tesseract_version": subprocess.run(
                ["tesseract", "--version"], capture_output=True, text=True, check=True
            ).stdout,
            "source_code_sha256": sha(__file__),
        },
    )
    if failures:
        raise RuntimeError(
            "Strict common OCR failures retained; do not silently discard sources"
        )
    print(
        "Strict shared OCR complete:",
        len(records),
        "changed pages",
        len(changes),
        flush=True,
    )


if __name__ == "__main__":
    main()

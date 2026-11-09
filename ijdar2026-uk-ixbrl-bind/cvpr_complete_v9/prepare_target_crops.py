"""Common original-pixel target crops for every assisted development task.

Every target receives the same crop and two native OCR modes. No failures are
selected for special treatment and no reference values resolve OCR hypotheses.
This remains an oracle-region diagnostic, not an unassisted visual benchmark.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json

from PIL import Image

from canonicalization_prepare import ROOT
from prepare import OUT, save, sha
from strict_native_ocr import process

SOURCE = OUT / "canonicalization_isolation_v2_full_cohort"
DEST = OUT / "canonicalization_crop_frontend_v3"


def crop_box(box, width, height):
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("Target outside original pixels")
    return [
        max(0, int(x0 * width) - 16),
        max(0, int(y0 * height) - 4),
        min(width, int(x1 * width + 0.999999) + 16),
        min(height, int(y1 * height + 0.999999) + 4),
    ]


def main():
    tasks_file = SOURCE / "public/tasks.json"
    tasks = json.loads(tasks_file.read_text())
    strict_manifest_file = ROOT / "common_perception_v2_tsv/manifest.json"
    strict = json.loads(strict_manifest_file.read_text())
    if strict["completed_pages"] != 211 or strict["failures"]:
        raise RuntimeError("Complete corrected shared source OCR required")
    index = {(r["report"], r["page"]): r for r in strict["pages"]}
    page_index = {
        p["page_id"]: p["page_index"]
        for p in json.loads((ROOT / "public_feature_pages.json").read_text())
    }
    design = {
        "tasks": len(tasks),
        "source_tasks_sha256": sha(tasks_file),
        "fixed_horizontal_padding_px": 16,
        "fixed_vertical_padding_px": 4,
        "OCR_PSM_modes": [7, 13],
        "crop_image_encoding": "lossless PNG from original RGB pixels",
        "image_rescaling_or_annotations_added": False,
        "all_targets_receive_the_same_processing": True,
        "target_region_is_an_explicit_common_oracle_input": True,
        "reference_answers_or_source_publisher_transforms_read": False,
        "strict_source_ocr_manifest_sha256": sha(strict_manifest_file),
        "human_confirmations": 0,
        "runner_sha256": sha(__file__),
    }
    design_file = DEST / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise RuntimeError("Frozen shared crop design changed")
    save(design_file, design)
    records = []
    failures = []

    def job(task):
        if sha(task["image"]) != task["image_sha256"]:
            raise RuntimeError("Original report pixels changed")
        file = DEST / "public/crops" / (task["task_id"] + ".png")
        file.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(task["image"]) as original:
            box = crop_box(task["target_box"], original.width, original.height)
            crop = original.convert("RGB").crop(box)
            if file.exists():
                with Image.open(file) as previous:
                    if (
                        previous.mode != "RGB"
                        or previous.size != crop.size
                        or previous.tobytes() != crop.tobytes()
                    ):
                        raise RuntimeError(
                            "Cached crop differs from source pixel rectangle"
                        )
            else:
                crop.save(file, format="PNG")
        observations = []
        for psm in (7, 13):
            target = DEST / "public/crop_ocr" / f"{task['task_id']}_psm{psm}.json"
            raw = target.with_suffix(".tsv")
            if target.exists():
                observed = json.loads(target.read_text())
                if observed["image_sha256"] != sha(file) or not raw.exists():
                    raise RuntimeError("Frozen common crop OCR provenance differs")
            else:
                observed = process(file, raw, psm=psm)
                save(target, observed)
            observations.append(
                {
                    "psm": psm,
                    "ocr_path": str(target),
                    "ocr_sha256": sha(target),
                    "raw_tsv_sha256": sha(raw),
                }
            )
        original_ocr = index[task["report"], page_index[task["page_id"]]]
        return {
            "task_id": task["task_id"],
            "original_image_sha256": task["image_sha256"],
            "original_pixel_crop_box": box,
            "crop_path": str(file),
            "crop_sha256": sha(file),
            "crop_pixels_exactly_match_original_rectangle": True,
            "native_crop_ocr": observations,
            "strict_full_page_ocr": original_ocr,
            "original_public_task_unchanged": True,
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(job, task): task["task_id"] for task in tasks}
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except Exception as exc:
                failures.append(
                    {
                        "task_id": futures[future],
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
            save(
                DEST / "progress.json",
                {
                    "completed_targets": len(records),
                    "expected_targets": len(tasks),
                    "failures": failures,
                },
            )
            if (len(records) + len(failures)) % 100 == 0:
                print(
                    "Shared original-pixel target crops",
                    len(records),
                    "/",
                    len(tasks),
                    "failures",
                    len(failures),
                    flush=True,
                )
    save(
        DEST / "manifest.json",
        {
            "complete": len(records) == len(tasks) and not failures,
            "records": sorted(records, key=lambda r: r["task_id"]),
            "failures": failures,
            "expected_targets": len(tasks),
            "completed_targets": len(records),
            "design_sha256": sha(design_file),
            "reference_or_private_numeric_annotations_read": False,
            "no_target_selected_by_model_correctness": True,
            "human_confirmations": 0,
        },
    )
    if failures:
        raise RuntimeError("Incomplete shared crops; preserve every failure")


if __name__ == "__main__":
    main()

"""Uniform original-pixel row/column context; no answer-conditioned cropping.

All pointed targets receive the same rule, including failed/ambiguous targets.
Row and column crops preserve source pixels without annotations or resizing.
They use the shared annotated target ROI, never field/period/quantity references.
"""

import json
from pathlib import Path
import shutil

from PIL import Image

from native_field_retrieval_v11 import DEST as SOURCE
from prepare import save, sha

DEST = SOURCE.parent / "native_context_crops_v12"


def context_boxes(target):
    x0, y0, x1, y1 = target
    if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
        raise ValueError("Original target outside source page")
    height, width = y1-y0, x1-x0
    # Row halo covers nearby row labels/notes; column strip retains preceding
    # headers. These are observation windows, not certified ownership ranges.
    row = [0.0, max(0.0, y0-max(.03, 4*height)), 1.0, min(1.0, y1+max(.03, 4*height))]
    column = [max(0.0, x0-max(.08, 3*width)), 0.0,
              min(1.0, x1+max(.08, 3*width)), min(1.0, y1+max(.03, 4*height))]
    return row, column


def pixel_box(box, width, height):
    return [max(0, int(box[0]*width)), max(0, int(box[1]*height)),
            min(width, int(box[2]*width+.999999)), min(height, int(box[3]*height+.999999))]


def prepare():
    if (DEST / "protocol.json").exists():
        raise RuntimeError("Uniform native context inputs already frozen")
    tasks_file = SOURCE / "public/tasks.json"
    tasks = json.loads(tasks_file.read_text())
    public = []
    for i, task in enumerate(tasks, 1):
        if shutil.disk_usage(DEST.parent).free < 512*1024**2:
            raise RuntimeError("Insufficient source-preserving disk reserve")
        if sha(task["image"]) != task["image_sha256"]:
            raise ValueError("Unchanged source page hash differs")
        row, column = context_boxes(task["target_box"])
        new = {k: task[k] for k in ("task_id", "company", "report", "source_year", "page_id", "image", "image_sha256",
                                   "target_box", "crop_image", "crop_image_sha256", "native_ocr_strings",
                                   "crop_ocr_hypotheses", "public_row_text", "public_top_text", "field_candidates")}
        with Image.open(task["image"]) as original:
            for kind, box in (("row", row), ("column", column)):
                rect = pixel_box(box, original.width, original.height)
                path = DEST / "public/crops" / (task["task_id"] + "_" + kind + ".png")
                path.parent.mkdir(parents=True, exist_ok=True)
                crop = original.convert("RGB").crop(rect)
                if path.exists():
                    with Image.open(path) as old:
                        if old.size != crop.size or old.tobytes() != crop.tobytes():
                            raise ValueError("Cached context pixels differ from source rectangle")
                else:
                    crop.save(path, format="PNG")
                new[kind+"_context_image"] = str(path)
                new[kind+"_context_sha256"] = sha(path)
                new[kind+"_context_box"] = [rect[0]/original.width, rect[1]/original.height,
                                             rect[2]/original.width, rect[3]/original.height]
        public.append(new)
        if i % 100 == 0:
            print("Uniform native row/column contexts", i, "/", len(tasks), flush=True)
    save(DEST / "public/tasks.json", public)
    (DEST / "preparer_snapshot.py").write_bytes(Path(__file__).read_bytes())
    save(DEST / "protocol.json", {
        "targets": len(tasks), "original_public_tasks_sha256": sha(tasks_file),
        "hashes": {"public_tasks": sha(DEST / "public/tasks.json")},
        "only_common_pointed_geometry_defines_crops": True,
        "all_targets_receive_identical_rule_not_error_selected_processing": True,
        "no_resizing_annotation_or_image_synthesis": True,
        "crop_geometry_is_not_header_ownership_gold": True,
        "no_target_field_scale_quantity_teacher_or_scoring_files_read": True,
        "new_response_and_image_token_budget_will_be_shared_by_all_controls": True,
        "prior_no_oracle_visual_calls_were_used_only_for_shared_field_card_retrieval": True,
        "human_runtime_confirmations": 0, "runner_sha256": sha(__file__),
    })


if __name__ == "__main__":
    prepare()

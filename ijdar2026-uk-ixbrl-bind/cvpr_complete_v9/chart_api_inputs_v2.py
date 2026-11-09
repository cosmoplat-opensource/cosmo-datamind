"""Uniform, reference-blind DeepSeek image-size adaptation for ChartQAPro."""

import json
import math

from PIL import Image
from prepare import OUT, save, sha
from public_benchmarks import ROOT

DEST = OUT / "chart_native_api_corrected_v2"
CAP = 8192


def adapted_size(width, height, cap=CAP):
    if min(width, height, cap) <= 0:
        raise ValueError("Invalid image dimensions")
    factor = min(1.0, cap / max(width, height))
    return max(1, math.floor(width * factor)), max(1, math.floor(height * factor))


def prepare():
    root = ROOT / "chartqapro"
    tasks_file = root / "public/tasks.json"
    tasks = json.loads(tasks_file.read_text())
    if len(tasks) != 1948 or len({t["task_id"] for t in tasks}) != 1948:
        raise RuntimeError("Complete original public inventory required")
    design = {
        "maximum_single_image_side": CAP,
        "implementation": "preserve original bytes within limit; aspect-preserving LANCZOS resize and PNG only above limit",
        "official_reference": "https://api-docs.deepseek.com/guides/vision/",
        "policy_applied_to_every_public_image_independent_of_API_status_or_answer": True,
        "original_files_modified": False,
        "same_image_adapter_required_for_all_later_comparison_conditions": True,
        "public_tasks_sha256": sha(tasks_file),
        "runner_sha256": sha(__file__),
        "private_reference_or_scoring_files_read": False,
    }
    frozen = DEST / "input_design.json"
    if frozen.exists() and json.loads(frozen.read_text()) != design:
        raise RuntimeError("Frozen uniform image policy changed")
    save(frozen, design)
    images = {}
    for task in tasks:
        ident = task["image_sha256"]
        if ident in images:
            continue
        original = root / task["image"]
        if sha(original) != ident:
            raise RuntimeError("Original image changed")
        with Image.open(original) as im:
            im.load()
            size = adapted_size(*im.size)
            changed = size != im.size
            target = DEST / "adapted_images" / (ident + ".png") if changed else original
            if changed:
                target.parent.mkdir(parents=True, exist_ok=True)
                im.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(
                    target, "PNG"
                )
            images[ident] = {
                "original_sha256": ident,
                "original_size": list(im.size),
                "api_image": str(target),
                "api_image_sha256": sha(target),
                "api_image_size": list(size),
                "changed": changed,
            }
    manifest = {
        "complete": True,
        "questions": len(tasks),
        "unique_image_content_hashes": len(images),
        "changed_unique_image_hashes": sum(i["changed"] for i in images.values()),
        "changed_input_question_ids": [
            t["task_id"] for t in tasks if images[t["image_sha256"]]["changed"]
        ],
        "images": images,
        "input_design_sha256": sha(frozen),
        "public_tasks_sha256": sha(tasks_file),
        "private_reference_or_scoring_files_read": False,
    }
    path = DEST / "input_manifest.json"
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise RuntimeError("Frozen adapted image inventory changed")
    save(path, manifest)
    print(
        "Uniform API images",
        len(images),
        "changed images",
        manifest["changed_unique_image_hashes"],
        "changed questions",
        len(manifest["changed_input_question_ids"]),
        flush=True,
    )


if __name__ == "__main__":
    prepare()

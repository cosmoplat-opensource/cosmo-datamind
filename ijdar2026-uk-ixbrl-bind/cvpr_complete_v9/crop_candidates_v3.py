"""Prepare every assisted target under identical strict/full/crop observations.

No reference answer resolves locale, signs or missing numbers. The original
840 target IDs and 0/5/10 feedback prefixes are retained unchanged.
"""

from decimal import Decimal
import json
from pathlib import Path
import re
import shutil

from canonicalization_prepare import nearest_public_text
from prepare import OUT, save, sha
from qualify_teacher_cells import overlap_fraction

SOURCE = OUT / "canonicalization_isolation_v2_full_cohort"
CROPS = OUT / "canonicalization_crop_frontend_v3"
DEST = OUT / "canonicalization_crop_learning_v3"


def number_hypotheses(text):
    if not isinstance(text, str):
        return []
    raw = text.strip().replace("−", "-").replace("–", "-")
    raw = re.sub(r"^[$£€]\s*", "", raw)
    raw = re.sub(
        r"\s*(?:%|p|million|millions|thousand|thousands|billion|billions)\s*$",
        "",
        raw,
        flags=re.I,
    )
    raw = re.sub(r"\s+", "", raw).replace("'", "")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    values = set()
    # Both decimal/grouping conventions are public hypotheses. A private
    # publisher transform or reference quantity must not pick the convention.
    for decimal, grouping in ((".", ","), (",", ".")):
        group = re.escape(grouping)
        dec = re.escape(decimal)
        pattern = r"[+-]?(?:\d{1,3}(?:" + group + r"\d{3})+|\d+)(?:" + dec + r"\d+)?"
        if re.fullmatch(pattern, raw) or re.fullmatch(r"[+-]?" + dec + r"\d+", raw):
            value = Decimal(raw.replace(grouping, "").replace(decimal, "."))
            values.add(-value if negative else value)
    return sorted(str(v) for v in values)


def observation_hypotheses(strings):
    values = set(number_hypotheses(" ".join(strings)))
    if not values:
        for text in strings:
            values.update(number_hypotheses(text))
    return values


def prepare():
    protocol_file = DEST / "protocol.json"
    if protocol_file.exists():
        raise RuntimeError("Crop-learning public inputs already frozen")
    crop_manifest_file = CROPS / "manifest.json"
    manifest = json.loads(crop_manifest_file.read_text())
    if (
        not manifest["complete"]
        or manifest["failures"]
        or manifest["completed_targets"] != 840
    ):
        raise RuntimeError("Complete common crops required")
    crop_index = {r["task_id"]: r for r in manifest["records"]}
    source_file = SOURCE / "public/tasks.json"
    original = json.loads(source_file.read_text())
    if (
        sha(source_file)
        != json.loads((CROPS / "design.json").read_text())["source_tasks_sha256"]
    ):
        raise RuntimeError("Original target pool changed")
    tasks = []
    evidence_hashes = {}
    empty = 0
    multiple = 0
    for task in original:
        record = crop_index[task["task_id"]]
        if (
            sha(record["crop_path"]) != record["crop_sha256"]
            or record["original_image_sha256"] != task["image_sha256"]
        ):
            raise RuntimeError("Common crop changed")
        native = record["strict_full_page_ocr"]
        file = Path(native["ocr_path"])
        if sha(file) != native["ocr_sha256"]:
            raise RuntimeError("Frozen strict native words changed")
        data = json.loads(file.read_text())
        width, height = data["width"], data["height"]
        box = [
            v * (width if j % 2 == 0 else height)
            for j, v in enumerate(task["target_box"])
        ]
        padded = [box[0] - 12, box[1] - 12, box[2] + 12, box[3] + 12]
        words = [
            w
            for w in data["words"]
            if overlap_fraction(
                padded,
                [v * (width if j % 2 == 0 else height) for j, v in enumerate(w["box"])],
            )
            >= 0.5
        ]
        words.sort(key=lambda w: (round(w["box"][1] / 0.005), w["box"][0]))
        strings = [w["text"] for w in words]
        literals = observation_hypotheses(strings)
        crop_observations = []
        for entry in record["native_crop_ocr"]:
            file = Path(entry["ocr_path"])
            if sha(file) != entry["ocr_sha256"]:
                raise RuntimeError("Common native crop OCR changed")
            observation = json.loads(file.read_text())
            text = [w["text"] for w in observation["words"]]
            literals.update(observation_hypotheses(text))
            crop_observations.append({"psm": entry["psm"], "strings": text})
            evidence_hashes[str(file)] = sha(file)
        candidates = []
        for literal in sorted(literals):
            for polarity in (1, -1):
                candidates.append(
                    {
                        "literal": literal,
                        "polarity": polarity,
                        "computed_answer": str(
                            Decimal(literal)
                            * Decimal(task["oracle_multiplier"])
                            * polarity
                        ),
                    }
                )
        row_text, top_text = nearest_public_text(data["words"], task["target_box"])
        tasks.append(
            dict(
                task,
                ocr_strings=strings,
                candidates=candidates,
                public_row_text=row_text,
                public_top_text=top_text,
                crop_image=record["crop_path"],
                crop_image_sha256=record["crop_sha256"],
                crop_OCR_hypotheses=crop_observations,
                shared_headers_are_uncertified_pre_correction_hypotheses=True,
            )
        )
        empty += not literals
        multiple += len(literals) > 1
    if {t["task_id"] for t in tasks} != {t["task_id"] for t in original}:
        raise RuntimeError("A task was dropped or introduced")
    save(DEST / "public/tasks.json", tasks)
    for relative in (
        "feedback_prefixes.json",
        "training_only/past_feedback.json",
        "scoring_only/references.json",
    ):
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)
    save(
        protocol_file,
        {
            "stage": "equal-crop assisted display-direction development",
            "selected_tasks": len(tasks),
            "past_targets": 280,
            "future_development_targets": 560,
            "feedback_year_only": 2023,
            "evaluation_years": [2024, 2025],
            "automatic_feedback_budgets": [0, 5, 10],
            "human_confirmations": 0,
            "all_controls_same_original_pages_crops_OCR_schema_and_candidates": True,
            "oracle_field_region_multiplier_shared_by_all_controls": True,
            "not_native_public_QA_or_full_semantic_binding_test": True,
            "no_private_numeric_reference_used_for_candidate_generation": True,
            "locale_and_missing_quantity_ambiguity_retained": True,
            "empty_candidate_targets_retained": empty,
            "multiple_numeric_hypothesis_targets": multiple,
            "same_original_target_IDs_and_feedback_prefixes": True,
            "source_tasks_sha256": sha(source_file),
            "crop_manifest_sha256": sha(crop_manifest_file),
            "public_tasks_sha256": sha(DEST / "public/tasks.json"),
            "feedback_prefixes_sha256": sha(DEST / "feedback_prefixes.json"),
            "evidence_hashes": evidence_hashes,
            "runner_sha256": sha(__file__),
        },
    )
    print(
        "Frozen equal-crop public inputs:",
        len(tasks),
        "empty numeric pools",
        empty,
        "ambiguous pools",
        multiple,
        flush=True,
    )


if __name__ == "__main__":
    prepare()

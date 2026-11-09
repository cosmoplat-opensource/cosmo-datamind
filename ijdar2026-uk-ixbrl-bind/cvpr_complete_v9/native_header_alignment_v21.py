"""Locate proposed header text in frozen native OCR, preserving ambiguity.

Observation windows are not used as glyph boxes. Only public lexical matches
create anchors; semantic teachers, source years and target answers are absent.
Anchors remain hypotheses, never certified header ownership.
"""

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from crop_candidates_v3 import CROPS
from native_field_retrieval_v12 import DEST as SOURCE
from prepare import opaque, save, sha

DEST = SOURCE.parent.parent / "header_transfer_v21"
MAX_ANCHORS = 32
MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"


def tokens(text):
    # Superscript footnotes are not calendar digits. Do not repair OCR letters
    # into currency signs, infer a date, or turn a dash into a numeric zero.
    clean = "".join(c for c in text if unicodedata.category(c) != "No")
    clean = unicodedata.normalize("NFKC", clean).casefold()
    return re.findall(r"[^\W\d_]+|\d+|[£€$¥%]", clean)


def lexical_levels(header):
    text = header["text"]
    full = tokens(text)
    if not full or text.casefold().strip() in {"unknown", "null", "n/a"}:
        return []
    levels = [("full_visible_header_text", [full])]
    if header["kind"] == "period":
        dates = re.findall(r"\b\d{1,2}\s+" + MONTH + r"\s+\d{4}\b|\b\d{4}-\d{2}-\d{2}\b", text, flags=re.IGNORECASE)
        if dates:
            levels.append(("visible_date_substring", [tokens(s) for s in dates]))
        years = re.findall(r"(?<!\d)(?:18|19|20|21|22)\d{2}(?!\d)", text)
        if years:
            levels.append(("visible_year_substring_not_complete_period", [tokens(s) for s in years]))
    elif header["kind"] == "unit":
        matches = re.findall(r"(?:US\$|A\$|C\$|NZ\$|[£€$¥]|\b(?:GBP|EUR|USD|AUD|CAD|NZD)\b)\s*(?:millions?|thousands?|billions?|000(?:['’]s)?|mn|bn|m|k)?", text, flags=re.IGNORECASE)
        if matches:
            levels.append(("visible_unit_substring_not_verified_multiplier", [tokens(s) for s in matches]))
    return levels


def text_lines(words):
    order = sorted(range(len(words)), key=lambda i: ((words[i]["box"][1] + words[i]["box"][3]) / 2, words[i]["box"][0]))
    lines = []
    for index in order:
        box = words[index]["box"]
        y, height = (box[1] + box[3]) / 2, box[3] - box[1]
        if not lines or abs(y - lines[-1]["y"]) >= .4 * max(height, lines[-1]["height"]):
            lines.append({"y": y, "height": height, "words": [index]})
        else:
            line = lines[-1]
            line["y"] = (line["y"] * len(line["words"]) + y) / (len(line["words"]) + 1)
            line["height"] = max(line["height"], height)
            line["words"].append(index)
    result = []
    for line in lines:
        sequence = []
        for index in sorted(line["words"], key=lambda i: words[i]["box"][0]):
            sequence.extend((token, index) for token in tokens(words[index]["text"]))
        if sequence:
            result.append(sequence)
    return result


def align_header(header, words, lines=None):
    lines = text_lines(words) if lines is None else lines
    for basis, variants in lexical_levels(header):
        locations = {}
        for query in variants:
            if not query:
                continue
            for line in lines:
                text = [v[0] for v in line]
                for start in range(len(text) - len(query) + 1):
                    if text[start:start + len(query)] != query:
                        continue
                    indices = tuple(dict.fromkeys(v[1] for v in line[start:start + len(query)]))
                    boxes = [words[i]["box"] for i in indices]
                    locations[indices] = {
                        "word_indices": list(indices),
                        "box": [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)],
                        "anchor_text": " ".join(words[i]["text"] for i in indices),
                        "basis": basis,
                        "full_header_text_aligned": basis == "full_visible_header_text",
                        "header_ownership_certified": False,
                    }
        if locations:
            overflow = len(locations) > MAX_ANCHORS
            anchors = list(locations.values())[:MAX_ANCHORS]
            return {"status": "overflow_unknown" if overflow else "unique_lexical_location" if len(anchors) == 1 else "ambiguous_lexical_locations",
                    "match_count_before_cap": len(locations), "anchors": anchors,
                    "may_supply_transfer_hypotheses": not overflow,
                    "scope_or_semantic_identity_certified": False}
    return {"status": "unmatched_unknown", "match_count_before_cap": 0, "anchors": [],
            "may_supply_transfer_hypotheses": False, "scope_or_semantic_identity_certified": False}


def same_box(a, b):
    return max(abs(x - y) for x, y in zip(a, b, strict=True)) < 1e-6


def target_words(words, box):
    return [i for i, w in enumerate(words)
            if box[0] <= (w["box"][0] + w["box"][2]) / 2 <= box[2]
            and box[1] <= (w["box"][1] + w["box"][3]) / 2 <= box[3]]


def prepare():
    output = DEST / "localization/manifest.json"
    if output.exists():
        raise RuntimeError("Native header localization is already frozen")
    source = SOURCE / "public/tasks.json"
    meta = json.loads((SOURCE / "manifest.json").read_text())
    if sha(source) != meta["public_target_sha256"]:
        raise ValueError("Frozen public target inputs changed")
    crop_manifest = CROPS / "manifest.json"
    crops = json.loads(crop_manifest.read_text())
    if not crops["complete"] or crops["completed_targets"] != 840:
        raise ValueError("Full strict native OCR inventory required")
    index = {r["task_id"]: r for r in crops["records"]}
    tasks = json.loads(source.read_text())
    if len(tasks) != 840 or {t["task_id"] for t in tasks} != set(index):
        raise ValueError("Complete public target denominator required")
    design = {"maximum_lexical_anchors_per_header": MAX_ANCHORS,
              "exact_unicode_normalized_visible_token_matching": True,
              "unmatched_or_overflow_remains_unknown": True,
              "all_distinct_locations_retained_before_the_predeclared_cap": True,
              "no_nearest_target_or_teacher_chooses_an_ambiguous_location": True,
              "full_text_before_explicit_date_unit_or_year_substring": True,
              "bare_year_anchor_never_supplies_a_complete_period": True,
              "public_source_sha256": sha(source), "strict_ocr_manifest_sha256": sha(crop_manifest),
              "runner_sha256": sha(__file__), "future_or_independent_reference_files_read": False,
              "human_runtime_confirmations": 0, "new_generative_visual_calls": 0}
    save(DEST / "localization/design.json", design)
    pages, lines, results, counts = {}, {}, {}, Counter({"targets": len(tasks)})
    for task in tasks:
        raw = index[task["task_id"]]
        native = raw["strict_full_page_ocr"]
        digest = task["image_sha256"]
        if raw["original_image_sha256"] != digest or native["image_sha256"] != digest:
            raise ValueError("OCR page differs from original public pixels")
        if digest not in pages:
            path = Path(native["ocr_path"])
            if sha(path) != native["ocr_sha256"] or sha(task["image"]) != digest:
                raise ValueError("Frozen OCR or original page changed")
            data = json.loads(path.read_text())
            if data["image_sha256"] != digest:
                raise ValueError("OCR declares a different image hash")
            pages[digest] = {"image": task["image"], "image_sha256": digest, "ocr_path": str(path),
                             "ocr_sha256": sha(path), "width": data["width"], "height": data["height"], "words": data["words"]}
            lines[digest] = text_lines(data["words"])
        elif pages[digest]["ocr_sha256"] != native["ocr_sha256"]:
            raise ValueError("A source page has conflicting frozen OCR variants")
        words = pages[digest]["words"]
        obs = task.get("common_visual_observation") or {}
        headers = {}
        for header in obs.get("headers", []):
            counts["proposed_headers"] += 1
            context_equal = any(same_box(header["box"], task[k]) for k in ("row_context_box", "column_context_box"))
            x, y = (task["target_box"][0] + task["target_box"][2]) / 2, (task["target_box"][1] + task["target_box"][3]) / 2
            b = header["box"]
            contains = b[0] <= x <= b[2] and b[1] <= y <= b[3]
            scope_equal = any(same_box(b, box) for box in header.get("scope_boxes", []))
            aligned = align_header(header, words, lines[digest])
            for anchor in aligned["anchors"]:
                anchor["anchor_id"] = opaque([task["task_id"], header["id"], anchor["word_indices"]])
            headers[header["id"]] = {"original_header": header, "old_box_equals_observation_window": context_equal,
                                      "old_box_contains_target_center": contains, "old_box_equals_proposed_scope_box": scope_equal,
                                      **aligned}
            counts["box_equals_observation_window"] += context_equal
            counts["box_contains_target_center"] += contains
            counts["box_equals_proposed_scope_box"] += scope_equal
            counts[aligned["status"]] += 1
        indices = target_words(words, task["target_box"])
        counts["targets_without_ocr_word_center"] += not indices
        results[task["task_id"]] = {"page_hash": digest, "target_box": task["target_box"],
            "target_word_indices": indices, "headers": headers, "visible_header_ownership_certified": False}
    save(DEST / "localization/public_pages.json", pages)
    save(DEST / "localization/public_anchors.json", results)
    counts["distinct_original_pages"] = len(pages)
    snapshot = DEST / "localization/aligner_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "design_sha256": sha(DEST / "localization/design.json"),
        "public_pages_sha256": sha(DEST / "localization/public_pages.json"),
        "public_anchors_sha256": sha(DEST / "localization/public_anchors.json"), "runner_sha256": sha(snapshot),
        "no_scope_or_semantic_teacher_read": True, "native_visual_ownership_certified": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0})
    print("Public header localization frozen", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

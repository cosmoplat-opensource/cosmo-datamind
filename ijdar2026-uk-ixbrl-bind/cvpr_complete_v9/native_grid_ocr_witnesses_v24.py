"""Historical weak year correspondences using common OCR observations only.

Original source slots/periods and browser positions are teacher-side evidence.
All exported learning inputs use already frozen common OCR words/coordinates;
no source periods, fact concepts, DOM coordinates or source IDs are inputs.
Unknown candidates are retained. Year-only links do not certify complete
periods, currency/unit semantics, or the boundaries of visual ownership.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from native_table_render_audit_v24 import DEST as RENDER
from native_table_render_audit_v24 import ROOT
from prepare import EXP, opaque, save, sha

DEST = ROOT / "training_only/ocr_weak_year_v1"
COMMON = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/header_transfer_v21/localization"


def intersection(a, b):
    return max(0., min(a[2], b[2]) - max(a[0], b[0])) * max(0., min(a[3], b[3]) - max(a[1], b[1]))


def pixel_box(word, width, height):
    box = word["box"]
    return [box[0] * width, box[1] * height, box[2] * width, box[3] * height]


def area(box):
    return max(0., box[2] - box[0]) * max(0., box[3] - box[1])


def matching_words(words, box, width, height, year=None):
    """Return every plausible original OCR word, never select the nearest one."""
    if box is None:
        return []
    result = []
    for i, word in enumerate(words):
        text = word["text"]
        if year is not None:
            # A word with two different years is not a unique year glyph.
            years = set(re.findall(r"(?<!\d)(?:18|19|20|21|22)\d{2}(?!\d)", text))
            if years != {year}:
                continue
        elif not re.search(r"\d", text):
            continue
        candidate = pixel_box(word, width, height)
        mass = min(area(candidate), area(box))
        center = ((candidate[0] + candidate[2]) / 2, (candidate[1] + candidate[3]) / 2)
        if mass > 0 and intersection(candidate, box) / mass >= .5 and box[0] - 2 <= center[0] <= box[2] + 2 and box[1] - 2 <= center[1] <= box[3] + 2:
            result.append(i)
    return result


def common_box(words, indices):
    if not indices:
        return None
    boxes = [words[i]["box"] for i in indices]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def pair_label(header, target, positive_headers):
    """Do not turn absence of an edge or same-year ambiguity into a negative."""
    if not header["ocr_unique"] or not target["ocr_observed"]:
        return None
    supported = positive_headers.get(target["key"], set())
    if header["key"] in supported:
        return 1
    if supported and target["source_year_token"] != header["source_year_token"] and header["source_block_consistent"]:
        return 0
    return None


def prepare():
    output = DEST / "manifest.json"
    if output.exists():
        raise RuntimeError("Historical common-OCR correspondence dataset already frozen")
    render_audit_path = RENDER / "audit.json"
    render_audit = json.loads(render_audit_path.read_text())
    if not render_audit["complete"] or render_audit["counts"]["pages_pixels_identical"] != 32:
        raise ValueError("Historical original-pixel geometry audit must be complete")
    source_file = ROOT / "training_only/source_table_candidates.json"
    grid_audit_file = ROOT / "training_only/grid_audit.json"
    grid_audit = json.loads(grid_audit_file.read_text())
    if sha(grid_audit_file) != render_audit["historical_source_grid_audit_sha256"] or sha(source_file) != grid_audit["table_candidates_sha256"]:
        raise ValueError("Frozen source-grid provenance differs")
    tables = json.loads(source_file.read_text())
    page_file, page_manifest = COMMON / "public_pages.json", COMMON / "manifest.json"
    common_manifest = json.loads(page_manifest.read_text())
    if sha(page_file) != common_manifest["public_pages_sha256"]:
        raise ValueError("Common OCR page freeze changed")
    pages = json.loads(page_file.read_text())
    public, teachers, targets = [], {}, {}
    counts = Counter()
    geometry = {}
    reports = {}
    for company, reference in render_audit["reports"].items():
        path = RENDER / "reports" / (company + ".json")
        if sha(path) != reference["result_sha256"]:
            raise ValueError("Historical geometry record changed")
        report = json.loads(path.read_text())
        if report["source_year"] != 2023:
            raise ValueError("Only old-year correspondence teachers allowed")
        reports[company] = report
        geometry.update({t["table_id"]: t for t in report["tables"]})
    for table in tables:
        company, table_id = table["company"], table["table_id"]
        measured = geometry[table_id]
        page_id = measured["page_id"]
        rendered_page = reports[company]["pages"][page_id]
        page_hash = rendered_page["frozen_image_sha256"]
        page = pages.get(page_hash)
        if page and (page_hash != page["image_sha256"] or company + "_2023" not in Path(page["image"]).parts):
            raise ValueError("Common OCR page is not the original historical page")
        if page and (sha(page["image"]) != page_hash or sha(page["ocr_path"]) != page["ocr_sha256"]):
            raise ValueError("Frozen common original image/OCR changed")
        words = page["words"] if page else []
        width, height = (page["width"], page["height"]) if page else (1, 1)
        by_path = {r["xpath"]: r for r in measured.get("measurements", [])}
        by_cell = {b["header_cell"]: b for b in measured.get("year_candidate_geometry", [])}
        headers, fact_targets = [], []
        positive_headers = defaultdict(set)
        for source_block in table["year_cue_scope_candidates"]:
            cell_id = source_block["cell"]
            block = by_cell[cell_id]
            key = opaque([table_id, "year_header", cell_id])
            located = matching_words(words, block["year_glyph_bbox"], width, height, block["year_token"])
            header = {"key": key, "source_year_token": block["year_token"], "ocr_word_indices": located,
                      "ocr_box": common_box(words, located), "ocr_unique": len(located) == 1,
                      "source_block_consistent": source_block["all_source_periods_agree_with_year_cue"]}
            headers.append(header)
            counts["source_year_header_candidates"] += 1
            counts["header_with_unique_common_ocr_word"] += header["ocr_unique"]
            for edge in block["edges"]:
                if edge["weak_year_association_geometry_agrees"] and header["ocr_unique"]:
                    positive_headers[opaque([table_id, edge["fact_xpath"]])].add(key)
        for cell in table["cells"]:
            for fact in cell["numeric_facts"]:
                key = opaque([table_id, fact["xpath"]])
                record = by_path.get(fact["xpath"], {})
                # Only common OCR locations will be exported to the learner.
                boxes = record.get("text_rects", []) if record.get("style_visible") and record.get("page_id") == page_id else []
                box = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None
                located = matching_words(words, box, width, height)
                target = {"key": key, "ocr_word_indices": located, "ocr_box": common_box(words, located),
                          "ocr_observed": bool(located) and not fact["hidden_inline_ancestor"],
                          "source_year_token": fact["declared_period_end_year"]}
                fact_targets.append(target)
                counts["all_original_source_numeric_facts_retained"] += 1
                counts["numeric_fact_with_common_ocr_digits"] += target["ocr_observed"]
                targets[key] = {"company": company, "source_year": 2023, "table_key": table_id,
                                "source_fact_xpath": fact["xpath"], "publisher_region_id": fact["id"],
                                "year_token": fact["declared_period_end_year"],
                                "positive_header_keys": sorted(positive_headers[key])}
        for target in fact_targets:
            for header in headers:
                key = opaque([header["key"], target["key"]])
                label = pair_label(header, target, positive_headers)
                public.append({"pair_id": key, "company": company, "source_year": 2023,
                               "table_key": table_id, "page_hash": page_hash,
                               "header_key": header["key"], "target_key": target["key"],
                               "header_word_indices": header["ocr_word_indices"],
                               "target_word_indices": target["ocr_word_indices"],
                               "header_box": header["ocr_box"], "target_box": target["ocr_box"]})
                teachers[key] = {"weak_year_correspondence": label,
                                 "complete_financial_period_or_ownership_certified": False}
                counts["positive_year_edges" if label == 1 else "negative_year_edges" if label == 0 else "unknown_year_edges"] += 1
        counts["source_tables_retained"] += 1
        counts["tables_without_existing_common_ocr"] += page is None
    if counts["all_original_source_numeric_facts_retained"] != 2278 or counts["source_year_header_candidates"] != 87 or len(targets) != 2278:
        raise ValueError("All original targets/headers, including unknowns, must be preserved")
    public.sort(key=lambda r: r["pair_id"])
    files = {"common_inputs.json": public, "weak_year_teacher.json": teachers, "source_target_index.json": targets}
    for filename, value in files.items():
        save(DEST / filename, value)
    snapshot = DEST / "builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "counts": dict(counts), "historical_company_identifiers": sorted(reports),
        "render_geometry_audit_sha256": sha(render_audit_path),
        "source_tables_sha256": sha(source_file), "common_ocr_manifest_sha256": sha(page_manifest),
        "common_pages_sha256": sha(page_file), "builder_snapshot_sha256": sha(snapshot),
        "files_sha256": {name: sha(DEST / name) for name in files},
        "teacher_source_dom_coordinates_and_semantic_periods_not_exported_as_learning_inputs": True,
        "same_full_common_inputs_and_weak_teachers_required_for_every_control": True,
        "own_company_extra_source_teachers_must_be_excluded_from_parent_training": True,
        "local_0_5_10_feedback_cannot_access_other_own_company_source_labels": True,
        "unknown_year_links_must_not_be_trained_as_negatives": True,
        "all_original_source_numeric_facts_and_year_headers_retained": True,
        "weak_period_year_correspondence_preparation_complete": True,
        "native_scope_ownership_or_complete_period_teacher_certified": False,
        "not_supplied_to_frozen_v23_training": True, "trained_model_or_accuracy_result": False,
        "future_semantic_references_or_reserved_source_assets_read": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Historical common-OCR weak year edges", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

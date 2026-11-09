"""Audit publisher header declarations in all 280 historical native targets.

An explicit source association is a possible supervision witness, not proof of
visible ownership. No geometric or semantic guess is promoted to a label.
Later reports and independent test references are never opened.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree
from native_binding_candidates_v18 import DEST as BINDING_INPUTS
from native_binding_prepare_v14 import DEST as TEACHERS
from prepare import EXP, save, sha

DEST = BINDING_INPUTS.parent / "header_supervision_audit_v20"
HTML = "http://www.w3.org/1999/xhtml"
IX = "http://www.xbrl.org/2013/inlineXBRL"


def html_name(node):
    if node is None or not isinstance(node.tag, str):
        return ""
    name = etree.QName(node)
    return name.localname.lower() if name.namespace in (None, HTML) else ""


def nearest(node, names):
    return next((parent for parent in node.iterancestors() if html_name(parent) in names), None)


def declaration_for(fact, ids):
    cell = nearest(fact, {"td", "th"})
    table = nearest(cell, {"table"}) if cell is not None else None
    result = {
        "target_in_html_cell": cell is not None,
        "target_in_html_table": table is not None,
        "source_headers_attribute_present": cell is not None and "headers" in cell.attrib,
        "explicit_header_ids": [],
        "unresolved_or_invalid_header_ids": [],
        "qualified_publisher_header_association_available": False,
        "visible_header_ownership_certified": False,
    }
    if cell is None or table is None:
        return result
    references = cell.get("headers", "").split()
    if len(references) != len(set(references)):
        result["duplicate_header_references"] = True
    for ident in dict.fromkeys(references):
        nodes = ids.get(ident, [])
        if len(nodes) != 1 or html_name(nodes[0]) != "th" or nearest(nodes[0], {"table"}) is not table:
            result["unresolved_or_invalid_header_ids"].append(ident)
            continue
        result["explicit_header_ids"].append({
            "id": ident,
            "scope_attribute": nodes[0].get("scope"),
            "literal_text": " ".join("".join(nodes[0].itertext()).split())[:500],
            "rendered_visibility_and_geometric_ownership_not_verified": True,
        })
    result["qualified_publisher_header_association_available"] = bool(result["explicit_header_ids"]) and not result["unresolved_or_invalid_header_ids"]
    result["table_header_cells"] = sum(html_name(n) == "th" and nearest(n, {"table"}) is table for n in table.iter())
    result["table_explicit_scope_attributes"] = sum(html_name(n) == "th" and n.get("scope") in {"row", "col", "rowgroup", "colgroup"} and nearest(n, {"table"}) is table for n in table.iter())
    result["fact_has_hidden_inline_ancestor"] = any(
        (isinstance(n.tag, str) and n.tag == "{" + IX + "}hidden")
        or "display:none" in n.get("style", "").replace(" ", "").lower()
        or "visibility:hidden" in n.get("style", "").replace(" ", "").lower()
        for n in [fact, *fact.iterancestors()]
    )
    return result


def run():
    output = DEST / "training_only/structure_audit.json"
    if output.exists():
        raise RuntimeError("Historical source declaration audit already frozen")
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    teacher_manifest = json.loads((TEACHERS / "manifest.json").read_text())
    if sha(teacher_file) != teacher_manifest["teacher_sha256"]:
        raise ValueError("Historical teacher/source identity changed")
    teachers = json.loads(teacher_file.read_text())
    public_file = BINDING_INPUTS / "candidates/tasks.json"
    public_manifest = json.loads((BINDING_INPUTS / "candidates/manifest.json").read_text())
    if sha(public_file) != public_manifest["candidate_inventory_sha256"]:
        raise ValueError("Frozen public inventory changed")
    tasks = [t for t in json.loads(public_file.read_text()) if t["source_year"] == 2023]
    if len(tasks) != 280 or {t["task_id"] for t in tasks} != set(teachers):
        raise ValueError("Complete historical target denominator required")
    plan_file = EXP / "runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1/download_plan.json"
    sources = [s for s in json.loads(plan_file.read_text())["files"] if s["kind"] == "native_report" and s["period_end"].startswith("2023-")]
    if len(sources) != 14:
        raise ValueError("Fourteen historical reports required")
    counts, reports, targets, source_hashes = Counter(), {}, {}, {}
    for source in sources:
        path = Path(source["path"])
        digest = sha(path)
        if teacher_manifest["source_and_audit_sha256"].get(str(path)) != digest:
            raise ValueError("Original 2023 report hash changed")
        source_hashes[str(path)] = digest
        tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True))
        if tree.docinfo.doctype:
            raise ValueError("Source document type declaration is unsupported")
        ids = defaultdict(list)
        table_count = header_count = headers_count = scope_count = 0
        for node in tree.iter():
            if not isinstance(node.tag, str):
                continue
            if node.get("id"):
                ids[node.get("id")].append(node)
            table_count += html_name(node) == "table"
            header_count += html_name(node) == "th"
            headers_count += html_name(node) in {"td", "th"} and "headers" in node.attrib
            scope_count += html_name(node) == "th" and "scope" in node.attrib
        report_counts = Counter()
        for task in tasks:
            if task["company"] != source["lei"]:
                continue
            ident = task["task_id"]
            teacher = teachers[ident]
            if teacher["source_year"] != 2023 or teacher["source_report_sha256"] != digest:
                raise ValueError("Historical task/source provenance differs")
            nodes = ids.get(teacher["publisher_region_id"], [])
            if len(nodes) == 1:
                row = declaration_for(nodes[0], ids)
                row["source_target_id_unique"] = True
            else:
                row = {"source_target_id_unique": False, "unknown_reason": "missing_or_duplicate_publisher_region_id",
                       "qualified_publisher_header_association_available": False, "visible_header_ownership_certified": False}
            targets[ident] = {"report": task["report"], **row}
            report_counts["targets"] += 1
            for key in ("source_target_id_unique", "target_in_html_cell", "target_in_html_table", "source_headers_attribute_present",
                        "qualified_publisher_header_association_available", "fact_has_hidden_inline_ancestor"):
                report_counts[key] += bool(row.get(key))
        reports[source["lei"]] = {
            "html_tables": table_count, "html_th_cells": header_count,
            "html_cells_with_headers_attribute": headers_count, "html_th_with_scope_attribute": scope_count,
            "target_counts": dict(report_counts), "source_report_sha256": digest,
        }
        counts.update(report_counts)
        print("Historical header declaration audit", source["lei"], dict(report_counts), flush=True)
    if set(targets) != set(teachers) or counts["targets"] != 280:
        raise ValueError("All historical targets must remain in the audit")
    counts["reports"] = len(reports)
    counts["reports_with_any_html_th"] = sum(r["html_th_cells"] > 0 for r in reports.values())
    counts["reports_with_any_headers_attribute"] = sum(r["html_cells_with_headers_attribute"] > 0 for r in reports.values())
    save(output, {
        "complete": True, "audited_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "reports": reports, "targets": targets, "source_report_sha256": source_hashes,
        "teacher_sha256": sha(teacher_file), "public_input_sha256": sha(public_file), "auditor_sha256": sha(__file__),
        "all_280_historical_targets_retained": True,
        "source_declarations_are_not_render_verified_visual_ownership_labels": True,
        "missing_explicit_header_attributes_do_not_imply_missing_visible_headers": True,
        "geometric_or_semantic_guesses_are_not_promoted_to_labels": True,
        "future_or_independent_reference_files_read": False, "new_generative_visual_calls": 0,
        "human_runtime_confirmations": 0, "native_visual_scope_teacher_certified": False,
    })
    save(DEST / "pipeline_progress.json", {"phase": "historical_header_declaration_audit_complete",
         "updated_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
         "visual_scope_teacher_certified": False, "training_started": False,
         "independent_native_test_completed": False, "all_original_cvpr_experiments_completed": False})
    print("Historical source header qualification counts", dict(counts), flush=True)


if __name__ == "__main__":
    run()

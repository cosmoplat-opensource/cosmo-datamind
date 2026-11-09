"""Historical table slots and source-period consistency, never ownership labels.

No reserved/later-year report is opened. The audit preserves every one of the
280 old targets, including unresolved IDs and unsupported HTML tables. Source
slots are not assumed to match CSS rendering; browser validation is required
before any candidate could become an additional weak training witness.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree
from native_binding_prepare_v14 import DEST as TEACHERS
from native_binding_prepare_v14 import IX, XI, context, expanded, unique_ids
from native_header_supervision_audit_v20 import html_name, nearest
from prepare import EXP, opaque, save, sha

DEST = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/historical_grid_supervision_v24"
TABLE_MODEL = "https://html.spec.whatwg.org/multipage/tables.html#table-processing-model"
MAX_SLOTS = 100000


def declared_span(cell, key, maximum):
    value = cell.get(key, "1")
    if not re.fullmatch(r"\d+", value) or not 1 <= int(value) <= maximum:
        raise ValueError("unsupported_" + key)
    return int(value)


def table_grid(table):
    """Conservative explicit-slot subset of the HTML table model.

    Rowspan=0, implicit rows, crossing row groups and overlapping spans abstain
    rather than being silently repaired into training references.
    """
    rows = [n for n in table.iter() if html_name(n) == "tr" and nearest(n, {"table"}) is table]
    cells, occupied = [], {}
    if not rows:
        raise ValueError("no_explicit_rows")
    groups = []
    for row in rows:
        parent = row.getparent()
        if parent is not table and html_name(parent) not in {"thead", "tbody", "tfoot"}:
            raise ValueError("unsupported_row_container")
        groups.append(parent)
    footer_seen = False
    for group in groups:
        if html_name(group) == "tfoot":
            footer_seen = True
        elif footer_seen:
            raise ValueError("footer_before_other_rows_requires_browser_ordering")
    for y, row in enumerate(rows):
        x = 0
        children = [n for n in row if html_name(n) in {"td", "th"}]
        if not children:
            raise ValueError("row_without_anchored_cells")
        for node in children:
            while (y, x) in occupied:
                x += 1
            width, height = declared_span(node, "colspan", 1000), declared_span(node, "rowspan", 65534)
            if y + height > len(rows):
                raise ValueError("span_requires_implicit_rows")
            if any(groups[j] is not groups[y] for j in range(y, y + height)):
                raise ValueError("span_crosses_row_group")
            if len(occupied) + width * height > MAX_SLOTS:
                raise ValueError("slot_resource_limit")
            slots = [(r, c) for r in range(y, y + height) for c in range(x, x + width)]
            if any(slot in occupied for slot in slots):
                raise ValueError("overlapping_source_spans")
            index = len(cells)
            occupied.update(dict.fromkeys(slots, index))
            cells.append({"element": node, "row": y, "column": x, "rowspan": height, "colspan": width})
            x += width
    return cells


def literal_cell_text(cell):
    # Reject nested-table headers separately. Do not insert artificial spaces
    # at font-only spans, or repair dates/letters using a semantic answer.
    return " ".join("".join(cell.itertext()).split())


def year_cue(text):
    if len(text) > 100:
        return None
    values = set(re.findall(r"(?<!\d)(?:18|19|20|21|22)\d{2}(?!\d)", text))
    return next(iter(values)) if len(values) == 1 else None


def hidden_inline(node):
    return any(n.tag == IX + "hidden" or "display:none" in n.get("style", "").replace(" ", "").lower()
               or "visibility:hidden" in n.get("style", "").replace(" ", "").lower()
               for n in [node, *node.iterancestors()] if isinstance(n.tag, str))


def column_covers(header, cell):
    return header["column"] <= cell["column"] and cell["column"] + cell["colspan"] <= header["column"] + header["colspan"]


def candidate_blocks(cells, facts):
    cues = [{"cell": i, "year_token": year_cue(literal_cell_text(c["element"]))}
            for i, c in enumerate(cells) if not facts.get(i) and not any(html_name(n) == "table" for n in c["element"].iterdescendants())]
    cues = [c for c in cues if c["year_token"] is not None]
    blocks = []
    for cue in cues:
        header = cells[cue["cell"]]
        following = [cells[c["cell"]]["row"] for c in cues if cells[c["cell"]]["row"] >= header["row"] + header["rowspan"]
                     and cells[c["cell"]]["column"] < header["column"] + header["colspan"]
                     and header["column"] < cells[c["cell"]]["column"] + cells[c["cell"]]["colspan"]]
        stop = min(following) if following else max(c["row"] + c["rowspan"] for c in cells)
        supported = [f for i, c in enumerate(cells) if header["row"] + header["rowspan"] <= c["row"] < stop and column_covers(header, c)
                     for f in facts.get(i, []) if not f["hidden_inline_ancestor"]]
        known = [f for f in supported if f["declared_period_end_year"] is not None]
        agrees = [f for f in known if f["declared_period_end_year"] == cue["year_token"]]
        consistent = len(known) >= 2 and len(known) == len(supported) and len(agrees) == len(known)
        blocks.append({**cue, "stop_before_row": stop, "source_numeric_facts": len(supported),
                       "known_source_periods": len(known), "matching_source_period_end_year": len(agrees),
                       "all_source_periods_agree_with_year_cue": consistent,
                       "supported_fact_paths": [f["xpath"] for f in supported],
                       "matching_fact_paths": [f["xpath"] for f in agrees],
                       "browser_geometry_and_visibility_verified": False,
                       "visual_header_ownership_certified": False})
    return blocks


def run():
    output = DEST / "training_only/grid_audit.json"
    if output.exists():
        raise RuntimeError("Historical grid audit already frozen")
    manifest_file = TEACHERS / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    if sha(teacher_file) != manifest["teacher_sha256"]:
        raise ValueError("Historical teacher/source hashes changed")
    teachers = json.loads(teacher_file.read_text())
    if len(teachers) != 280 or any(t["source_year"] != 2023 for t in teachers.values()):
        raise ValueError("Only the original 280 historical targets may be audited")
    plan_file = EXP / "runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1/download_plan.json"
    sources = [s for s in json.loads(plan_file.read_text())["files"] if s["kind"] == "native_report" and s["period_end"].startswith("2023-")]
    if len(sources) != 14:
        raise ValueError("Only fourteen original 2023 sources required")
    counts, reports, targets, all_tables = Counter(), {}, {}, []
    for source in sources:
        path = Path(source["path"])
        digest = sha(path)
        if manifest["source_and_audit_sha256"].get(str(path)) != digest:
            raise ValueError("An original 2023 report changed")
        tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True))
        if tree.docinfo.doctype:
            raise ValueError("Unsupported source document type declaration")
        contexts = unique_ids(tree.iter(XI + "context"))
        declared = {}
        for ident, node in contexts.items():
            try:
                period = context(node)["period"]
                end = period.get("instant") or period.get("end_date")
                declared[ident] = end[:4] if end and re.match(r"^\d{4}-", end) else None
            except (ValueError, KeyError, TypeError, AttributeError):
                declared[ident] = None
        ids, by_table = defaultdict(list), defaultdict(list)
        for node in tree.iter():
            if not isinstance(node.tag, str):
                continue
            if node.get("id"):
                ids[node.get("id")].append(node)
            if node.tag == IX + "nonFraction":
                cell, table = nearest(node, {"td", "th"}), nearest(node, {"table"})
                if cell is not None and table is not None:
                    by_table[table].append((node, cell))
        cell_info, fact_info, table_info = {}, {}, {}
        local = Counter()
        for table, numeric in by_table.items():
            table_id = opaque([digest, tree.getpath(table)])
            local["tables_with_inline_numeric_facts"] += 1
            try:
                grid = table_grid(table)
            except ValueError as error:
                table_info[table] = {"table_id": table_id, "supported_grid": False, "unknown_reason": str(error)}
                local["unsupported_source_grids"] += 1
                continue
            index = {c["element"]: i for i, c in enumerate(grid)}
            facts = defaultdict(list)
            for node, cell in numeric:
                if cell not in index:
                    local["numeric_facts_outside_supported_direct_rows"] += 1
                    continue
                try:
                    concept = expanded(node.get("name"), node.nsmap)
                except ValueError:
                    concept = None
                entry = {"id": node.get("id"), "xpath": tree.getpath(node), "concept": concept,
                         "declared_period_end_year": declared.get(node.get("contextRef")),
                         "source_unit_ref": node.get("unitRef"), "source_scale_attribute": node.get("scale"),
                         "hidden_inline_ancestor": hidden_inline(node)}
                facts[index[cell]].append(entry)
                fact_info[node] = entry
            blocks = candidate_blocks(grid, facts)
            local["supported_source_grids"] += 1
            local["source_numeric_facts_in_supported_grids"] += sum(len(f) for f in facts.values())
            local["year_cue_candidates"] += len(blocks)
            local["period_consistent_source_grid_candidates"] += sum(b["all_source_periods_agree_with_year_cue"] for b in blocks)
            table_info[table] = {"table_id": table_id, "supported_grid": True, "blocks": blocks}
            for i, cell in enumerate(grid):
                cell_info[cell["element"]] = {"table_id": table_id, "cell": i,
                                             "numeric_facts_in_cell": len(facts.get(i, [])),
                                             "row": cell["row"], "column": cell["column"]}
            all_tables.append({"table_id": table_id, "company": source["lei"], "source_report": str(path),
                               "source_report_sha256": digest, "table_xpath": tree.getpath(table),
                               "cells": [{"xpath": tree.getpath(c["element"]), "row": c["row"], "column": c["column"],
                                          "rowspan": c["rowspan"], "colspan": c["colspan"],
                                          "literal_text": literal_cell_text(c["element"])[:400],
                                          "numeric_facts": facts.get(i, [])} for i, c in enumerate(grid)],
                               "year_cue_scope_candidates": blocks,
                               "browser_verified": False, "training_witnesses_qualified": False})
        for ident, teacher in teachers.items():
            if teacher["source_report_sha256"] != digest:
                continue
            local["historical_targets"] += 1
            nodes = ids.get(teacher["publisher_region_id"], [])
            row = {"report_company": source["lei"], "source_id_unique": len(nodes) == 1,
                   "source_table_grid_supported": False, "visual_scope_certified": False}
            if len(nodes) == 1:
                node = nodes[0]
                table, cell = nearest(node, {"table"}), nearest(node, {"td", "th"})
                row["source_target_xpath"] = tree.getpath(node)
                row["source_table_present"] = table is not None and cell is not None
                local["target_in_source_table"] += row["source_table_present"]
                if table in table_info and table_info[table]["supported_grid"] and cell in cell_info and node in fact_info:
                    row.update(cell_info[cell], source_table_grid_supported=True)
                    local["target_in_supported_source_grid"] += 1
                    xpath = fact_info[node]["xpath"]
                    candidates = [b for b in table_info[table]["blocks"] if xpath in b["supported_fact_paths"]]
                    matching = [b for b in candidates if xpath in b["matching_fact_paths"]]
                    consistent = [b for b in matching if b["all_source_periods_agree_with_year_cue"]]
                    row.update(candidate_year_cells=[b["cell"] for b in candidates],
                               source_period_agreeing_year_cells=[b["cell"] for b in matching],
                               source_period_consistent_block_cells=[b["cell"] for b in consistent])
                    local["target_with_grid_year_cue_matching_declared_period_end"] += bool(matching)
                    local["target_in_period_consistent_grid_candidate"] += bool(consistent)
                elif table in table_info:
                    row["grid_unknown_reason"] = table_info[table].get("unknown_reason", "unresolved_direct_cell_or_inline_fact")
            targets[ident] = row
        reports[source["lei"]] = {"counts": dict(local), "source_report_sha256": digest}
        counts.update(local)
        print("Historical source grid audit", source["lei"], dict(local), flush=True)
    if len(targets) != 280 or counts["historical_targets"] != 280:
        raise ValueError("All historical targets, including unknown cases, must remain")
    tables_file = DEST / "training_only/source_table_candidates.json"
    save(tables_file, all_tables)
    snapshot = DEST / "training_only/auditor_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "audited_utc": datetime.now(timezone.utc).isoformat(),
        "counts": dict(counts), "reports": reports, "targets": targets,
        "table_candidates_sha256": sha(tables_file), "historical_teacher_sha256": sha(teacher_file),
        "source_manifest_sha256": sha(manifest_file), "auditor_snapshot_sha256": sha(snapshot),
        "html_slot_model_reference": TABLE_MODEL,
        "source_grid_and_semantic_consistency_are_not_visible_ownership_certificates": True,
        "year_cues_are_not_complete_periods": True, "browser_geometry_and_visibility_verified": False,
        "additional_training_witnesses_qualified": False, "not_supplied_to_frozen_v23_training": True,
        "original_280_targets_all_retained": True,
        "future_or_reserved_independent_source_assets_or_references_read": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Historical grid candidate qualification pending rendering", dict(counts), flush=True)


if __name__ == "__main__":
    run()

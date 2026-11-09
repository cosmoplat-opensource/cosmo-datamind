"""Check historical source-grid candidates against original rendered pixels.

Only the six 2023 reports containing audited source tables are opened. CSS
geometry and source-period consistency provide weak year associations, not
certified financial period ownership or complete scope boundaries. Nothing
from this audit is supplied to the frozen v23 experiment.
"""

import argparse
import io
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from prepare import EXP, save, sha


ROOT = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/historical_grid_supervision_v24"
DEST = ROOT / "training_only/render_geometry_v1"
RENDERS = EXP / "runs/cvpr_complete_v9_20261013/expanded_development_sources/native_renders_v2"

# XPath comes from the frozen lxml source tree. Prefix resolution is required
# for inline-XBRL elements in the original application/xhtml+xml document.
MEASURE = r"""args => {
  const ns = prefix => document.documentElement.lookupNamespaceURI(prefix);
  const resolve = path => {
    const found = document.evaluate(path, document, ns, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    return found.snapshotLength === 1 ? found.snapshotItem(0) : null;
  };
  const root = document.getElementById(args.page);
  const rb = root.getBoundingClientRect(), dpr = devicePixelRatio;
  const rect = r => [(r.x-rb.x)*dpr, (r.y-rb.y)*dpr,
                     (r.right-rb.x)*dpr, (r.bottom-rb.y)*dpr];
  const styleVisible = node => {
    for (let n = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement; n; n=n.parentElement) {
      const s=getComputedStyle(n);
      if (s.display==='none' || ['hidden','collapse'].includes(s.visibility) ||
          Number(s.opacity)===0 || s.contentVisibility==='hidden') return false;
    }
    return true;
  };
  const measure = item => {
    const node = resolve(item.xpath);
    if (!node) return {resolved:false, xpath:item.xpath};
    const textNodes=[], walker=document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    let text='', n;
    while ((n=walker.nextNode())) { textNodes.push({node:n, start:text.length}); text+=n.textContent; }
    const ranges=[];
    for (const part of textNodes) {
      if (!part.node.textContent.trim() || !styleVisible(part.node)) continue;
      const range=document.createRange();range.selectNodeContents(part.node);
      for (const r of range.getClientRects()) if(r.width>0 && r.height>0) ranges.push(rect(r));
    }
    const matches=[];
    if (item.year) {
      const expression=new RegExp('(?<![0-9])'+item.year+'(?![0-9])','g');
      for (const match of text.matchAll(expression)) {
        const start=match.index, end=start+match[0].length;
        const first=textNodes.find(p=>p.start<=start && start<p.start+p.node.textContent.length);
        const last=textNodes.find(p=>p.start<end && end<=p.start+p.node.textContent.length);
        if (!first || !last) continue;
        const covered=textNodes.filter(p=>p.start<end && start<p.start+p.node.textContent.length);
        const range=document.createRange();range.setStart(first.node,start-first.start);range.setEnd(last.node,end-last.start);
        matches.push({style_visible:covered.every(p=>styleVisible(p.node)),
                      rects:[...range.getClientRects()].filter(r=>r.width>0&&r.height>0).map(rect)});
      }
    }
    const host=node.closest('.pf');
    return {resolved:true, xpath:item.xpath, id:node.id||null, name:node.localName,
            page_id:host?.id||null, bbox:rect(node.getBoundingClientRect()),
            style_visible:styleVisible(node), text_rects:ranges, year_matches:matches};
  };
  return {page:args.page, page_size:[rb.width*dpr,rb.height*dpr],
          nodes:args.items.map(measure)};
}"""


def finite_box(box):
    return (isinstance(box, list) and len(box) == 4
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in box)
            and box[2] > box[0] and box[3] > box[1])


def union(boxes):
    if not boxes or not all(finite_box(b) for b in boxes):
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def visible_text_box(record, page_id, page_size, year=False):
    """Nonzero browser text geometry is necessary, not an occlusion certificate."""
    if not record or not record.get("resolved") or record.get("page_id") != page_id:
        return None
    if not record.get("style_visible"):
        return None
    if year:
        matches = record.get("year_matches", [])
        if len(matches) != 1 or not matches[0]["style_visible"]:
            return None
        boxes = matches[0]["rects"]
    else:
        boxes = record.get("text_rects", [])
    box = union(boxes)
    if box is None or box[0] < -2 or box[1] < -2 or box[2] > page_size[0] + 2 or box[3] > page_size[1] + 2:
        return None
    return box


def column_intervals(table, measurements, page_id, page_size):
    """Empirical text columns; reject source/CSS order disagreement and ties."""
    centers = defaultdict(list)
    for cell in table["cells"]:
        if cell["colspan"] != 1:
            continue
        for fact in cell["numeric_facts"]:
            if fact["hidden_inline_ancestor"]:
                continue
            box = visible_text_box(measurements.get(fact["xpath"]), page_id, page_size)
            if box is not None:
                centers[cell["column"]].append((box[0] + box[2]) / 2)
    tracks = [(column, median(xs)) for column, xs in sorted(centers.items()) if len(xs) >= 2]
    if len(tracks) < 2 or any(b[1] - a[1] <= 4 for a, b in zip(tracks, tracks[1:])):
        return {}
    result = {}
    for i, (column, center) in enumerate(tracks):
        left = ((tracks[i - 1][1] + center) / 2 if i else center - (tracks[1][1] - center) / 2)
        right = ((center + tracks[i + 1][1]) / 2 if i < len(tracks) - 1 else center + (center - tracks[i - 1][1]) / 2)
        result[column] = [left, right]
    return result


def inspect_block(table, block, measurements, page_id, page_size, pixels_equal):
    header = table["cells"][block["cell"]]
    header_box = visible_text_box(measurements.get(header["xpath"]), page_id, page_size, year=True)
    columns = column_intervals(table, measurements, page_id, page_size)
    interval = columns.get(header["column"]) if header["colspan"] == 1 else None
    header_aligned = (header_box is not None and interval is not None
                      and interval[0] - 2 <= (header_box[0] + header_box[2]) / 2 <= interval[1] + 2)
    edges = []
    for xpath in block["supported_fact_paths"]:
        box = visible_text_box(measurements.get(xpath), page_id, page_size)
        reasons = []
        if not pixels_equal:
            reasons.append("rendered_pixels_differ_from_frozen_page")
        if not block["all_source_periods_agree_with_year_cue"]:
            reasons.append("source_period_block_not_consistent")
        if header_box is None:
            reasons.append("year_glyph_not_uniquely_visible_on_same_page")
        if not header_aligned:
            reasons.append("source_column_not_confirmed_by_rendered_text_tracks")
        if box is None:
            reasons.append("numeric_text_not_visible_on_same_page")
        elif header_box is not None:
            if header_box[3] > (box[1] + box[3]) / 2 + 2:
                reasons.append("year_glyph_not_above_numeric_text")
            if interval is not None and not interval[0] - 2 <= (box[0] + box[2]) / 2 <= interval[1] + 2:
                reasons.append("numeric_text_outside_rendered_column_track")
        edges.append({"fact_xpath": xpath, "numeric_text_bbox": box,
                      "weak_year_association_geometry_agrees": not reasons, "unknown_reasons": reasons})
    agrees = sum(e["weak_year_association_geometry_agrees"] for e in edges)
    return {"header_cell": block["cell"], "year_token": block["year_token"],
            "source_period_consistent": block["all_source_periods_agree_with_year_cue"],
            "page_id": page_id, "year_glyph_bbox": header_box, "rendered_column_interval": interval,
            "source_column_and_year_glyph_aligned": bool(header_aligned), "edges": edges,
            "all_candidate_edges_geometrically_supported": len(edges) >= 2 and agrees == len(edges),
            "visual_scope_ownership_certified": False, "complete_financial_period_certified": False}


def same_pixels(original, screenshot):
    from PIL import Image

    with Image.open(original) as first, Image.open(io.BytesIO(screenshot)) as second:
        a, b = first.convert("RGBA"), second.convert("RGBA")
        return a.size == b.size and a.tobytes() == b.tobytes()


def source_tables():
    source = ROOT / "training_only/source_table_candidates.json"
    audit_path = ROOT / "training_only/grid_audit.json"
    audit = json.loads(audit_path.read_text())
    if not audit["complete"] or sha(source) != audit["table_candidates_sha256"]:
        raise ValueError("Historical table candidate freeze differs")
    tables = json.loads(source.read_text())
    if len(tables) != 40:
        raise ValueError("Unexpected source-table inventory")
    grouped = defaultdict(list)
    for table in tables:
        path = Path(table["source_report"])
        if (not re.fullmatch(r"[A-Z0-9]{20}_2023-\d{2}-\d{2}", path.parent.name)
                or path.name != "original.xhtml"
                or path.parent.parent != EXP / "ext/xbrl_scope_additional_development"
                or path.parent.name[:20] != table["company"]):
            raise ValueError("Only original historical reports may be opened")
        grouped[table["company"]].append(table)
    return grouped, audit_path


def audit_report(browser, company, tables):
    source = Path(tables[0]["source_report"])
    digest = sha(source)
    if any(t["source_report"] != str(source) or t["source_report_sha256"] != digest for t in tables):
        raise ValueError("Original historical source changed")
    # Open one historical-only render record, never a multi-year manifest.
    record_path = RENDERS / (company + "_2023/dpr2/render_scoring_only.json")
    record = json.loads(record_path.read_text())
    if record["original_sha256"] != digest or not record["period_end"].startswith("2023-"):
        raise ValueError("Frozen renderer provenance differs")
    frozen_pages = {p["page_id"]: p for p in record["selected_pages"]}
    blocked, errors = [], []
    page = browser.new_page(viewport={"width": 1400, "height": 1600}, device_scale_factor=2)
    page.set_default_timeout(30000)
    page.on("pageerror", lambda error: errors.append(str(error)[:240]))

    def route_request(route):
        if route.request.url.startswith(("https:", "http:", "ws:", "wss:")):
            blocked.append({"scheme": route.request.url.split(":", 1)[0], "resource_type": route.request.resource_type})
            route.abort()
        else:
            route.continue_()

    page.route("**/*", route_request)
    try:
        page.goto(source.as_uri(), wait_until="load", timeout=60000)
        page.evaluate("document.fonts.ready")
        dom = page.evaluate("() => ({content_type:document.contentType,parse_error:!!document.querySelector('parsererror')})")
        if dom["parse_error"]:
            raise ValueError("Browser could not parse original historical XHTML")
        table_pages = page.evaluate(r"""paths => paths.map(path=>{
            const n=document.evaluate(path,document,p=>document.documentElement.lookupNamespaceURI(p),
                XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue;
            return n?.closest('.pf')?.id||null;
        })""", [t["table_xpath"] for t in tables])
        pages = {}
        results = []
        counts = Counter()
        for table, identifier in zip(tables, table_pages):
            if identifier not in frozen_pages or not re.fullmatch(r"pf[A-Za-z0-9_-]+", identifier or ""):
                results.append({"table_id": table["table_id"], "page_id": identifier,
                                "unknown_reason": "table_not_on_a_frozen_historical_page"})
                counts["tables_not_on_frozen_page"] += 1
                continue
            locator = page.locator('[id="' + identifier + '"]')
            locator.scroll_into_view_if_needed()
            if identifier not in pages:
                page.evaluate(r"""async id => {
                    const root=document.getElementById(id);
                    await Promise.all([...root.querySelectorAll('img')].map(i=>i.complete?Promise.resolve():
                        new Promise(r=>{i.onload=r;i.onerror=r})));
                }""", identifier)
                original = Path(frozen_pages[identifier]["image_path"])
                if sha(original) != frozen_pages[identifier]["image_sha256"]:
                    raise ValueError("Original frozen page image changed")
                screenshot = locator.screenshot(timeout=30000)
                image = DEST / "rerendered_pages" / company / (identifier + ".png")
                image.parent.mkdir(parents=True, exist_ok=True)
                image.write_bytes(screenshot)
                pages[identifier] = {"image_path": str(image), "image_sha256": sha(image),
                                     "frozen_image_sha256": frozen_pages[identifier]["image_sha256"],
                                     "decoded_pixels_identical": same_pixels(original, screenshot)}
            items = {table["table_xpath"]: {"xpath": table["table_xpath"]}}
            for cell in table["cells"]:
                items[cell["xpath"]] = {"xpath": cell["xpath"]}
                for fact in cell["numeric_facts"]:
                    items[fact["xpath"]] = {"xpath": fact["xpath"]}
            for block in table["year_cue_scope_candidates"]:
                items[table["cells"][block["cell"]]["xpath"]]["year"] = block["year_token"]
            measured = page.evaluate(MEASURE, {"page": identifier, "items": list(items.values())})
            by_path = {m["xpath"]: m for m in measured["nodes"]}
            inspected = [inspect_block(table, b, by_path, identifier, measured["page_size"], pages[identifier]["decoded_pixels_identical"])
                         for b in table["year_cue_scope_candidates"]]
            results.append({"table_id": table["table_id"], "page_id": identifier,
                            "page_size": measured["page_size"], "measurements": measured["nodes"],
                            "year_candidate_geometry": inspected})
            counts["tables_measured"] += 1
            counts["year_candidates_checked"] += len(inspected)
            counts["year_candidates_all_edges_geometry_agree"] += sum(b["all_candidate_edges_geometrically_supported"] for b in inspected)
            for block in inspected:
                counts["source_candidate_fact_edges"] += len(block["edges"])
                counts["weak_year_edges_geometry_agree"] += sum(e["weak_year_association_geometry_agrees"] for e in block["edges"])
                for edge in block["edges"]:
                    counts.update("edge_unknown/" + reason for reason in edge["unknown_reasons"])
        counts["pages_rerendered"] = len(pages)
        counts["pages_pixels_identical"] = sum(p["decoded_pixels_identical"] for p in pages.values())
        return {"complete": True, "company": company, "source_year": 2023,
                "source_report_sha256": digest, "frozen_historical_render_record_sha256": sha(record_path),
                "code_sha256": sha(__file__), "tables": results, "pages": pages,
                "counts": dict(counts), "dom_diagnostics": dom, "blocked_external_requests": blocked,
                "page_script_errors": errors, "visual_scope_ownership_certified": False}
    finally:
        page.close()


def run(company=None):
    from playwright.sync_api import sync_playwright

    if (DEST / "audit.json").exists():
        raise RuntimeError("Completed rendering audit is frozen")
    grouped, audit_path = source_tables()
    if company is not None and company not in grouped:
        raise ValueError("Company is outside the historical source-table inventory")
    selected = [company] if company else sorted(grouped)
    executable = next(p for p in (Path.home() / "Library/Caches/ms-playwright").rglob("chrome-headless-shell") if p.is_file())
    results = {}
    with sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=str(executable), headless=True)
        browser_version = browser.version
        try:
            for identifier in selected:
                output = DEST / "reports" / (identifier + ".json")
                if output.exists():
                    result = json.loads(output.read_text())
                    if result["code_sha256"] != sha(__file__) or result["source_report_sha256"] != sha(grouped[identifier][0]["source_report"]):
                        raise ValueError("Refusing to reuse a different rendering protocol")
                    for value in result["pages"].values():
                        if sha(value["image_path"]) != value["image_sha256"]:
                            raise ValueError("A completed audited page changed")
                else:
                    result = audit_report(browser, identifier, grouped[identifier])
                    save(output, result)
                results[identifier] = result
                save(DEST / "progress.json", {"completed_reports": list(results), "expected_reports": len(grouped),
                                            "historical_source_grid_audit_sha256": sha(audit_path)})
                print("Historical rendered-grid audit", identifier, result["counts"], flush=True)
        finally:
            browser.close()
    if company is not None:
        return
    counts = Counter()
    for result in results.values():
        counts.update(result["counts"])
    snapshot = DEST / "auditor_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "audit.json", {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "reports": {c: {"counts": r["counts"], "result_sha256": sha(DEST / "reports" / (c + ".json"))}
                    for c, r in results.items()}, "counts": dict(counts),
        "historical_source_grid_audit_sha256": sha(audit_path), "auditor_snapshot_sha256": sha(snapshot),
        "browser_version": browser_version, "viewport": [1400, 1600], "device_pixel_ratio": 2,
        "source_styles_or_pixels_modified": False, "external_network_requests_allowed": False,
        "audit_process_only_opened_2023_sources_and_historical_page_records": True,
        "future_semantic_reference_or_reserved_source_files_opened": False,
        "shared_multi_year_render_manifests_not_used_as_audit_inputs": True,
        "year_association_is_weak_not_complete_period_or_ownership_certification": True,
        "text_occlusion_and_ocr_glyph_alignment_not_certified": True,
        "additional_training_witnesses_qualified": False, "not_supplied_to_frozen_v23_training": True,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Historical rendered-grid audit complete", dict(counts), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company")
    run(parser.parse_args().company)

"""Held-out companies, step A: render tagged statement pages and record numeric fact boxes.

Companies: Bay Capital (213800F59868OZQU6E56) and Wilmington (213800PFUQT4HHFYZJ63), three
filings each; the 2024/2025 filings are the held-out test, 2023 serves as that company's own
historical teacher exactly as for the 14 development companies. Pages are rendered with a
viewport wider than the page (device scale factor 2, same element-screenshot recipe as the
development renders after the v30 fix). For every ix:nonFraction on a rendered page the DOM
box (normalised to the page) and the tag attributes are recorded; the fact *values* and
contexts are not read here. Run with a Python that has Playwright (anaconda).
"""

import os
import hashlib
import json
import math
from pathlib import Path

from playwright.sync_api import sync_playwright

EXP = Path(__file__).resolve().parents[1]
SOURCES = Path(os.environ.get("HELDOUT_SOURCES", str(EXP / "ext/xbrl_scope_additional_development")))
DEST = Path(os.environ.get("HELDOUT_ROOT", str(EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))
COMPANIES = tuple(json.loads((DEST / "selection.json").read_text())["companies"]) if (DEST / "selection.json").exists() else ("213800F59868OZQU6E56", "213800PFUQT4HHFYZJ63")
MIN_FACTS = 6
MARGIN = 120
DPR = 2

PAGE_JS = """() => {
  // page containers of the three ESEF generators met so far: pdf2htmlEX (div.pf), div.page[id] (e.g. AstraZeneca),
  // and div.pageView without ids (Workiva-style; ids are assigned in document order so pages can be located by id)
  // pdf2htmlEX pages carry the class token pf, or pf_N when several converted documents are concatenated
  let pages = Array.from(document.querySelectorAll('div[id]')).filter(p => Array.from(p.classList).some(c => /^pf(_\\d+)?$/.test(c)));
  if (!pages.length) pages = Array.from(document.querySelectorAll('div.page[id]'));
  if (!pages.length) { pages = Array.from(document.querySelectorAll('div.pageView')); pages.forEach((p, i) => { if (!p.id) p.id = 'pv' + i; }); }
  return pages.map(p => {
    const r = p.getBoundingClientRect();
    const facts = Array.from(p.querySelectorAll('*')).filter(n => (n.tagName || '').toLowerCase() === 'ix:nonfraction');
    return {id: p.id, width: r.width, height: r.height, facts: facts.map(f => {
      const b = f.getBoundingClientRect();
      return {id: f.getAttribute('id'), name: f.getAttribute('name'), contextRef: f.getAttribute('contextRef'), unitRef: f.getAttribute('unitRef'),
              scale: f.getAttribute('scale'), sign: f.getAttribute('sign'), format: f.getAttribute('format'), text: (f.textContent || '').trim().slice(0, 40),
              box: [(b.left - r.left) / r.width, (b.top - r.top) / r.height, (b.right - r.left) / r.width, (b.bottom - r.top) / r.height],
              visible: b.width > 0 && b.height > 0};
    })};
  });
}"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    exe = next(p for p in (Path.home() / "Library/Caches/ms-playwright").rglob("chrome-headless-shell") if p.is_file())
    manifest = {"min_facts_per_page": MIN_FACTS, "device_scale_factor": DPR, "reports": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(exe), headless=True)
        for folder in sorted(SOURCES.iterdir()):
            lei, _, date = folder.name.partition("_")
            if lei not in COMPANIES or not (folder / "original.xhtml").exists():
                continue
            report = lei + "_" + date[:4]
            out = DEST / "renders" / report
            out.mkdir(parents=True, exist_ok=True)
            tab = browser.new_page(viewport={"width": 1400, "height": 1600}, device_scale_factor=DPR)
            tab.goto((folder / "original.xhtml").resolve().as_uri(), wait_until="load", timeout=300000)
            pages = tab.evaluate(PAGE_JS)
            if not pages:
                print(report, "NO PAGE CONTAINER FOUND", flush=True); manifest.setdefault("unrendered", []).append(report); tab.close(); continue
            width = int(math.ceil(max(p["width"] for p in pages))) + MARGIN
            tab.set_viewport_size({"width": width, "height": 1600})
            pages = tab.evaluate(PAGE_JS)  # re-measure after the viewport change
            selected = []
            for p in pages:
                numeric = [f for f in p["facts"] if f["visible"]]  # facts without an id attribute (older filings) are matched to the facts file by content in the target step
                if len(numeric) < MIN_FACTS:
                    continue
                loc = tab.locator("#" + p["id"])
                loc.scroll_into_view_if_needed()
                image = out / (p["id"] + ".png")
                loc.screenshot(path=str(image), timeout=60000)
                selected.append({"page_id": p["id"], "image": str(image), "image_sha256": sha(image), "css_width": p["width"], "css_height": p["height"],
                                 "facts": numeric})
                print(report, p["id"], len(numeric), "facts", flush=True)
            tab.close()
            (out / "pages.json").write_text(json.dumps(selected, ensure_ascii=False, indent=1) + "\n")
            manifest["reports"][report] = {"source": str(folder / "original.xhtml"), "source_sha256": sha(folder / "original.xhtml"), "pages_total": len(pages),
                                           "pages_selected": len(selected), "numeric_facts_on_selected_pages": sum(len(s["facts"]) for s in selected),
                                           "viewport_width": width, "pages_file": str(out / "pages.json")}
        browser.close()
    (DEST / "renders/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: (v["pages_selected"], v["numeric_facts_on_selected_pages"]) for k, v in manifest["reports"].items()}))


if __name__ == "__main__":
    main()

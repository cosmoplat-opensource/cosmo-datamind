"""v30 perception patch, step 1: re-render pages wider than the original 1400 px viewport.

The original renderer (render_native_sources.py) used a fixed 1400 CSS px viewport and
element screenshots; content right of the viewport was left unpainted. Pages whose CSS
width exceeds the viewport are re-rendered here with a viewport wider than the page,
same device pixel ratio and same element, so the image size is unchanged and every
target box keeps its coordinates. Run with a Python that has Playwright (anaconda).
"""

import hashlib
import json
import math
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

EXP = Path(__file__).resolve().parents[1]
ALIGN = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/header_transfer_v21"
DEST = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/perception_v30"
SOURCES = EXP / "ext/xbrl_scope_additional_development"
VIEWPORT = 1400
MARGIN = 120


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_for(report):
    lei, year = report.rsplit("_", 1)
    matches = sorted(p for p in SOURCES.glob(lei + "_" + year + "*") if (p / "original.xhtml").exists())
    if len(matches) != 1:
        raise ValueError("Source lookup failed for " + report + ": " + str(matches))
    return matches[0] / "original.xhtml"


def main():
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    todo = []
    for key, page in sorted(pages.items()):
        image = Path(page["image"])
        dpr = 2  # every frozen native render (v2 dpr2 folders and v3) was captured at device scale factor 2
        css_width = page["width"] / dpr
        if css_width > VIEWPORT:
            todo.append((key, page, image, dpr, css_width))
    print("pages wider than viewport:", len(todo), flush=True)
    out = DEST / "renders"
    out.mkdir(parents=True, exist_ok=True)
    exe = next(p for p in (Path.home() / "Library/Caches/ms-playwright").rglob("chrome-headless-shell") if p.is_file())
    manifest = {"viewport_original": VIEWPORT, "margin": MARGIN, "pages": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(exe), headless=True)
        by_report = {}
        for key, page, image, dpr, css_width in todo:
            report = image.parts[[i for i, x in enumerate(image.parts) if x.startswith("native_renders")][0] + 1]
            by_report.setdefault(report, []).append((key, page, image, dpr, css_width))
        for report, items in by_report.items():
            src = source_for(report)
            width = int(math.ceil(max(i[4] for i in items))) + MARGIN
            tab = browser.new_page(viewport={"width": width, "height": 1600}, device_scale_factor=items[0][3])
            tab.goto(src.resolve().as_uri(), wait_until="load", timeout=300000)
            for key, page, image, dpr, css_width in items:
                page_id = image.stem
                loc = tab.locator("#" + page_id)
                loc.scroll_into_view_if_needed()
                target = out / report / (page_id + ".png")
                target.parent.mkdir(parents=True, exist_ok=True)
                loc.screenshot(path=str(target), timeout=60000)
                from PIL import Image
                with Image.open(target) as im:
                    size = im.size
                ok = size == (page["width"], page["height"])
                manifest["pages"][key] = {"report": report, "page_id": page_id, "original_image": str(image), "new_image": str(target),
                                          "new_sha256": sha(target), "size": list(size), "size_matches_original": ok,
                                          "viewport_width": width, "device_scale_factor": dpr, "source": str(src)}
                print(report, page_id, size, "match" if ok else "SIZE MISMATCH", flush=True)
            tab.close()
        browser.close()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print("rendered", len(manifest["pages"]), "pages ->", out)


if __name__ == "__main__":
    main()

"""Page-crop figures for the IJDAR paper, restyled to match make_figures.py.

Functions keep their authoring numbers; outputs carry the manuscript numbers: fig0_teaser -> fig1_teaser,
fig1 -> fig2_example, fig4 -> fig5_segmentation, fig7 -> fig8_errors.  choices.json keys (fig1, fig3, fig4) are
the authoring numbers of make_paper_figures_v1.

Same cell choices as make_paper_figures_v1 (figures/choices.json): the crops are drawn with PIL, the text panels
and legends with matplotlib in the shared typographic style and palette.  Reads references only to label the
figures (an audit use); never feeds them to any method.
"""

import json
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import patches
import numpy as np
from PIL import Image, ImageDraw

import native_structural_prior_v27 as p27
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from score_native_binding_v17 import scoring_references
import make_paper_figures_v1 as V1

R = BANK.parent
OUT = Path(__file__).resolve().parents[1] / "paper_ijdar" / "figures"
CHOICES = json.loads((OUT / "choices.json").read_text())

# palette (shared with make_figures.py)
BLUE, ORANGE, GREEN, VIOLET, RED = "#2a78d6", "#eb6834", "#008300", "#6250d6", "#e34948"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8a86", "#e6e5e1"
rgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
C_TARGET, C_PERIOD, C_UNIT, C_COL, C_ROW, C_CLUSTER, C_WRONG = rgb(RED), rgb(GREEN), rgb(BLUE), rgb(ORANGE), rgb(VIOLET), (214, 150, 110), rgb(ORANGE)
plt.rcParams.update({"font.family": ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"], "font.size": 8, "pdf.fonttype": 42, "savefig.dpi": 300})
W2 = 5.15  # sn-jnl text width (372 pt)
PAD = 0.04  # savefig pad_inches: the figure is drawn PAD narrower on each side, so the saved file is exactly W2 wide


def register_faces():
    """matplotlib reads only the first face of a .ttc, so Helvetica Neue had no bold and every fontweight="bold"
    fell back to the regular face.  Extract the bold face once into the temp directory (as in make_figures.py)."""
    import tempfile
    from matplotlib import font_manager
    src = Path("/System/Library/Fonts/HelveticaNeue.ttc")
    if not src.exists():
        return
    cache = Path(tempfile.gettempdir()) / "mpl_helveticaneue_faces"
    cache.mkdir(exist_ok=True)
    coll = None
    for idx, name in ((1, "HelveticaNeue-Bold.ttf"), (2, "HelveticaNeue-Italic.ttf")):
        f = cache / name
        if not f.exists():
            from fontTools.ttLib import TTCollection
            coll = coll or TTCollection(str(src))
            coll.fonts[idx].save(str(f))
        font_manager.fontManager.addfont(str(f))


register_faces()


def short(q):
    return q.split("}")[-1]


def tuple_rows(b):
    p = b["period"]; period = p.get("instant") or (p["start_date"] + " to " + p["end_date"])
    dims = "; ".join(short(d["axis"]).replace("Axis", "") + " = " + short(d["member"]).replace("Member", "") for d in b.get("dimensions") or []) or "none"
    unit = "/".join(short(u) for u in b["unit"]["numerator"])
    q = Decimal(str(b["quantity"])); value = ("\u2212" if q < 0 else "") + "{:,}".format(abs(int(q))) if q == q.to_integral_value() else str(q)
    return [("concept", short(b["field"])), ("period", period), ("dimensions", dims), ("unit", unit), ("value", value)]


def ink_bbox(clean, rect, grow=0.45):
    """Tight box of the glyph ink of one OCR word box (pixel rect in the crop).  OCR boxes include line spacing and are
    sometimes offset from the glyphs, so a box drawn on them cuts through the letters; this one is measured on the
    undrawn crop: the run of inked rows that overlaps the OCR box most, and the inked columns of that run."""
    x0, y0, x1, y1 = rect; h = max(1.0, y1 - y0)
    X0, X1 = max(0, int(x0) - 2), min(clean.width, int(x1) + 3)
    Y0, Y1 = max(0, int(y0 - grow * h)), min(clean.height, int(y1 + grow * h) + 1)
    if X1 <= X0 or Y1 <= Y0:
        return rect
    a = np.asarray(clean.crop((X0, Y0, X1, Y1)).convert("L")) < 160
    rows = a.any(axis=1)
    runs, r = [], 0
    while r < len(rows):
        if rows[r]:
            q = r
            while q + 1 < len(rows) and rows[q + 1]:
                q += 1
            runs.append((r, q)); r = q + 1
        else:
            r += 1
    if not runs:
        return rect
    lo, hi = y0 - Y0, y1 - Y0
    r0, r1 = max(runs, key=lambda u: (min(u[1] + 1, hi) - max(u[0], lo), -abs((u[0] + u[1]) / 2 - (lo + hi) / 2)))
    cols = np.where(a[r0:r1 + 1].any(axis=0))[0]
    return (X0 + cols.min(), Y0 + r0, X0 + cols.max() + 1, Y0 + r1 + 1)


def box(draw, b, geo, color, width=4, pad=4, clean=None):
    """Outline box b (page fractions).  With clean (the undrawn crop), the outline is drawn around the glyph ink of b,
    pad pixels clear of it, so that it neither touches nor cuts the letters; otherwise around the OCR box itself."""
    left, top, W, H = geo
    rect = (b[0] * W - left, b[1] * H - top, b[2] * W - left, b[3] * H - top)
    if clean is not None:
        x0, y0, x1, y1 = ink_bbox(clean, rect)
        draw.rectangle([x0 - pad - width, y0 - pad - width, x1 + pad + width - 1, y1 + pad + width - 1], outline=color, width=width)
    else:
        draw.rectangle([rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad], outline=color, width=width)


def blank_row(im, y, lo, hi, ignore=()):
    """The pixel row nearest to y within [lo, hi] that carries no glyph ink, so that a cut never slices a line of text.
    Pale table shading and rules count as blank; dark pixels (text) do not."""
    a = np.asarray(im.convert("RGB")).astype(int)
    dark = a.mean(axis=2) < 215  # glyphs, rules and their antialiased edges (pale table shading is lighter)
    coloured = (a.max(axis=2) - a.min(axis=2)) > 70  # drawn outlines (pale table shading stays below this)
    m = dark | coloured
    for c0, c1 in ignore:  # pixel columns of drawn vertical lines, which cross every row
        m[:, max(0, c0):max(0, c1)] = False
    inkrows = m.sum(axis=1)
    def ink(r):
        return int(inkrows[r])
    best = None
    for dy in range(0, max(y - lo, hi - y) + 1):
        for r in (y - dy, y + dy):
            if lo <= r < min(hi, im.height) and ink(r) == 0:  # the middle of this blank run, clear of both lines
                r0, r1 = r, r
                while r0 - 1 >= max(lo, 0) and ink(r0 - 1) == 0:
                    r0 -= 1
                while r1 + 1 < min(hi, im.height) and ink(r1 + 1) == 0:
                    r1 += 1
                return (r0 + r1) // 2
            if lo <= r < min(hi, im.height) and (best is None or ink(r) < best[0]):
                best = (ink(r), r)
    return best[1] if best else y


def cut_middle(im, keep_top_px, keep_bottom_px, ignore=()):
    if keep_top_px + keep_bottom_px + 80 >= im.height:
        return im
    end = blank_row(im, im.height - 1, im.height - 60, im.height, ignore)  # do not end the crop inside a line of text either
    start = blank_row(im, im.height - keep_bottom_px, im.height - keep_bottom_px - 30, im.height - keep_bottom_px + 30, ignore)
    keep_top_px = blank_row(im, keep_top_px, keep_top_px - 30, keep_top_px + 30, ignore)
    im = im.crop((0, 0, im.width, end + 1)); keep_bottom_px = im.height - start
    top = im.crop((0, 0, im.width, keep_top_px)); bottom = im.crop((0, im.height - keep_bottom_px, im.width, im.height))
    out = Image.new("RGB", (im.width, top.height + 28 + bottom.height), "white")
    out.paste(top, (0, 0)); out.paste(bottom, (0, top.height + 28))
    d = ImageDraw.Draw(out)
    for x in range(0, im.width, 24):
        d.line([(x, top.height + 14), (x + 12, top.height + 14)], fill=rgb(MUTED), width=3)
    return out


def squeeze(im, keep=None):
    """Shorten every run of blank (white) pixel rows to keep pixels, so that empty page space does not take print height."""
    keep = keep or int(0.025 * im.width)
    a = np.asarray(im.convert("RGB"))
    blank = (a.min(axis=2) > 245).all(axis=1)
    rows, r = [], 0
    while r < im.height:
        if blank[r]:
            q = r
            while q + 1 < im.height and blank[q + 1]:
                q += 1
            rows.extend(range(r, min(q + 1, r + keep))); r = q + 1
        else:
            rows.append(r); r += 1
    if len(rows) == im.height:
        return im
    return Image.fromarray(a[rows])


def enlarged_strip(im, d, rect, share=0.92, kmax=3.0):
    """Enlarged copy of rect (pixels of im) as a separate white panel, right-aligned, framed; a dashed frame drawn on im
    marks where it comes from.  Crop the strip before drawing the frame, so that the frame does not appear in it."""
    strip = im.crop(rect)
    k = min(kmax, share * im.width / strip.width)
    strip = strip.resize((int(strip.width * k), int(strip.height * k)), Image.LANCZOS)
    inset = Image.new("RGB", (im.width, strip.height + 16), "white")
    inset.paste(strip, (im.width - strip.width - 8, 8))
    di = ImageDraw.Draw(inset)
    di.rectangle([im.width - strip.width - 9, 7, im.width - 8, strip.height + 8], outline=rgb(MUTED), width=2)
    for x in range(rect[0], rect[2], 16):
        d.line([(x, rect[1]), (min(x + 8, rect[2]), rect[1])], fill=rgb(MUTED), width=2); d.line([(x, rect[3]), (min(x + 8, rect[2]), rect[3])], fill=rgb(MUTED), width=2)
    for y_ in range(rect[1], rect[3], 16):
        d.line([(rect[0], y_), (rect[0], min(y_ + 8, rect[3]))], fill=rgb(MUTED), width=2); d.line([(rect[2], y_), (rect[2], min(y_ + 8, rect[3]))], fill=rgb(MUTED), width=2)
    return inset, k


def compose(panels, name, legend, width=W2 - 2 * PAD, trim=None):
    """panels: list of (image, label, rows); rows are (key, value) pairs shown under the crop. legend: [(color, text)].
    An image whose label is None is an enlargement (enlarged_strip): it is drawn without the outer frame."""
    panels = [(im if label is None else squeeze(im), label, rows) for im, label, rows in panels]
    ROW, GAP, LAB = 0.155, 0.12, 0.2
    LEG = 0.26 + 0.16 * ((len(legend) + 1) // 2 - 1) if legend else 0.0
    heights = []
    for im, label, rows in panels:
        heights.append((LAB if label else 0.0) + width * im.height / im.width + (ROW * len(rows) + 0.06 if rows else 0.0))
    total = sum(heights) + GAP * (len(panels) - 1) + (LEG if legend else 0.0)
    fig = plt.figure(figsize=(width, total))
    y = 1.0
    for (im, label, rows), h in zip(panels, heights):
        if label:
            fig.text(0.0, y - 0.12 / total, label, fontsize=8.5, fontweight="bold", color=INK, va="center", ha="left")
            y -= LAB / total
        ih = width * im.height / im.width / total
        ax = fig.add_axes([0.0, y - ih, 1.0, ih]); ax.imshow(im); ax.axis("off")
        if label is not None:
            ax.add_patch(patches.Rectangle((0, 0), im.width - 1, im.height - 1, fill=False, edgecolor=GRID, linewidth=0.6))
        y -= ih + 0.06 / total
        for k, v in rows:
            fig.text(0.012, y - ROW / total / 2, k, fontsize=7.3, color=MUTED, va="center", ha="left")
            fig.text(0.22, y - ROW / total / 2, v, fontsize=7.3, color=INK, va="center", ha="left")
            y -= ROW / total
        y -= GAP / total
    if legend:
        nrow = (len(legend) + 1) // 2
        for k, (color, text) in enumerate(legend):
            r, c = divmod(k, 2); x = 0.012 + c * 0.5; yl = (0.075 + 0.16 * (nrow - 1 - r)) / total
            fig.patches.append(patches.Rectangle((x, yl - 0.055 / total), 0.012, 0.11 / total, transform=fig.transFigure, facecolor="none", edgecolor=color, linewidth=1.6))
            fig.text(x + 0.018, yl, text, fontsize=6.8, color=INK2, va="center", ha="left")
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"{name}.{ext}", bbox_inches="tight", pad_inches=PAD, facecolor="white")
    plt.close(fig); print("wrote", name)


def load():
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    pages = json.loads((PERCEPTION / "localization/public_pages.json").read_text())
    anchors = json.loads((PERCEPTION / "localization/public_anchors.json").read_text())
    contexts = json.loads((BANK / "prior_v27/table_contexts.json").read_text())
    teachers = json.loads((p27.TEACHERS / "training_only/past_binding_feedback.json").read_text())
    by_id = {t["task_id"]: (t, item) for t, item in zip(tasks, inventory)}
    return tasks, by_id, pages, anchors, contexts, teachers


def fig1(tasks, by_id, pages, anchors, contexts, teachers):
    tid = CHOICES["fig1"]["task_id"]; t, item = by_id[tid]; tb = teachers[tid]["teacher_binding"]
    glyphs = {g for q in t["quantity_candidates"] for g in q.get("source_glyph_hypotheses", [])}
    page = pages[anchors[tid]["page_hash"]]
    im, geo = V1.crop(page, t["target_box"], above=0.16, below=0.04)
    clean = im.copy(); d = ImageDraw.Draw(im)
    inside = lambda a, b: a != b and a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]
    for c, color in (("period", C_PERIOD), ("unit_quantity", C_UNIT)):
        boxes = []
        for g in item["components"][c]:
            val = g["value"]
            hit = (c == "period" and val == tb["period"]) or (c == "unit_quantity" and val and val["unit"] == tb["unit"] and val["quantity"].split(".")[0] == str(tb["quantity"]).split(".")[0])
            if hit:
                boxes += [tuple(w["anchor"]["box"]) for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box")]
        for b in set(boxes):
            if not any(inside(b, o) for o in boxes):  # nested anchors share the outer box
                box(d, b, geo, color, 2, 3, clean=clean)
    box(d, t["target_box"], geo, C_TARGET, 4, 5, clean=clean)
    im = im.crop((0, int(0.03 * im.height), im.width, im.height))
    assert len(glyphs) == 1, glyphs
    rows = [("printed", next(iter(glyphs)))] + tuple_rows(tb)
    compose([(im, "", rows)], "fig2_example", [(RED, "marked cell"), (GREEN, "witnesses of the filed period"), (BLUE, "witnesses of the unit and scale")])


def fig4(tasks, by_id, pages, anchors, contexts, teachers):
    tid = CHOICES["fig3"]["task_id"]; t, item = by_id[tid]; tb = teachers[tid]["teacher_binding"]
    page = pages[anchors[tid]["page_hash"]]; words = page["words"]; x0, y0, x1, y1 = t["target_box"]
    wit = [w for g in item["components"]["period"] if g["value"] == tb["period"] for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box")]
    left, right = p27.column_bounds(words, t["target_box"])
    top_y = min(w["anchor"]["box"][1] for w in wit)
    above_used = min(0.5, y0 - top_y + 0.03)
    im, geo = V1.crop(page, t["target_box"], above=above_used, below=0.06)
    clean = im.copy(); d = ImageDraw.Draw(im); L, T, W, H = geo
    hdr_bottom = int((max(w["anchor"]["box"][3] for w in wit if w["anchor"]["box"][1] < y0) - (y0 - above_used)) * H) + 24
    for w in words:
        b = w["box"]; cx = (b[0] + b[2]) / 2
        on_target = min(b[2], x1) > max(b[0], x0) and min(b[3], y1) > max(b[1], y0)
        if left <= cx <= right and any(ch.isdigit() for ch in w["text"]) and (b[1] - (y0 - above_used)) * H > hdr_bottom and not on_target:
            box(d, b, geo, C_CLUSTER, 2, 2, clean=clean)
    rl = [w["box"] for w in words if w["box"][2] <= x0 and min(w["box"][3], y1) - max(w["box"][1], y0) > 0.4 * (y1 - y0) and any(ch.isalpha() for ch in w["text"])]
    if rl:
        box(d, [min(b[0] for b in rl), min(b[1] for b in rl), max(b[2] for b in rl), max(b[3] for b in rl)], geo, C_ROW, 3, 3, clean=clean)
    wb = [tuple(w["anchor"]["box"]) for w in wit]
    inside = lambda a, b: a != b and a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]
    for b in set(wb):
        if not any(inside(b, o) for o in wb):  # nested anchors share the outer box
            box(d, b, geo, C_PERIOD, 2, 1, clean=clean)
    box(d, t["target_box"], geo, C_TARGET, 4, 5, clean=clean)
    # the column headers print at about 3 pt in the crop: an enlarged copy goes under it (as in fig8_errors)
    wit_bottom = max(w["anchor"]["box"][3] for w in wit if w["anchor"]["box"][1] < y0)
    band = [w["box"] for w in words if w["box"][1] >= top_y - 0.004 and w["box"][1] < wit_bottom + 0.02 and w["box"][0] >= left - 0.12]
    sx0, sx1 = min(b[0] for b in band) - 0.008, max(b[2] for b in band) + 0.008
    sy0, sy1 = top_y - 0.005, max(b[3] for b in band) + 0.004
    inset, inset_k = enlarged_strip(im, d, [int(sx0 * W - L), int(sy0 * H - T), int(sx1 * W - L), int(sy1 * H - T)])
    lines = [int(left * W - L), int(right * W - L)]
    for xl in lines:  # the column bounds, drawn after the strip is taken so that their tops do not appear in it
        d.line([(xl, hdr_bottom), (xl, im.height)], fill=C_COL, width=3)
    hdr_px = int((max(w["anchor"]["box"][3] for w in wit if w["anchor"]["box"][1] < y0) - (y0 - above_used)) * H) + 60
    tgt_px = int((y1 + 0.06 - y0) * H) + int(0.09 * H)
    im = cut_middle(im, hdr_px, tgt_px, ignore=[(xl - 3, xl + 4) for xl in lines])
    ctx = contexts[tid]
    rows = [("column header (OCR)", " | ".join(ctx["column"][:3])), ("row label (OCR)", ctx["row"][:70]), ("filed period", tb["period"].get("instant") or tb["period"]["start_date"] + " to " + tb["period"]["end_date"])]
    rows = [("enlarged", "the dashed region of the crop, %.1f\u00d7" % inset_k)] + rows
    compose([(im, "", []), (inset, None, rows)], "fig5_segmentation", [(RED, "marked cell"), (ORANGE, "column bounds from the numeric clusters"), ("#%02x%02x%02x" % C_CLUSTER, "numeric cluster members"), (VIOLET, "row label"), (GREEN, "witnesses of the filed period")])


def predictions(version, policy="hash_prefix", method="semantic_memory", seed=19, budget=10):
    out = {}
    root = R / f"common_candidate_{version}/learning_v1/predictions/{policy}"
    for c in root.iterdir():
        f = c / method / str(seed) / f"{budget}.json"
        assert f.exists(), f
        for q in json.loads(f.read_text())["predictions"]:
            out[q["task_id"]] = q["predicted_binding"]
    return out


def fig7(tasks, by_id, pages, anchors, contexts, teachers):
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    refs = scoring_references(future)[0]
    before, after = predictions("v26"), predictions("v32")
    ta, tb_ = CHOICES["fig4"]["a"], CHOICES["fig4"]["b"]
    panels = []
    for label, tid, pred, ref in (("a  Neighbouring-year column (v26), corrected by the structural prior (v27)", ta, before[ta], refs[ta]), ("b  Sibling concept (v32), not corrected by the structural prior", tb_, after[tb_], refs[tb_])):
        t, item = by_id[tid]; page = pages[anchors[tid]["page_hash"]]
        ys = [w["anchor"]["box"][1] for g in item["components"]["period"] if g["value"] in (ref["period"], pred["period"]) for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box") and w["anchor"]["box"][1] < t["target_box"][1]]
        above = min(0.5, t["target_box"][1] - min(ys) + 0.03) if ys else 0.1
        im, geo = V1.crop(page, t["target_box"], above=above, below=0.04)
        clean = im.copy(); d = ImageDraw.Draw(im)
        if label.startswith("a"):
            for g in item["components"]["period"]:
                color = C_PERIOD if g["value"] == ref["period"] else (C_WRONG if g["value"] == pred["period"] else None)
                if color:
                    for w in g["witnesses"]:
                        a_ = w.get("anchor")
                        if a_ and a_.get("box"):
                            box(d, a_["box"], geo, color, 2, 2, clean=clean)
        if label.startswith("b"):  # the row label that supports either concept
            x0, y0, x1, y1 = t["target_box"]
            rl = [w["box"] for w in page["words"] if w["box"][2] <= x0 and min(w["box"][3], y1) - max(w["box"][1], y0) > 0.4 * (y1 - y0) and any(ch.isalpha() for ch in w["text"])]
            if rl:
                box(d, [min(b[0] for b in rl), min(b[1] for b in rl), max(b[2] for b in rl), max(b[3] for b in rl)], geo, C_ROW, 3, 3, clean=clean)
        box(d, t["target_box"], geo, C_TARGET, 4, 5, clean=clean)
        inset = None
        if label.startswith("a"):  # enlarged copy of the two year headers, which print at about 2.5 pt in the crop
            L, T, W, H = geo
            hb = [w["anchor"]["box"] for g in item["components"]["period"] if g["value"] in (ref["period"], pred["period"]) for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box") and w["anchor"]["box"][1] < t["target_box"][1]]
            top = max(b[1] for b in hb)  # the year header line nearest above the cell (the one coloured in the crop)
            hb = [b for b in hb if abs(b[1] - top) < 0.5 * (b[3] - b[1])]
            hx0, hy0, hx1, hy1 = min(b[0] for b in hb), min(b[1] for b in hb), max(b[2] for b in hb), max(b[3] for b in hb)
            hgt = hy1 - hy0
            # the strip: from the "Notes" column to the right edge of the last header, from the year line to below the unit line
            notes = [w["box"] for w in page["words"] if w["text"].lower().startswith("notes") and abs(w["box"][1] - hy0) < 4 * hgt]
            sx0 = (min(b[0] for b in notes) if notes else hx0 - 0.08) - 0.01
            sx1, sy0, sy1 = hx1 + 0.012, hy0 - 0.6 * hgt, hy1 + 2.7 * hgt
            rect = [int(sx0 * W - L), int(sy0 * H - T), int(sx1 * W - L), int(sy1 * H - T)]
            inset, inset_k = enlarged_strip(im, d, rect, share=0.62)
        if ys:
            L, T, W, H = geo
            hdr_px = int((max(w["anchor"]["box"][3] for g in item["components"]["period"] if g["value"] in (ref["period"], pred["period"]) for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box") and w["anchor"]["box"][1] < t["target_box"][1]) - (t["target_box"][1] - above)) * H) + 60
            tgt_px = int((t["target_box"][3] + 0.04 - t["target_box"][1]) * H) + int(0.055 * H)
            im = cut_middle(im, hdr_px, tgt_px)
        if label.startswith("a"):
            pa = pred["period"]; pr = ref["period"]
            per27 = predictions("v27")[tid]["period"]; assert per27 == ref["period"], (per27, ref["period"])
            rows = [("before the prior (v26)", "period " + (pa.get("instant") or pa.get("end_date"))), ("after the prior (v27)", "period " + (per27.get("instant") or per27.get("end_date")) + ", as filed")]
        else:
            rows = [("system concept (v32)", short(pred["field"])), ("filed concept", short(ref["field"]))]
        panels.append((im, label, rows if inset is None else []))
        if inset is not None:
            panels.append((inset, None, [("enlarged", "the dashed region of the crop, %.1f\u00d7" % inset_k)] + rows))
    compose(panels, "fig8_errors", [(RED, "marked cell"), (ORANGE, "column header chosen before the prior (a)"), (GREEN, "witness of the filed period (a)"), (VIOLET, "row label (b)")])




def fig0_teaser(tasks, by_id, pages, anchors, contexts, teachers, width=W2 - 0.2, _depth=0):
    """Teaser: the task on the left (a marked cell and its filed fact), the measurement on the right.  Drawn at the
    width whose tight bounding box is the text width, so that LaTeX places it at 100% (text sizes are print sizes)."""
    from matplotlib import patches as P
    tid = CHOICES["fig1"]["task_id"]; t, item = by_id[tid]; tb = teachers[tid]["teacher_binding"]
    page = pages[anchors[tid]["page_hash"]]
    im, geo = V1.crop(page, t["target_box"], above=0.16, below=0.04)
    L, T, W, H = geo
    d = ImageDraw.Draw(im)
    box(d, t["target_box"], geo, C_TARGET, 6, 9)
    # keep the marked column and the Total column next to it; drop the empty left margin, so the crop prints larger
    right = min(im.width, int(t["target_box"][2] * W - L + 0.068 * im.width))
    inrows = [w["box"] for w in page["words"] if w["box"][1] * H >= T and w["box"][3] * H <= T + im.height]
    left = max(0, int(min(b[0] for b in inrows) * W - L) - 12) if inrows else 0
    im = im.crop((left, int(0.03 * im.height), right, im.height))
    fig = plt.figure(figsize=(width, 3.1))
    # left: page crop
    ax = fig.add_axes([0.0, 0.635, 0.54, 0.30]); ax.imshow(im); ax.axis("off")
    ax.add_patch(P.Rectangle((0, 0), im.width - 1, im.height - 1, fill=False, edgecolor=GRID, linewidth=0.6))
    fig.text(0.0, 0.995, "a  A marked cell and the fact the issuer filed for it", fontsize=8, fontweight="bold", va="top")
    chips = [(k, v.split(" = ")[-1].replace(" to ", " \u2013 "), c) for (k, v), c in zip(tuple_rows(tb), (BLUE, GREEN, VIOLET, ORANGE, RED))]
    renderer = fig.canvas.get_renderer(); PADX, CH = 0.010, 0.062
    rows_of_chips = (chips[:2], chips[2:])
    for r, row in enumerate(rows_of_chips):
        x, yb = 0.0, 0.50 - 0.135 * r
        for k, v, c in row:
            txt = fig.text(x, yb + CH / 2, v, fontsize=6.6, color="white", ha="left", va="center", zorder=5)
            w = txt.get_window_extent(renderer).width / fig.bbox.width + 2 * PADX
            txt.set_x(x + PADX)
            fig.patches.append(P.FancyBboxPatch((x, yb), w, CH, boxstyle="round,pad=0,rounding_size=0.008", transform=fig.transFigure, facecolor=c, edgecolor="none", zorder=4))
            fig.text(x + w / 2, yb + CH + 0.028, k, fontsize=6.4, color=MUTED, ha="center", va="center")
            x += w + 0.010
    glyphs = {g for q in t["quantity_candidates"] for g in q.get("source_glyph_hypotheses", [])}
    assert len(glyphs) == 1, glyphs
    fig.text(0.0, 0.255, "Printed as %s: nothing in the cell names the concept, the\nperiod or the dimension, and the scale comes from the \u00a3m\ncolumn header." % next(iter(glyphs)), fontsize=6.4, color=INK2, va="center", linespacing=1.3)
    fig.text(0.0, 0.10, "Evaluation cells: 560 development (14 companies), 160 held-out (2),\n800 sealed (10 of 60 opened); references are the issuers' filings.", fontsize=6.4, color=INK2, va="center", linespacing=1.3)
    # right: the measurement
    ax = fig.add_axes([0.73, 0.20, 0.265, 0.62])
    fig.text(0.585, 0.995, "b  v34 vs. the two LLM controls", fontsize=8, fontweight="bold", va="top", linespacing=1.25)
    # exact-tuple and concept accuracies at budget 10 (Tables 6, 7, 8) and the budget-10 paired differences of Sections 5.3, 5.6 and 5.7
    # computed from the frozen predictions and answers (make_figures.load_all) and checked against the printed values
    printed = [("Development (14)", (39.8, 53.2), (64.8, 84.6), (13.4, 19.8)), ("Held-out (2)", (55.0, 64.2), (71.9, 86.9), (9.2, 15.0)), ("Sealed (10, opened once)", (45.8, 46.9), (64.9, 81.4), (1.1, 16.5))]
    data = []
    for (name, *_), split in zip(printed, ("dev", "heldout", "sealed")):
        d = MF().SPLIT_DATA[split]; refs = d["refs"]; reps = [d["sys"]["v34"][(s, 10)] for s in MF().SEEDS]; llm = d["llmc"]["fewshot"]; ann = d["llmc"]["annotated"]
        a = lambda rows, c: 100 * np.mean([float(r[t][c]) for r in rows for t in refs])
        ex, co = (a([llm], "semantic_tuple_exact"), a(reps, "semantic_tuple_exact")), (a([llm], "field"), a(reps, "field"))
        exa, coa = a([ann], "semantic_tuple_exact"), a([ann], "field")
        data.append((name, ex, co, (ex[1] - ex[0], co[1] - co[0]), (exa, coa)))  # paired mean difference = difference of means (same targets)
    for got, want in zip(data, printed):
        assert all(abs(g - w) < 0.051 for gg, ww in zip(got[1:4], want[1:]) for g, w in zip(gg, ww)), (got, want)
    # annotated control, budget 10 (Tables 3-5): exact 44.8 / 58.8 / 52.2, concept 72.7 / 80.0 / 73.9
    for got, want in zip(data, ((44.8, 72.7), (58.8, 80.0), (52.2, 73.9))):
        assert all(abs(g - w) < 0.051 for g, w in zip(got[4], want)), (got[4], want)
    y = 0.0
    fmt = lambda v: ("%+.1f" % v).replace("-", "\u2212")
    for name, exact, concept, paired, (exa, coa) in data:
        ax.text(28.5, y + 0.45, name, fontsize=6.6, color=INK, fontweight="bold", ha="right", va="center", clip_on=False)
        for comp, (llm, sysv), anv, diff, yoff in (("exact tuple", exact, exa, paired[0], 0.0), ("concept", concept, coa, paired[1], -0.36)):
            yy = y + yoff
            lo, hi = min(llm, sysv, anv), max(llm, sysv, anv)
            ax.plot([lo, hi], [yy, yy], color=GRID, linewidth=2.2, zorder=2, solid_capstyle="round")
            ax.scatter([sysv], [yy], s=22, color=BLUE, edgecolor="white", linewidth=0.6, zorder=7); ax.scatter([llm], [yy], s=20, marker="s", facecolor="white", edgecolor=ORANGE, linewidth=1.3, zorder=5)
            ax.scatter([anv], [yy], s=22, marker="D", facecolor="white", edgecolor=VIOLET, linewidth=1.3, zorder=5)
            ax.text(28.5, yy, comp, fontsize=6.4, color=INK2, ha="right", va="center", clip_on=False)
            ax.text(112, yy, fmt(diff), fontsize=6.6, color=ORANGE, ha="right", va="center")
            ax.text(135, yy, fmt(sysv - anv), fontsize=6.6, color=VIOLET, ha="right", va="center")
        y -= 1.3
    ax.set_yticks([])
    ax.set_xlim(30, 136); ax.set_ylim(y + 0.75, 0.75)
    ax.set_xticks([40, 60, 80, 100]); ax.set_xlabel("accuracy (%), budget 10", fontsize=7); ax.spines["bottom"].set_bounds(30, 100)
    grid(ax, "x")
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.legend(handles=[plt.Line2D([], [], marker="s", color="white", markeredgecolor=ORANGE, markeredgewidth=1.3, linestyle="", markersize=4.5, label="history-matched control"), plt.Line2D([], [], marker="D", color="white", markeredgecolor=VIOLET, markeredgewidth=1.3, linestyle="", markersize=4.2, label="annotated control"), plt.Line2D([], [], marker="o", color=BLUE, linestyle="", markersize=5, label="typed system (v34)")],
              loc="upper left", bbox_to_anchor=(-0.42, -0.2), ncol=1, fontsize=6.5, handletextpad=0.3, frameon=False)
    fig.canvas.draw(); w = fig.get_tightbbox(fig.canvas.get_renderer()).width
    target = W2 - 2 * PAD
    if abs(w - target) > 0.01 and _depth < 6:
        plt.close(fig)
        return fig0_teaser(tasks, by_id, pages, anchors, contexts, teachers, width=width + 0.9 * (target - w), _depth=_depth + 1)
    for ext in ("png", "pdf"):
        fig.savefig(OUT / f"fig1_teaser.{ext}", bbox_inches="tight", pad_inches=PAD, facecolor="white")
    plt.close(fig); print("wrote fig1_teaser (%.2f in)" % (w + 2 * PAD))


def grid(ax, axis="y"):
    ax.grid(True, axis=axis, color=GRID, linewidth=0.5, zorder=0); ax.set_axisbelow(True)


_MF = []


def MF():
    """make_figures with its run data loaded (once); imported inside rc_context so that its rcParams do not leak here."""
    if not _MF:
        import sys
        sys.path.insert(0, str(OUT))
        with plt.rc_context():
            import make_figures
            make_figures.load_all()
        _MF.append(make_figures)
    return _MF[0]


if __name__ == "__main__":
    plt.rcParams.update({"axes.edgecolor": INK2, "axes.linewidth": 0.6, "xtick.labelsize": 6.5, "xtick.color": INK2, "ytick.color": INK2})
    data = load()
    for fn in (fig0_teaser, fig1, fig4, fig7):
        fn(*data)

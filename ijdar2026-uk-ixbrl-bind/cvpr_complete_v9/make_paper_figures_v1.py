"""Figures for the IJDAR paper from the released development data.

fig1  a statement page region with one target cell, its filed tuple and the places on the page that witness it
fig3  the structural segmentation: column bounds from numeric clusters, header anchors above the column,
      the row label to the left, the period witnesses of the reference
fig4  two error cases: a neighbouring-year column chosen before the structural prior and corrected after it,
      and a sibling-concept confusion that survives

Reads references only to label the figures (an audit use); never feeds them to any method.
Output: paper_ijdar/figures/*.png and figures/choices.json with the task ids and tuples used.
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import native_structural_prior_v27 as p27
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from llm_fewshot_baseline_v1 import TEACHERS
from score_native_binding_v17 import component_correct, scoring_references

R = BANK.parent
OUT = Path(__file__).resolve().parents[1] / "paper_ijdar" / "figures"
FONT = "/System/Library/Fonts/Helvetica.ttc"
GREEN, RED, BLUE, ORANGE, GREY = (22, 142, 78), (200, 55, 45), (74, 95, 243), (217, 130, 43), (120, 120, 120)


def load():
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    pages = json.loads((PERCEPTION / "localization/public_pages.json").read_text())
    anchors = json.loads((PERCEPTION / "localization/public_anchors.json").read_text())
    contexts = json.loads((BANK / "prior_v27/table_contexts.json").read_text())
    teachers = json.loads(TEACHERS.read_text())
    return tasks, inventory, pages, anchors, contexts, teachers


def short(qname):
    return qname.split("}")[-1]


def tuple_text(b):
    p = b["period"]; period = p.get("instant") or (p["start_date"] + " to " + p["end_date"])
    dims = "; ".join(short(d["axis"]).replace("Axis", "") + " = " + short(d["member"]).replace("Member", "") for d in b.get("dimensions") or []) or "none"
    unit = "/".join(short(u) for u in b["unit"]["numerator"])
    return ["concept   " + short(b["field"]), "period    " + period, "dims      " + dims, "unit      " + unit, "value     " + str(b["quantity"])]


def crop(page, tb, above=0.14, below=0.05, x0=0.0, x1=1.0):
    im = Image.open(page["image"]).convert("RGB")
    W, H = im.size
    top, bottom = max(0, int((tb[1] - above) * H)), min(H, int((tb[3] + below) * H))
    left, right = int(x0 * W), int(x1 * W)
    return im.crop((left, top, right, bottom)), (left, top, W, H)


def cut_middle(im, keep_top_px, keep_bottom_px):
    """Drop the rows between keep_top_px and image.height-keep_bottom_px, marking the cut with a dashed band."""
    if keep_top_px + keep_bottom_px + 80 >= im.height:
        return im
    top = im.crop((0, 0, im.width, keep_top_px)); bottom = im.crop((0, im.height - keep_bottom_px, im.width, im.height))
    out = Image.new("RGB", (im.width, top.height + 28 + bottom.height), "white")
    out.paste(top, (0, 0)); out.paste(bottom, (0, top.height + 28))
    d = ImageDraw.Draw(out)
    for x in range(0, im.width, 24):
        d.line([(x, top.height + 14), (x + 12, top.height + 14)], fill=GREY, width=3)
    return out


def box(draw, b, geo, color, width=4, pad=4):
    left, top, W, H = geo
    draw.rectangle([b[0] * W - left - pad, b[1] * H - top - pad, b[2] * W - left + pad, b[3] * H - top + pad], outline=color, width=width)


def panel(image, lines, title=None, width=None):
    """Put text lines in a white band under the image."""
    font = ImageFont.truetype(FONT, 30); tfont = ImageFont.truetype(FONT, 34)
    w = width or image.width
    band = 44 * len(lines) + (56 if title else 0) + 30
    out = Image.new("RGB", (w, image.height + band), "white")
    out.paste(image, (0, 0))
    d = ImageDraw.Draw(out)
    y = image.height + 12
    if title:
        d.text((20, y), title, fill=(30, 30, 30), font=tfont); y += 56
    for ln in lines:
        d.text((28, y), ln, fill=(30, 30, 30), font=font); y += 44
    return out


def fig1(tasks, inventory, pages, anchors, contexts, teachers):
    # a dimensioned, parenthesised teacher cell on a statement-of-changes-in-equity page
    best = None
    for t, item in zip(tasks, inventory):
        if t["source_year"] != 2023:
            continue
        tb = teachers.get(t["task_id"], {}).get("teacher_binding")
        if not tb or not tb.get("dimensions"):
            continue
        glyphs = {g for q in t["quantity_candidates"] for g in q.get("source_glyph_hypotheses", [])}
        if not any(g.startswith("(") for g in glyphs):
            continue
        if "equity" not in " ".join(contexts[t["task_id"]]["blocks"]).lower() and "equity" not in contexts[t["task_id"]]["column"][0].lower() if contexts[t["task_id"]]["column"] else True:
            pass
        best = (t, item, tb, glyphs); break
    t, item, tb, glyphs = best
    page = pages[anchors[t["task_id"]]["page_hash"]]
    im, geo = crop(page, t["target_box"], above=0.16, below=0.04)
    d = ImageDraw.Draw(im)
    box(d, t["target_box"], geo, RED, 5)
    # witnesses of the filed period and unit
    for c, color in (("period", GREEN), ("unit_quantity", BLUE)):
        for g in item["components"][c]:
            val = g["value"]
            hit = (c == "period" and val == tb["period"]) or (c == "unit_quantity" and val and val["unit"] == tb["unit"] and val["quantity"].split(".")[0] == str(tb["quantity"]).split(".")[0])
            if not hit:
                continue
            for w in g["witnesses"]:
                a = w.get("anchor")
                if a and a.get("box"):
                    box(d, a["box"], geo, color, 3, 2)
    lines = ["printed   " + sorted(glyphs)[0]] + tuple_text(tb) + ["red: target cell   green: period witnesses   blue: unit / scale witnesses"]
    panel(im, lines, "Filed fact of the marked cell (from the issuer's iXBRL)").save(OUT / "fig1_example.png")
    return {"fig1": {"task_id": t["task_id"], "report": t["report"], "tuple": tb}}


def fig3(tasks, inventory, pages, anchors, contexts, teachers):
    # a teacher cell on a page with several year columns, period witnessed by a column header
    best = None
    for t, item in zip(tasks, inventory):
        if t["source_year"] != 2023:
            continue
        tb = teachers.get(t["task_id"], {}).get("teacher_binding")
        ctx = contexts[t["task_id"]]
        if not tb or len(ctx["column"]) < 1 or not ctx["row"]:
            continue
        page = pages[anchors[t["task_id"]]["page_hash"]]
        words = page["words"]
        wit = [w for g in item["components"]["period"] if g["value"] == tb["period"] for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box")]
        if len(wit) >= 1 and any(w["anchor"]["box"][1] < t["target_box"][1] - 0.02 for w in wit):
            best = (t, item, tb, page, wit); break
    t, item, tb, page, wit = best
    words = page["words"]; x0, y0, x1, y1 = t["target_box"]
    left, right = p27.column_bounds(words, t["target_box"])
    top_y = min(w["anchor"]["box"][1] for w in wit)
    above_used = min(0.5, y0 - top_y + 0.03)
    im, geo = crop(page, t["target_box"], above=above_used, below=0.06)
    d = ImageDraw.Draw(im)
    L, T, W, H = geo
    # column bounds
    d.line([(left * W - L, 0), (left * W - L, im.height)], fill=ORANGE, width=3)
    d.line([(right * W - L, 0), (right * W - L, im.height)], fill=ORANGE, width=3)
    # numeric cluster words inside the column (light boxes)
    for w in words:
        b = w["box"]; cx = (b[0] + b[2]) / 2
        if left <= cx <= right and any(ch.isdigit() for ch in w["text"]):
            box(d, b, geo, (245, 200, 160), 2, 1)
    # row label words: same row, to the left of the cell
    for w in words:
        b = w["box"]
        if b[2] <= x0 and min(b[3], y1) - max(b[1], y0) > 0.4 * (y1 - y0):
            box(d, b, geo, BLUE, 3, 2)
    for w in wit:
        box(d, w["anchor"]["box"], geo, GREEN, 4, 3)
    box(d, t["target_box"], geo, RED, 5)
    hdr_px = int((max(w["anchor"]["box"][3] for w in wit if w["anchor"]["box"][1] < y0) - (y0 - above_used)) * H) + 60
    tgt_px = int((y1 + 0.06 - y0) * H) + int(0.09 * H)
    im = cut_middle(im, hdr_px, tgt_px)
    ctx = contexts[t["task_id"]]
    lines = ["orange: column bounds from the numeric clusters    blue: row label    green: anchors of the filed period",
             "column header read:  " + " | ".join(ctx["column"][:3]), "row label read:      " + ctx["row"][:70], "filed period:        " + (tb["period"].get("instant") or tb["period"]["start_date"] + " to " + tb["period"]["end_date"])]
    panel(im, lines, "Structural segmentation and period witnesses").save(OUT / "fig3_segmentation.png")
    return {"fig3": {"task_id": t["task_id"], "report": t["report"], "column_bounds": [left, right]}}


def predictions(version, policy="hash_prefix", method="semantic_memory", seed=19, budget=10):
    out = {}
    root = R / f"common_candidate_{version}/learning_v1/predictions/{policy}"
    for c in root.iterdir():
        f = c / method / str(seed) / f"{budget}.json"
        if f.exists():
            for q in json.loads(f.read_text())["predictions"]:
                out[q["task_id"]] = q["predicted_binding"]
    return out


def fig4(tasks, inventory, pages, anchors, contexts, teachers):
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    refs = scoring_references(future)[0]
    before, after = predictions("v26"), predictions("v32")
    by_id = {t["task_id"]: (t, item) for t, item in zip(tasks, inventory)}
    # (a) neighbouring-year period error fixed by the structural prior
    case_a = None
    for tid, ref in refs.items():
        b, a = before.get(tid), after.get(tid)
        if not b or not a:
            continue
        cb, ca = component_correct(b, ref), component_correct(a, ref)
        if cb["period"] or not ca["period"] or not ca["semantic_tuple_exact"]:
            continue
        pb, pr = b["period"], ref["period"]
        yb = int((pb.get("instant") or pb.get("end_date"))[:4]); yr = int((pr.get("instant") or pr.get("end_date"))[:4])
        if abs(yb - yr) == 1 and pb.get("kind") == pr.get("kind"):
            case_a = (tid, b, a, ref); break
    # (b) sibling concept confusion in v32
    case_b = None
    for tid, ref in refs.items():
        a = after.get(tid)
        if not a:
            continue
        ca = component_correct(a, ref)
        if ca["field"] or not (ca["period"] and ca["dimensions"] and ca["quantity"]):
            continue
        fa, fr = short(a["field"]), short(ref["field"])
        common = 0
        for x, y in zip(fa, fr):
            if x != y:
                break
            common += 1
        if common >= 6 and a["field"].split("}")[0] == ref["field"].split("}")[0]:
            case_b = (tid, a, ref); break
    panels = []
    pa = case_a[1]["period"]; pa_text = pa.get("instant") or pa.get("end_date")
    cases = [("(a)", case_a[0], case_a[1], case_a[3], "before the structural prior: period %s; after: correct" % pa_text),
             ("(b)", case_b[0], case_b[1], case_b[2], "v32 concept: " + short(case_b[1]["field"]) + "   filed: " + short(case_b[2]["field"]))]
    for label, tid, pred, ref, note in cases:
        t, item = by_id[tid]
        page = pages[anchors[tid]["page_hash"]]
        ys = [w["anchor"]["box"][1] for g in item["components"]["period"] if g["value"] in (ref["period"], pred["period"]) for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box") and w["anchor"]["box"][1] < t["target_box"][1]]
        above = min(0.5, t["target_box"][1] - min(ys) + 0.03) if ys else 0.1
        im, geo = crop(page, t["target_box"], above=above, below=0.04)
        d = ImageDraw.Draw(im)
        box(d, t["target_box"], geo, RED, 5)
        if label == "(a)":
            for g in item["components"]["period"]:
                color = GREEN if g["value"] == ref["period"] else (RED if g["value"] == pred["period"] else None)
                if color:
                    for w in g["witnesses"]:
                        a_ = w.get("anchor")
                        if a_ and a_.get("box"):
                            box(d, a_["box"], geo, color, 3, 2)
        if ys:
            L, T, W, H = geo
            hdr_px = int((max(w["anchor"]["box"][3] for g in item["components"]["period"] if g["value"] in (ref["period"], pred["period"]) for w in g["witnesses"] if w.get("anchor") and w["anchor"].get("box") and w["anchor"]["box"][1] < t["target_box"][1]) - (t["target_box"][1] - above)) * H) + 60
            tgt_px = int((t["target_box"][3] + 0.04 - t["target_box"][1]) * H) + int(0.07 * H)
            im = cut_middle(im, hdr_px, tgt_px)
        lines = [note] if label == "(b)" else [note, "green: anchors of the filed period   red: anchors of the period chosen before the prior"]
        panels.append(panel(im, lines, label + "  " + ("neighbouring-year column" if label == "(a)" else "sibling concept")))
    w = max(p.width for p in panels); h = sum(p.height for p in panels) + 20
    out = Image.new("RGB", (w, h), "white"); y = 0
    for p in panels:
        out.paste(p, (0, y)); y += p.height + 20
    out.save(OUT / "fig4_errors.png")
    return {"fig4": {"a": case_a[0], "b": case_b[0]}}


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    data = load()
    choices = {}
    for fn in (fig1, fig3, fig4):
        choices.update(fn(*data)); print(fn.__name__, "done", flush=True)
    (OUT / "choices.json").write_text(json.dumps(choices, indent=1))

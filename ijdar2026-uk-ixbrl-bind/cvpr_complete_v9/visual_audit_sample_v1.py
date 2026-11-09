"""Post-hoc visual ownership audit: stratified sample of 100 development targets with annotated crops.

Scoring-only references are read here (this is an audit of the references
after all predictions are frozen, not candidate generation). Each card shows
the table region around the target box with the box highlighted, and a text
panel with the reference tuple and the v28 semantic_memory prediction, so the
auditor can judge whether the printed number visually belongs to the reference
fact: row label vs concept, column header vs period, unit/scale, dimension
column or section, and printed value vs quantity.
"""

import json
import re
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from native_header_alignment_v21 import DEST as ALIGN
from native_structural_prior_v27 import DEST as V27
from native_v26_bank import DEST as BANK
from prepare import save
from score_native_binding_v17 import scoring_references

DEST = BANK / "posthoc_visual_audit_v1"
SEED, SIZE = 20261017, 100


def statement(blocks):
    text = " ".join(blocks).lower()
    for name, pats in (("equity", ("changes in equity",)), ("cashflow", ("cash flow",)), ("position", ("financial position", "balance sheet")),
                       ("income", ("income statement", "profit or loss", "comprehensive income", "profit and loss"))):
        if any(p in text for p in pats):
            return name
    return "other"


def label_of(task, field):
    for c in task["field_candidates"]:
        if "{" + c["namespace"] + "}" + c["name"] == field:
            return (c.get("labels") or [c["name"]])[0]
    return field.split("}")[-1]


def member_label(task, member):
    for m in task["dimension_member_candidates"]:
        if m["field"] == member:
            return (m.get("labels") or [m["name"]])[0].replace("[member]", "").strip()
    return member.split("}")[-1]


def period_text(p):
    return p["instant"] if p["kind"] == "instant" else p["start_date"] + " .. " + p["end_date"]


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    tasks = {t["task_id"]: t for t in json.loads((BANK / "public/tasks.json").read_text())}
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((ALIGN / "localization/public_anchors.json").read_text())
    contexts = json.loads((V27 / "table_contexts.json").read_text())
    future = {k: t for k, t in tasks.items() if t["source_year"] != 2023}
    refs, _ = scoring_references(future)
    preds = {}
    for c in sorted({t["company"] for t in future.values()}):
        for q in json.loads((BANK.parent / "common_candidate_v28/learning_v1/predictions/hash_prefix" / c / "semantic_memory/19/10.json").read_text())["predictions"]:
            preds[q["task_id"]] = q["predicted_binding"]
    strata = {k: (statement(contexts[k]["blocks"]), bool(refs[k]["dimensions"])) for k in future}
    rng = np.random.default_rng(SEED)
    groups = {}
    for k, s in strata.items():
        groups.setdefault(s, []).append(k)
    # proportional allocation with at least 4 per non-empty stratum, then fill to SIZE
    alloc = {s: max(4, round(SIZE * len(v) / len(future))) for s, v in groups.items()}
    chosen = []
    for s, v in groups.items():
        pick = rng.choice(sorted(v), min(alloc[s], len(v)), replace=False)
        chosen.extend(pick.tolist())
    rest = [k for k in sorted(future) if k not in chosen]
    rng.shuffle(rest)
    chosen = chosen[:SIZE] + rest[:max(0, SIZE - len(chosen))]
    chosen = sorted(chosen[:SIZE])
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22) if Path("/System/Library/Fonts/Helvetica.ttc").exists() else ImageFont.load_default()
    sample = []
    for n, k in enumerate(chosen, 1):
        t, ref = future[k], refs[k]
        page = pages[anchors[k]["page_hash"]]
        img = Image.open(page["image"]).convert("RGB")
        W, H = img.size
        x0, y0, x1, y1 = t["target_box"]
        bx = [int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)]
        top, bottom = max(0, int((y0 - 0.22) * H)), min(H, int((y1 + 0.10) * H))
        crop = img.crop((0, top, W, bottom))
        draw = ImageDraw.Draw(crop)
        draw.rectangle([bx[0] - 6, bx[1] - top - 6, bx[2] + 6, bx[3] - top + 6], outline=(255, 0, 0), width=5)
        scale = 1400 / W
        crop = crop.resize((1400, int(crop.height * scale)))
        pred = preds[k]
        lines = ["#%d  %s  %s  stratum=%s/%s" % (n, k, t["report"], strata[k][0], "dims" if strata[k][1] else "nodims"),
                 "REF field: %s" % label_of(t, ref["field"]), "          (%s)" % ref["field"].split("}")[-1],
                 "REF period: %s   unit: %s   quantity: %s" % (period_text(ref["period"]), "/".join(u.split("}")[-1] for u in ref["unit"]["numerator"]) or "pure", ref["quantity"]),
                 "REF dims: %s" % ("; ".join("%s = %s" % (d["axis"].split("}")[-1].replace("Axis", ""), member_label(t, d["member"])) for d in ref["dimensions"]) or "none"),
                 "row label (OCR): %s" % contexts[k]["row"][:70], "column headers (OCR): %s" % " | ".join(contexts[k]["column"])[:90],
                 "PRED field: %s   period: %s   qty: %s   dims: %s" % (label_of(t, pred["field"]) if pred.get("field") else None, period_text(pred["period"]) if pred.get("period") else None, pred.get("quantity"),
                                                                  "; ".join(member_label(t, d["member"]) for d in pred["dimensions"]) if pred.get("dimensions") else "none")]
        panel = Image.new("RGB", (1400, 30 * len(lines) + 20), (245, 245, 235))
        pd = ImageDraw.Draw(panel)
        for i, line in enumerate(lines):
            pd.text((12, 10 + 30 * i), line, fill=(0, 0, 0) if not line.startswith("PRED") else (90, 90, 90), font=font)
        card = Image.new("RGB", (1400, crop.height + panel.height), (255, 255, 255))
        card.paste(crop, (0, 0)); card.paste(panel, (0, crop.height))
        card.save(DEST / "cards" / ("%03d_%s.png" % (n, k))) if (DEST / "cards").mkdir(exist_ok=True) or True else None
        sample.append({"index": n, "task_id": k, "report": t["report"], "stratum": {"statement": strata[k][0], "dimensioned": strata[k][1]},
                       "reference": ref, "reference_field_label": label_of(t, ref["field"]), "prediction": pred, "row_label_ocr": contexts[k]["row"], "column_headers_ocr": contexts[k]["column"],
                       "card": str(DEST / "cards" / ("%03d_%s.png" % (n, k)))})
    save(DEST / "sample.json", {"seed": SEED, "size": len(sample), "strata_population": {"%s/%s" % s: len(v) for s, v in groups.items()},
                                "strata_sample": dict(Counter("%s/%s" % (x["stratum"]["statement"], x["stratum"]["dimensioned"]) for x in sample)), "items": sample})
    print("cards", len(sample), DEST)


if __name__ == "__main__":
    main()

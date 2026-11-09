"""v30 common candidate bank: the v26 bank rebuilt on the v30 perception patch with four candidate repairs.

1. Pages wider than the original viewport are re-rendered (perception_v30); header
   hypotheses on those pages are disabled because their word indices are stale.
2. Period candidates always include the filing's own period end for the report
   year and the two preceding years (instants and annual durations), from the
   public filing metadata, independent of what the page prints.
3. OCR variants of unit tokens are normalised before the unit bank runs
   ("ém" -> "£m", curly apostrophes, "Uss" -> "US$").
4. A dash-like glyph inside the target box yields an explicit zero quantity
   candidate when none exists.
5. The field retrieval query is augmented with the column header phrases and
   the statement title block read from the page (v27 table context).

Everything stays common to all learners. No reference, teacher or reserved file is read.
"""

import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import native_v26_bank as base
from native_structural_prior_v27 import table_context
from prepare import opaque

PERCEPTION = base.ALIGN.parent / "perception_v30"
DEST = base.ALIGN.parent / "common_candidate_v30"
SOURCES = Path(__file__).resolve().parents[1] / "ext/xbrl_scope_additional_development"
DASH = {"-", "–", "—", "―", "=", "_", "~", "‒", "−", "nil", "n/a"}

base.ALIGN = PERCEPTION
base.DEST = DEST
_query_text = base.query_text
_expand_task = base.expand_task
_PageText = base.PageText
CONTEXTS = {}
PAGE_WORDS = {}
REPORT_ENDS = {}


def normalise_unit_text(text):
    t = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    t = re.sub(r"(?<![A-Za-z])ém\b", "£m", t)
    t = re.sub(r"(?<![A-Za-z])é(?=\d|'000|000)", "£", t)
    t = re.sub(r"\bUss\b", "US$", t)
    t = re.sub(r"\bUs\$", "US$", t)
    return t


class PageText(_PageText):
    def __init__(self, words):
        super().__init__([{**w, "text": normalise_unit_text(w["text"])} for w in words])


def query_text(task, observation):
    """Original query strings plus augmented variants (column headers, title block); both get encoded."""
    q = _query_text(task, observation)
    ctx = CONTEXTS.get(task["task_id"])
    q["visual_row_aug"], q["section_aug"] = q["visual_row"], q["section"]
    if ctx:
        columns = " | ".join(c for c in ctx["column"] if c)
        blocks = " | ".join(b for b in ctx["blocks"] if b)
        if columns:
            q["visual_row_aug"] = q["visual_row"] + " | " + columns
        if blocks:
            q["section_aug"] = (q["section"] if q["section"] != "unknown" else "") + " | " + blocks
    return q


NUMERIC_GLYPH = re.compile(r"^[\(\-–—−]?[£$€]?(\d[\d,]*)(\.\d+)?[\)%]?$")


def glyph_quantities(glyphs, existing):
    out = []
    for g in glyphs:
        m = NUMERIC_GLYPH.match(g.replace(" ", ""))
        if not m:
            continue
        integer, frac = m.group(1), m.group(2) or ""
        variants = [integer.replace(",", "") + frac]
        if "," in integer and not frac:
            variants.append(integer.replace(",", "."))
        for v in variants:
            try:
                Decimal(v)
            except InvalidOperation:
                continue
            if v in existing:
                continue
            existing.add(v)
            out.append({"candidate_id": opaque(["v30-box-glyph", g, v]), "printed_value": v, "source_glyph_hypotheses": [g],
                        "semantic_sign_candidates": [1, -1], "canonical_quantity_not_supplied": True, "added_by": "v30_box_glyph"})
    return out


def filing_period_end(report):
    if report not in REPORT_ENDS:
        lei, year = report.rsplit("_", 1)
        matches = sorted(p for p in SOURCES.glob(lei + "_" + year + "*") if (p / "original.xhtml").exists())
        if len(matches) != 1:
            raise ValueError("Filing lookup failed for " + report)
        REPORT_ENDS[report] = date.fromisoformat(matches[0].name.rsplit("_", 1)[1])
    return REPORT_ENDS[report]


def metadata_periods(report):
    end = filing_period_end(report)
    out = []
    for back in (0, 1, 2):
        try:
            e = end.replace(year=end.year - back)
        except ValueError:
            e = end.replace(year=end.year - back, day=28)
        start = base.calendar_start(e)
        out.append(({"kind": "instant", "instant": e.isoformat()}, "filing_period_end_minus_%d_years" % back))
        if start:
            out.append(({"kind": "duration", "start_date": start.isoformat(), "end_date": e.isoformat()}, "filing_annual_period_minus_%d_years" % back))
    return out


def expand_task(task, public_task, page, report_month_days, cards, vectors, qvec):
    new = _expand_task(task, public_task, page, report_month_days, cards, vectors, qvec)
    # field retrieval: merge the original ranking with the augmented-query ranking (score = max), keep top FIELD_K
    q = query_text(public_task, public_task.get("common_visual_observation") or {})
    matrix = base.np.stack([vectors[base.field_text(c)] for c in cards])
    failed = bool(public_task.get("common_observation_failed"))
    def scores(vr, sec):
        visual, native, section = matrix @ qvec[vr], matrix @ qvec[q["native_row"]], matrix @ qvec[sec]
        return ((.8 * native + .2 * section) if failed else (.65 * visual + .25 * native + .1 * section)), visual, native, section
    s0, v0, n0, c0 = scores(q["visual_row"], q["section"])
    s1, v1, n1, c1 = scores(q["visual_row_aug"], q["section_aug"])
    merged = base.np.maximum(s0, s1)
    use_aug = s1 > s0
    order = base.np.argsort(-merged, kind="stable")[:base.FIELD_K]
    new["field_candidates"] = [{**cards[int(i)], "retrieval_scores": [float(v1[i] if use_aug[i] else v0[i]), float(n0[i]), float(c1[i] if use_aug[i] else c0[i])],
                                "numeric_nonabstract_pool": True, "top_k": base.FIELD_K, "augmented_query_used": bool(use_aug[i])} for i in order]
    periods = new["period_candidates"]
    existing = {json.dumps({k: v for k, v in p["period"].items() if k != "complete"}, sort_keys=True) for p in periods if p["period"].get("complete")}
    for period, basis in metadata_periods(task["report"]):
        key = json.dumps(period, sort_keys=True)
        if key in existing:
            continue
        existing.add(key)
        periods.append({"candidate_id": opaque(["v30-filing-period", key]), "period": {**period, "complete": True}, "displayed_year": None,
                        "header": None, "uncertain": True, "header_ownership_certified": False, "source_year_used": True,
                        "calendar_convention_assumed": True, "basis": basis, "ocr_anchors": [],
                        "supporting_visible_evidence": [], "from_public_filing_metadata": True})
    # dash glyph -> explicit zero quantity candidate
    words = PAGE_WORDS.get(task["task_id"], [])
    x0, y0, x1, y1 = task["target_box"]
    inside = [w["text"].strip() for w in words if w["box"][0] < x1 and w["box"][2] > x0 and w["box"][1] < y1 and w["box"][3] > y0]
    dashes = [t for t in inside if t.lower() in DASH or (t and all(ch in "-–—―=_~‒−." for ch in t))]
    quantities = list(new["quantity_candidates"])
    quantities += glyph_quantities(inside, {qc["printed_value"] for qc in quantities})
    new = {**new, "quantity_candidates": quantities}
    if dashes and not any(q["printed_value"] in ("0", "0.0") for q in quantities):
        quantities = quantities + [{"candidate_id": opaque(["v30-dash-zero", task["task_id"]]), "printed_value": "0", "source_glyph_hypotheses": [dashes[0]],
                                    "semantic_sign_candidates": [1], "canonical_quantity_not_supplied": True,
                                    "semantic_zero_hypothesis_not_literal_zero": True, "added_by": "v30_dash_glyph"}]
        new = {**new, "quantity_candidates": quantities}
    return new


def main():
    manifest = json.loads((PERCEPTION / "localization/manifest.json").read_text())
    if not manifest["complete"]:
        raise ValueError("perception_v30 must be frozen first")
    pages = json.loads((PERCEPTION / "localization/public_pages.json").read_text())
    anchors = json.loads((PERCEPTION / "localization/public_anchors.json").read_text())
    tasks = json.loads((PERCEPTION / "candidates/tasks.json").read_text())
    for t in tasks:
        words = pages[anchors[t["task_id"]]["page_hash"]]["words"]
        PAGE_WORDS[t["task_id"]] = words
        CONTEXTS[t["task_id"]] = table_context(words, t["target_box"])
    base.PageText = PageText
    base.query_text = query_text
    base.expand_task = expand_task
    base.main()


if __name__ == "__main__":
    main()

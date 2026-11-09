"""Held-out companies, step C: sample targets and freeze their scoring-only references.

A target is one numeric iXBRL cell (ix:nonFraction) on a rendered statement page whose DOM
box contains exactly one OCR numeral consistent with the printed text, and whose fact is a
plain numeric fact (no per-share-of-text oddities). Up to 40 targets per 2024/2025 filing are
the held-out test; up to 20 per 2023 filing become that company's historical teachers. Zero
valued cells are capped at 10%. References come from the publisher's own OIM facts file and
are written to scoring_only/ (never read by candidate generation); the public task record
carries only the page, box and metadata every method may see.
"""

import os
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np
from prepare import opaque, save, sha

EXP = Path(__file__).resolve().parents[1]
SOURCES = Path(os.environ.get("HELDOUT_SOURCES", str(EXP / "ext/xbrl_scope_additional_development")))
DEST = Path(os.environ.get("HELDOUT_ROOT", str(EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1")))
SEED = 20261020
PER_DEV, PER_TEACHER, ZERO_CAP = 40, 20, 0.10
NUM = re.compile(r"^[\(\-–—]?[£$€]?[\d,]+(\.\d+)?[\)%]?$|^[-–—=]$")
ENTITY_SCHEME = "http://standards.iso.org/iso/17442"


def namespaces(xhtml):
    return dict(re.findall(r'xmlns:([A-Za-z0-9_\-]+)="([^"]+)"', xhtml[:200000]))


def expand(qname, ns):
    prefix, _, local = qname.partition(":")
    if prefix not in ns:
        raise ValueError("Unknown prefix " + prefix)
    return "{" + ns[prefix] + "}" + local


def period_of(text):
    """OIM periods use exclusive end datetimes (midnight of the following day); the iXBRL
    period is the calendar day before, matching the development teachers' convention."""
    if "/" in text:
        start, end = text.split("/")
        s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
        return {"kind": "duration", "start_date": s.date().isoformat(), "end_date": (e - timedelta(seconds=1)).date().isoformat()}
    e = datetime.fromisoformat(text)
    return {"kind": "instant", "instant": (e - timedelta(seconds=1)).date().isoformat()}


def reference_of(fact, ns, lei):
    d = fact["dimensions"]
    unit = d.get("unit")
    numerator, denominator = [], []
    if unit:
        num, _, den = unit.partition("/")
        numerator = [expand(u, ns) for u in num.split("*")]
        denominator = [expand(u, ns) for u in den.split("*")] if den else []
    dims = [{"axis": expand(k, ns), "kind": "explicit", "member": expand(v, ns)} for k, v in d.items()
            if k not in ("concept", "entity", "period", "unit", "language") and ":" in k and isinstance(v, str)]
    return {"field": expand(d["concept"], ns), "entity": {"scheme": ENTITY_SCHEME, "identifier": lei},
            "period": period_of(d["period"]), "dimensions": sorted(dims, key=lambda x: x["axis"]),
            "unit": {"numerator": numerator, "denominator": denominator}, "quantity": str(Decimal(str(fact["value"])))}


# ---- facts without an id attribute (older filings): match the DOM fact to the facts file by content ----
CTX_RE = re.compile(r"<(?:\w+:)?context\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</(?:\w+:)?context>", re.S)
UNIT_RE = re.compile(r"<(?:\w+:)?unit\b[^>]*\bid=\"([^\"]+)\"[^>]*>(.*?)</(?:\w+:)?unit>", re.S)
MEMBER_RE = re.compile(r"<(?:\w+:)?explicitMember\b[^>]*\bdimension=\"([^\"]+)\"[^>]*>\s*([^<\s]+)\s*<", re.S)
TYPED_RE = re.compile(r"<(?:\w+:)?typedMember\b")
MEASURE_RE = re.compile(r"<(?:\w+:)?measure>\s*([^<\s]+)\s*</(?:\w+:)?measure>")
TAG_RE = re.compile(r"<(?:\w+:)?(instant|startDate|endDate)>\s*([0-9\-]+)\s*<")


def expand_safe(qname, ns):
    try:
        return expand(qname, ns)
    except ValueError:
        return None


def oim_key(fact, ns):
    d = fact["dimensions"]
    concept = expand_safe(d["concept"], ns)
    dims = []
    for k, v in d.items():
        if k in ("concept", "entity", "period", "unit", "language") or ":" not in k or not isinstance(v, str):
            continue
        dims.append((expand_safe(k, ns), expand_safe(v, ns)))
    num, _, den = d.get("unit", "").partition("/")
    unit = (tuple(sorted(expand_safe(u, ns) for u in num.split("*") if u)), tuple(sorted(expand_safe(u, ns) for u in den.split("*") if u)))
    return (concept, d.get("period"), tuple(sorted(dims)), unit)


def content_index(xhtml, facts, oim_ns):
    """contexts/units of the iXBRL document and the facts file grouped by (concept, OIM period, dims, unit)."""
    ns = namespaces(xhtml)
    contexts = {}
    for cid, body in CTX_RE.findall(xhtml):
        if TYPED_RE.search(body):
            continue
        parts = dict((k, v) for k, v in TAG_RE.findall(body))
        if "instant" in parts:
            period = (datetime.fromisoformat(parts["instant"]) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        elif "startDate" in parts and "endDate" in parts:
            period = parts["startDate"] + "T00:00:00/" + (datetime.fromisoformat(parts["endDate"]) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
        else:
            continue
        dims = tuple(sorted((expand_safe(a, ns), expand_safe(m, ns)) for a, m in MEMBER_RE.findall(body)))
        contexts[cid] = (period, dims)
    units = {}
    for uid, body in UNIT_RE.findall(xhtml):
        num, _, den = body.partition("unitDenominator")
        if "unitNumerator" in body:
            units[uid] = (tuple(sorted(expand_safe(m, ns) for m in MEASURE_RE.findall(num))), tuple(sorted(expand_safe(m, ns) for m in MEASURE_RE.findall(den))))
        else:
            units[uid] = (tuple(sorted(expand_safe(m, ns) for m in MEASURE_RE.findall(body))), ())
    groups = defaultdict(list)
    for fid, fact in facts.items():
        if "unit" in fact["dimensions"] and isinstance(fact.get("value"), (str, int, float)):
            groups[oim_key(fact, oim_ns)].append(fid)
    return {"ns": ns, "contexts": contexts, "units": units, "groups": groups}


def printed_decimal(text, fmt, scale):
    t = (text or "").strip()
    if t in ("-", "–", "—", "=") or "zero" in (fmt or "").lower():
        return Decimal(0)
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").replace(" ", "")
    if "comma-decimal" in (fmt or ""):
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    t = re.sub(r"[^\d.]", "", t)
    if not t:
        return None
    try:
        v = Decimal(t) * (Decimal(10) ** int(scale or 0))
    except Exception:
        return None
    return -v if neg else v


def content_match(f, index, facts):
    ctx = index["contexts"].get(f.get("contextRef")); unit = index["units"].get(f.get("unitRef"))
    if ctx is None or unit is None or not f.get("name"):
        return None
    key = (expand_safe(f["name"], index["ns"]), ctx[0], ctx[1], unit)
    ids = index["groups"].get(key, [])
    if not ids:
        return None
    printed = printed_decimal(f.get("text"), f.get("format"), f.get("scale"))
    if printed is not None:
        ids = [i for i in ids if abs(Decimal(str(facts[i]["value"]))) == abs(printed)]
    return ids[0] if len(ids) == 1 else None


def box_numeral(words, box):
    x0, y0, x1, y1 = box
    inside = [w["text"] for w in words if w["box"][0] < x1 and w["box"][2] > x0 and w["box"][1] < y1 and w["box"][3] > y0]
    nums = [t for t in inside if NUM.match(t.replace(" ", ""))]
    return nums


def main():
    if (DEST / "public/tasks.json").exists():
        raise RuntimeError("heldout targets already frozen")
    renders = json.loads((DEST / "renders/manifest.json").read_text())
    pages = json.loads((DEST / "localization/public_pages.json").read_text())
    keys = json.loads((DEST / "localization/page_keys.json").read_text())
    rng = np.random.default_rng(SEED)
    public, references, stats = [], {}, defaultdict(dict)
    for report, info in sorted(renders["reports"].items()):
        lei, year = report.rsplit("_", 1)
        folder = Path(info["source"]).parent
        xhtml = (folder / "original.xhtml").read_text(errors="ignore")
        ns = namespaces(xhtml)
        facts_doc = json.loads((folder / "published_facts_scoring_only.json").read_text()); facts = facts_doc["facts"]
        index = content_index(xhtml, facts, facts_doc.get("documentInfo", {}).get("namespaces", {}))
        candidates = []; matched_by_content = 0
        for p in json.loads(Path(info["pages_file"]).read_text()):
            key = keys[report + "/" + p["page_id"]]
            words = pages[key]["words"]
            for f in p["facts"]:
                fid = f.get("id") if f.get("id") in facts else content_match(f, index, facts)
                if fid is not None and not f.get("id"):
                    matched_by_content += 1
                f = dict(f, id=fid)
                fact = facts.get(fid) if fid else None
                if not fact or "unit" not in fact["dimensions"] or not isinstance(fact.get("value"), (str, int, float)):
                    continue
                try:
                    ref = reference_of(fact, ns, lei)
                except (ValueError, KeyError):
                    continue
                nums = box_numeral(words, f["box"])
                if len(nums) != 1:
                    continue
                printed = re.sub(r"[^\d.]", "", nums[0].replace(",", ""))
                value = abs(Decimal(ref["quantity"]))
                zero = value == 0
                if not zero:
                    try:
                        pv = Decimal(printed) if printed else None
                    except Exception:
                        pv = None
                    if pv is None or not any(pv * Decimal(m) == value for m in ("1", "1000", "1000000", "1000000000", "0.01")):
                        continue
                elif nums[0].replace(" ", "") not in ("-", "–", "—", "=") and printed not in ("0", "0.0"):
                    continue
                candidates.append({"page_key": key, "page_id": p["page_id"], "fact_id": f["id"], "box": f["box"], "ref": ref, "zero": zero, "glyph": nums[0]})
        stats[report]["eligible"] = len(candidates); stats[report]["facts_matched_by_content"] = matched_by_content
        quota = PER_DEV if year != "2023" else PER_TEACHER
        zeros = [c for c in candidates if c["zero"]]; nonzeros = [c for c in candidates if not c["zero"]]
        rng.shuffle(zeros); rng.shuffle(nonzeros)
        take_zero = min(len(zeros), int(round(quota * ZERO_CAP)))
        chosen = nonzeros[:quota - take_zero] + zeros[:take_zero]
        if len(chosen) < quota:
            chosen = (nonzeros + zeros)[:quota]
        stats[report]["sampled"] = len(chosen); stats[report]["zero_sampled"] = sum(c["zero"] for c in chosen)
        for c in chosen:
            tid = opaque([DEST.name.replace("_", "-"), report, c["fact_id"]])
            public.append({"task_id": tid, "company": lei, "source_year": int(year), "report": report, "page_hash": c["page_key"], "page_id": c["page_id"],
                           "target_box": c["box"], "printed_glyph_ocr": c["glyph"], "fact_dom_id_scoring_only": c["fact_id"], "heldout": year != "2023"})
            references[tid] = c["ref"]
    (DEST / "public").mkdir(exist_ok=True); (DEST / "scoring_only").mkdir(exist_ok=True)
    save(DEST / "public/tasks.json", public)
    save(DEST / "scoring_only/references.json", references)
    save(DEST / "public/manifest.json", {"targets": len(public), "heldout_targets": sum(t["heldout"] for t in public), "teacher_targets": sum(not t["heldout"] for t in public),
                                         "per_report": dict(stats), "seed": SEED, "zero_cap": ZERO_CAP, "public_tasks_sha256": sha(DEST / "public/tasks.json"),
                                         "references_sha256": sha(DEST / "scoring_only/references.json"), "builder_sha256": sha(Path(__file__)),
                                         "selection_rule": "DOM box contains exactly one OCR numeral consistent with the fact value under scale normalisation; zeros capped"})
    print(json.dumps({"targets": len(public), "per_report": dict(stats)}, indent=1))


if __name__ == "__main__":
    main()

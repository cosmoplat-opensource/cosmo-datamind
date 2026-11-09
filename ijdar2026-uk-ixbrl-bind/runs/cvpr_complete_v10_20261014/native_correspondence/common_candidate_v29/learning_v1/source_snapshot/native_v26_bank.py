"""v26 common candidate bank, witness inventory and features.

Repairs the shared candidate pool for every control (reference-blind):
  * fields: report-local numeric, non-abstract cards ranked by the frozen
    MiniLM retrieval, top FIELD_K instead of 64;
  * periods: v17/v18 proposals plus whole-page OCR hypotheses (explicit dates,
    explicit ranges, calendar-annual intervals, printed year x visible
    month/day from the report's public pages);
  * units: v14/v18 proposals plus whole-page OCR currency/scale/pence/shares
    hypotheses.
Quantities, dimensions, entity, teacher masks, context vectors, header pool,
encoders and projections are unchanged from v21/v23. No reference, teacher or
reserved file is read here. Output root: <native_correspondence>/common_candidate_v26.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from native_binding_features_v17 import CHANNELS, candidate_records, extras
from native_field_features_v12 import properties
from native_field_retrieval_v12 import DEST as FIELDS
from native_field_retrieval_v12 import SCHEMA, field_text, query_text, report_card
from native_header_alignment_v21 import DEST as ALIGN
from native_visible_periods_v17 import calendar_start, date_mentions
from native_witness_features_v23 import ANCHOR_DIM, NODE_DIM, anchor_vector, witness_node
from native_witness_inventory_v23 import semantic_key
from pilot_features import encode_texts
from prepare import opaque, save, sha

DEST = ALIGN.parent / "common_candidate_v26"
FIELD_K = 512
ISO, XI = "{http://www.xbrl.org/2003/iso4217}", "{http://www.xbrl.org/2003/instance}"
NUMERIC_TYPES = ("monetaryItemType", "sharesItemType", "perShareItemType", "decimalItemType", "percentItemType",
                 "pureItemType", "integerItemType", "nonNegativeIntegerItemType", "areaItemType", "volumeItemType",
                 "energyItemType", "massItemType", "lengthItemType", "durationItemType", "floatItemType", "doubleItemType")
YEAR_RE = re.compile(r"(?<!\d)(?:19|20|21)\d{2}(?!\d)")
UNIT_RE = re.compile(
    r"(?P<cur>US\$|A\$|C\$|NZ\$|HK\$|S\$|\$|£|€|GBP|EUR|USD|AUD|CAD|NZD|CHF|JPY|CNY|ZAR|HKD|SGD)\s*"
    r"(?P<scale>millions?|thousands?|billions?|000(?:[’']s)?|'000|mn|bn|m|k)?(?![A-Za-z])", re.I)
CODES = {"us$": "USD", "a$": "AUD", "c$": "CAD", "nz$": "NZD", "hk$": "HKD", "s$": "SGD", "$": "USD", "£": "GBP", "€": "EUR"}
SCALE_WORD_RE = re.compile(r"(?<![A-Za-z])(?:in\s+)?(?P<scale>millions?|thousands?|billions?|'000|000s|£m|\$m|€m)(?![A-Za-z])", re.I)
MINOR_RE = re.compile(r"(?<![A-Za-z])(?:pence|p|cents?)(?![A-Za-z])")
SHARES_RE = re.compile(r"(?<![A-Za-z])shares?(?![A-Za-z])|number\s+of", re.I)
RATE_RE = re.compile(r"%|per\s*cent|percent|ratio|times|basis\s+points", re.I)


def numeric_card(card):
    kind = card.get("type") or ""
    return card.get("abstract") is False and any(kind.endswith("}" + t) for t in NUMERIC_TYPES)


def text_lines(words, tolerance=.006):
    rows = []
    for i, w in sorted(enumerate(words), key=lambda iw: ((iw[1]["box"][1] + iw[1]["box"][3]) / 2, iw[1]["box"][0])):
        center = (w["box"][1] + w["box"][3]) / 2
        if rows and abs(rows[-1][0] - center) <= tolerance:
            rows[-1][1].append(i)
        else:
            rows.append([center, [i]])
    return [sorted(ids, key=lambda i: words[i]["box"][0]) for _, ids in rows]


class PageText:
    """Joined OCR lines (and adjacent line pairs) with char-offset → word-index maps."""

    def __init__(self, words):
        self.words = words
        lines = text_lines(words)
        self.items = []
        for ids in lines:
            self.items.append(self._join(ids))
        for a, b in zip(lines, lines[1:]):
            self.items.append(self._join(a + b))

    def _join(self, ids):
        text, spans = "", []
        for i in ids:
            if text:
                text += " "
            spans.append((len(text), len(text) + len(self.words[i]["text"]), i))
            text += self.words[i]["text"]
        return text, spans

    @staticmethod
    def span_words(spans, start, end):
        return [i for s, e, i in spans if s < end and e > start]

    def anchor(self, spans, start, end, basis):
        ids = self.span_words(spans, start, end)
        if not ids:
            return None
        boxes = np.array([self.words[i]["box"] for i in ids])
        box = [float(boxes[:, 0].min()), float(boxes[:, 1].min()), float(boxes[:, 2].max()), float(boxes[:, 3].max())]
        text = " ".join(self.words[i]["text"] for i in ids)
        return {"word_indices": sorted(ids), "box": box, "anchor_text": text, "basis": basis,
                "anchor_id": opaque(["v26-ocr-anchor", sorted(ids), basis]), "full_header_text_aligned": False}


def page_period_bank(page, report_month_days):
    periods = {}

    def add(period, anchor, basis, assumption):
        key = json.dumps(period, sort_keys=True)
        item = periods.setdefault(key, {"period": period, "anchors": {}, "calendar_convention_assumed": True})
        item["calendar_convention_assumed"] &= assumption
        if anchor:
            item["anchors"].setdefault(anchor["anchor_id"], {**anchor, "basis": basis})

    month_days, year_anchors = set(report_month_days), {}
    for text, spans in page.items:
        for m in YEAR_RE.finditer(text):
            year_anchors.setdefault(int(m.group(0)), page.anchor(spans, m.start(), m.end(), "printed_year"))
        mentions = date_mentions(text)
        for m in mentions:
            end = m["date"]
            if not 1990 <= end.year <= 2100:
                continue
            month_days.add((end.month, end.day))
            a = page.anchor(spans, m["start"], m["end"], "explicit_visible_calendar_date")
            add({"kind": "instant", "instant": end.isoformat()}, a, "explicit_visible_calendar_date", False)
            add({"kind": "instant", "instant": (end - timedelta(days=1)).isoformat()}, a, "visible_date_minus_one_day", True)
            start = calendar_start(end)
            if start:
                add({"kind": "duration", "start_date": start.isoformat(), "end_date": end.isoformat()}, a, "calendar_annual_interval_at_visible_date", True)
        for first, second in zip(mentions, mentions[1:]):
            between = text[first["end"]:second["start"]]
            if re.fullmatch(r"\s*(?:to|through|until|[-–—])\s*", between, re.I) and first["date"] <= second["date"]:
                a = page.anchor(spans, first["start"], second["end"], "explicit_visible_start_to_end")
                add({"kind": "duration", "start_date": first["date"].isoformat(), "end_date": second["date"].isoformat()}, a, "explicit_visible_start_to_end", False)
    for year, anchor in year_anchors.items():
        if not 1990 <= year <= 2100:
            continue
        for month, day in sorted(month_days):
            try:
                end = date(year, month, day)
            except ValueError:
                continue
            add({"kind": "instant", "instant": end.isoformat()}, anchor, "printed_year_x_visible_month_day", True)
            add({"kind": "instant", "instant": (end - timedelta(days=1)).isoformat()}, anchor, "printed_year_x_visible_month_day_minus_one", True)
            start = calendar_start(end)
            if start:
                add({"kind": "duration", "start_date": start.isoformat(), "end_date": end.isoformat()}, anchor, "annual_interval_printed_year_x_visible_month_day", True)
    return periods


def page_unit_bank(page):
    bank, currencies, powers, scale_anchor, minor_anchor, shares_anchor, rate_anchor = {}, {}, {0}, None, None, None, None

    def add(unit, power, kind, anchor, basis):
        key = json.dumps([unit, power], sort_keys=True)
        item = bank.setdefault(key, {"unit": unit, "power": power, "kind": kind, "anchors": {}})
        if anchor:
            item["anchors"].setdefault(anchor["anchor_id"], {**anchor, "basis": basis})

    for text, spans in page.items:
        for m in UNIT_RE.finditer(text):
            cur = CODES.get(m["cur"].lower(), m["cur"].upper())
            a = page.anchor(spans, m.start(), m.end(), "visible_currency_token")
            currencies.setdefault(cur, a)
            add({"numerator": [ISO + cur], "denominator": []}, scale_power(m["scale"]), "monetary", a, "visible_currency_and_scale")
            if re.search(r"per\s+share|/\s*share", text[m.end():m.end() + 40], re.I):
                add({"numerator": [ISO + cur], "denominator": [XI + "shares"]}, 0, "currency_per_share", a, "visible_currency_per_share")
        for m in SCALE_WORD_RE.finditer(text):
            powers.add(scale_power(m["scale"]))
            scale_anchor = scale_anchor or page.anchor(spans, m.start(), m.end(), "visible_scale_word")
        if minor_anchor is None:
            m = MINOR_RE.search(text)
            if m:
                minor_anchor = page.anchor(spans, m.start(), m.end(), "visible_minor_currency_unit")
        if shares_anchor is None:
            m = SHARES_RE.search(text)
            if m:
                shares_anchor = page.anchor(spans, m.start(), m.end(), "visible_shares_word")
        if rate_anchor is None:
            m = RATE_RE.search(text)
            if m:
                rate_anchor = page.anchor(spans, m.start(), m.end(), "visible_rate_word")
    for cur, a in currencies.items():
        for p in powers:
            add({"numerator": [ISO + cur], "denominator": []}, p, "monetary", scale_anchor or a, "visible_currency_x_page_scale")
        if minor_anchor:
            add({"numerator": [ISO + cur], "denominator": [XI + "shares"]}, -2, "currency_per_share", minor_anchor, "visible_minor_unit_per_share")
            add({"numerator": [ISO + cur], "denominator": []}, -2, "monetary", minor_anchor, "visible_minor_unit")
        add({"numerator": [ISO + cur], "denominator": [XI + "shares"]}, 0, "currency_per_share", a, "visible_currency_per_share_hypothesis")
    if shares_anchor:
        for p in powers:
            add({"numerator": [XI + "shares"], "denominator": []}, p, "shares", shares_anchor, "visible_shares_word")
    if rate_anchor:
        add({"numerator": [XI + "pure"], "denominator": []}, 0, "rate", rate_anchor, "visible_rate_word")
        add({"numerator": [XI + "pure"], "denominator": []}, -2, "rate", rate_anchor, "visible_percent_as_fraction")
    return bank


def scale_power(scale):
    s = (scale or "").lower()
    if s in {"billion", "billions", "bn"}:
        return 9
    if s in {"million", "millions", "mn", "m", "£m", "$m", "€m"}:
        return 6
    if s.startswith(("thousand", "000", "'000")) or s == "k":
        return 3
    return 0


def multiplier_text(power):
    return str(10 ** power) if power >= 0 else "0." + "0" * (-power - 1) + "1"


def expand_task(task, public_task, page, report_month_days, cards, vectors, qvec):
    """New field/period/unit candidate lists for one task (public inputs only)."""
    q = query_text(public_task, public_task.get("common_visual_observation") or {})
    matrix = np.stack([vectors[field_text(c)] for c in cards])
    visual, native, section = matrix @ qvec[q["visual_row"]], matrix @ qvec[q["native_row"]], matrix @ qvec[q["section"]]
    failed = bool(public_task.get("common_observation_failed"))
    scores = (.8 * native + .2 * section) if failed else (.65 * visual + .25 * native + .1 * section)
    order = np.argsort(-scores, kind="stable")[:FIELD_K]
    fields = [{**cards[int(i)], "retrieval_scores": [float(visual[i]), float(native[i]), float(section[i])],
               "numeric_nonabstract_pool": True, "top_k": FIELD_K} for i in order]

    periods = list(task["period_candidates"])
    existing = {json.dumps({k: v for k, v in p["period"].items() if k != "complete"}, sort_keys=True) for p in periods if p["period"].get("complete")}
    for key, item in page_period_bank(page, report_month_days).items():
        if key in existing:
            # Same semantic value already proposed: add OCR occurrences as extra evidence paths.
            for p in periods:
                if p["period"].get("complete") and json.dumps({k: v for k, v in p["period"].items() if k != "complete"}, sort_keys=True) == key:
                    p.setdefault("ocr_anchors", []).extend(a for a in item["anchors"].values() if a not in p.get("ocr_anchors", []))
            continue
        periods.append({"candidate_id": opaque(["v26-page-period", key]), "period": {**item["period"], "complete": True},
                        "displayed_year": None, "header": None, "uncertain": True, "header_ownership_certified": False,
                        "source_year_used": False, "calendar_convention_assumed": item["calendar_convention_assumed"],
                        "basis": "whole_page_ocr_hypothesis", "ocr_anchors": list(item["anchors"].values()),
                        "supporting_visible_evidence": [{"basis": a["basis"], "text": a["anchor_text"], "box": a["box"], "evidence_kind": "page_ocr"}
                                                        for a in list(item["anchors"].values())[:2]]})
    units = list(task["unit_candidates"])
    existing_units = {(json.dumps(u["unit"], sort_keys=True), str(u["multiplier"])) for u in units if u.get("unit") and u.get("multiplier") is not None}
    for item in page_unit_bank(page).values():
        mult = multiplier_text(item["power"])
        key = (json.dumps(item["unit"], sort_keys=True), mult)
        if key in existing_units:
            for u in units:
                if u.get("unit") and (json.dumps(u["unit"], sort_keys=True), str(u["multiplier"])) == key:
                    u.setdefault("ocr_anchors", []).extend(a for a in item["anchors"].values() if a not in u.get("ocr_anchors", []))
            continue
        existing_units.add(key)
        anchors = list(item["anchors"].values())
        units.append({"candidate_id": opaque(["v26-page-unit", key]), "unit": item["unit"], "multiplier": mult, "kind": item["kind"],
                      "header": None, "uncertain": True, "header_ownership_certified": False,
                      "basis": "whole_page_ocr_unit_hypothesis", "ocr_anchors": anchors,
                      "supporting_visible_text": anchors[0]["anchor_text"] if anchors else None,
                      "no_currency_inferred_from_administrative_company_or_source_year": True})
    new = {k: v for k, v in task.items()}
    new.update(field_candidates=fields, period_candidates=periods, unit_candidates=units)
    return new


def build_groups(task, records, public_headers, channel):
    """Semantic values with located witnesses (header anchors or OCR anchors) and an unresolved alternative."""
    values, indices = {}, defaultdict(list)
    for i, row in enumerate(records):
        key = semantic_key(channel, row["value"])
        values.setdefault(key, row["value"])
        indices[key].append(i)
    null_key = semantic_key(channel, None)
    values.setdefault(null_key, None)
    indices[null_key].append(len(records))
    groups = []
    for number, (key, value) in enumerate(values.items()):
        witnesses, seen = [], set()
        for i in indices[key]:
            if i >= len(records):
                continue
            meta = records[i]["metadata"]
            header = meta.get("header") or {}
            hid = header.get("id")
            header_anchors, status = [], "no_proposed_header"
            if hid is not None and hid in public_headers and public_headers[hid].get("may_supply_transfer_hypotheses"):
                header_anchors, status = public_headers[hid].get("anchors", []), public_headers[hid].get("status", "no_proposed_header")
            paths = [(a, hid, status) for a in header_anchors] + [(a, None, "page_ocr_occurrence") for a in meta.get("ocr_anchors", [])]
            extra = {}
            if channel == "unit_quantity":
                extra = {"printed": meta.get("printed"), "multiplier": meta.get("multiplier"), "semantic_sign": meta.get("semantic_sign")}
            for anchor, anchor_hid, anchor_status in paths or [(None, None, status)]:
                sig = json.dumps([(anchor or {}).get("anchor_id"), extra], sort_keys=True)
                if sig in seen:
                    continue
                seen.add(sig)
                witnesses.append({"record_indices": [i], "header_id": anchor_hid, "basis": meta.get("basis"), **extra,
                                  "witness_id": opaque([task["task_id"], channel, number, sig]),
                                  "anchor": anchor, "anchor_status": anchor_status,
                                  "ownership": "unresolved_hypothesis", "visual_ownership_certified": False})
        witnesses.append({"witness_id": opaque([task["task_id"], channel, number, "unresolved"]), "record_indices": indices[key],
                          "header_id": None, "anchor": None, "anchor_status": "unresolved_visual_ownership_alternative",
                          "ownership": "unknown", "visual_ownership_certified": False})
        groups.append({"semantic_group": number, "value": value, "semantic_key": key, "source_record_indices": indices[key], "witnesses": witnesses})
    return groups


def field_card_vector(card, embedding):
    metadata = (properties(card) + [float(card.get("balance") == k) for k in ("debit", "credit", None)]
                + [float(card.get("period_type") == k) for k in ("instant", "duration", None)]
                + [float(card.get("abstract") is True), float(card.get("abstract") is None)])
    return np.concatenate((embedding, metadata, card["retrieval_scores"], [0])).astype("float32")


def main():
    if (DEST / "features/manifest.json").exists():
        raise RuntimeError("v26 common candidate bank already frozen")
    tasks = json.loads((ALIGN / "candidates/tasks.json").read_text())
    public = {t["task_id"]: t for t in json.loads((FIELDS / "public/tasks.json").read_text())}
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((ALIGN / "localization/public_anchors.json").read_text())
    word_index = json.loads((ALIGN / "native_word_features/index.json").read_text())
    catalog = json.loads((SCHEMA / "field_catalog.json").read_text())
    namespaces = json.loads((SCHEMA / "report_namespaces.json").read_text())
    emb = np.load(FIELDS / "shared_embeddings/catalog.npz")["features"]
    strings = json.loads((FIELDS / "shared_embeddings/catalog_metadata.json").read_text())["strings"]
    vectors = {s: emb[i] for i, s in enumerate(strings)}
    if len(tasks) != 840 or set(public) != {t["task_id"] for t in tasks}:
        raise ValueError("All 840 public targets required")

    pools = {}
    for report in sorted({t["report"] for t in tasks}):
        ns = set(namespaces[report])
        pools[report] = [c for c in (report_card(f, report) for f in catalog if f["namespace"] in ns) if c and numeric_card(c)]
    queries = {tid: query_text(public[tid], public[tid].get("common_visual_observation") or {}) for tid in public}
    qvec = encode_texts(sorted({s for q in queries.values() for s in q.values()}))
    page_text = {h: PageText(p["words"]) for h, p in pages.items()}
    report_pages = defaultdict(set)
    for t in tasks:
        report_pages[t["report"]].add(anchors[t["task_id"]]["page_hash"])
    report_month_days = {}
    for report, hashes in report_pages.items():
        md = set()
        for h in hashes:
            for text, _ in page_text[h].items:
                md.update((m["date"].month, m["date"].day) for m in date_mentions(text))
        report_month_days[report] = md

    print("Expanding candidates", flush=True)
    expanded, counts = [], Counter()
    for t in tasks:
        h = anchors[t["task_id"]]["page_hash"]
        new = expand_task(t, public[t["task_id"]], page_text[h], report_month_days[t["report"]], pools[t["report"]], vectors, qvec)
        expanded.append(new)
        counts["fields"] += len(new["field_candidates"])
        counts["periods"] += len(new["period_candidates"])
        counts["units"] += len(new["unit_candidates"])
    (DEST / "public").mkdir(parents=True, exist_ok=True)
    task_file = DEST / "public/tasks.json"
    save(task_file, expanded)

    print("Building records and witnesses", flush=True)
    records = [candidate_records(t) for t in expanded]
    inventory = []
    for t, row in zip(expanded, records, strict=True):
        groups = {c: build_groups(t, row[c], anchors[t["task_id"]]["headers"], c) for c in CHANNELS}
        inventory.append({"task_id": t["task_id"], "company": t["company"], "source_year": t["source_year"],
                          "components": groups, "visible_scope_certified": False})
    record_file, inventory_file = DEST / "public/records.json", DEST / "public/witnesses.json"
    save(record_file, [{c: [{"value": r["value"], "metadata": r["metadata"]} for r in row[c]] for c in CHANNELS} for row in records])
    save(inventory_file, inventory)

    print("Encoding texts", flush=True)
    texts = sorted({r["text"] for row in records for c in CHANNELS if c != "field" for r in row[c]})
    embeddings = encode_texts(texts)
    projection = np.load(ALIGN / "features/projection.npy")
    vision_projection = np.load(ALIGN.parent / "witness_composition_v23/features/vision_projection.npy")
    folder = DEST / "features"
    folder.mkdir(parents=True, exist_ok=True)
    np.save(folder / "text_projection.npy", projection)
    np.save(folder / "vision_projection.npy", vision_projection)
    context_src = ALIGN / "features/context.npy"
    ids = json.loads((ALIGN / "features/ids.json").read_text())
    if ids != [t["task_id"] for t in tasks]:
        raise ValueError("Context vector ordering differs from public targets")
    np.save(folder / "context.npy", np.load(context_src))
    save(folder / "ids.json", ids)

    print("Building features", flush=True)
    cached_pages, fcounts = {}, Counter()
    for number, (t, row, item) in enumerate(zip(expanded, records, inventory, strict=True), 1):
        ident = t["task_id"]
        visible = anchors[ident]
        h = visible["page_hash"]
        if h not in cached_pages:
            cached_pages[h] = np.load(word_index[h]["feature_file"])
        words = cached_pages[h]
        public_headers = visible["headers"]
        pool = []
        for header in public_headers.values():
            for a in header["anchors"] if header["may_supply_transfer_hypotheses"] else []:
                pool.append(anchor_vector(a, header["original_header"]["kind"], t["target_box"], words, vision_projection, projection))
        pool.append(anchor_vector(None, "unknown", t["target_box"], words, vision_projection, projection, unresolved=True))
        arrays = {"common_header_pool": np.stack(pool)}
        for c in CHANNELS:
            vecs = []
            for r in row[c]:
                v = extras(r, c, t["target_box"])
                if c == "field":
                    v[:401] = field_card_vector(r["metadata"], vectors[field_text(r["metadata"])])
                else:
                    v[:384] = embeddings[r["text"]]
                if c == "unit_quantity":
                    v[436] = r["metadata"].get("semantic_zero_hypothesis_not_literal_zero", False)
                vecs.append(v)
            null = np.zeros(448, dtype="float32")
            null[415] = 1
            null[416 + CHANNELS.index(c)] = 1
            full = np.stack(vecs + [null])
            base = np.concatenate((full[:, :384] @ projection, full[:, 384:]), 1).astype("float32")
            groups = item["components"][c]
            if c == "field":
                arrays["field"] = np.stack([base[g["source_record_indices"][0]] for g in groups])
                fcounts["field_nodes"] += len(groups)
                continue
            nodes, gids = [], []
            for g in groups:
                for w in g["witnesses"]:
                    kind = c if c != "unit_quantity" else "unit"
                    if w["header_id"]:
                        kind = public_headers[w["header_id"]]["original_header"]["kind"]
                    enc = anchor_vector(w["anchor"], kind, t["target_box"], words, vision_projection, projection, w["ownership"] == "unknown")
                    nodes.append(witness_node(base, w, g, c, t["target_box"], enc, row[c]))
                    gids.append(g["semantic_group"])
            arrays[c] = np.stack(nodes)
            arrays[c + "_groups"] = np.array(gids, dtype="int64")
            fcounts[c + "_witness_nodes"] += len(nodes)
        if any(not np.isfinite(v).all() for v in arrays.values()):
            raise ValueError("Nonfinite v26 features")
        np.savez_compressed(folder / (ident + ".npz"), **arrays)
        if number % 140 == 0:
            print("v26 features", number, "/ 840", flush=True)
    save(DEST / "public/manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "targets": 840, "field_top_k": FIELD_K,
        "mean_candidates": {k: v / 840 for k, v in counts.items()},
        "public_task_sha256": sha(task_file), "public_record_sha256": sha(record_file), "witness_inventory_sha256": sha(inventory_file),
        "source_v21_task_sha256": sha(ALIGN / "candidates/tasks.json"), "builder_sha256": sha(Path(__file__)),
        "reference_teacher_or_reserved_files_read": False, "new_generative_visual_calls": 0,
        "candidate_repair_is_common_to_all_controls_not_ontology_attribution": True})
    save(folder / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "targets": 840,
        "field_dimension": 112, "leaf_witness_dimension": NODE_DIM, "header_pool_dimension": ANCHOR_DIM,
        "counts": dict(fcounts), "witness_inventory_sha256": sha(inventory_file),
        "files": {p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.name != "manifest.json"}})
    print("v26 bank complete", dict(counts), dict(fcounts), flush=True)


if __name__ == "__main__":
    main()

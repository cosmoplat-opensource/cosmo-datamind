"""Development-only ceiling diagnosis for repaired common candidate banks.

Reads the frozen dev references (post hoc, like v24/v25 audits) to measure how
much candidate recall improves when: field pools are not cut at 64 and exclude
abstract/non-numeric cards; periods are generated from whole-page OCR; units
are scanned from whole-page OCR with pence/shares/bare-$ handling. Nothing here
changes any prediction, feature or training input. No reserved source is read.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import numpy as np
from native_binding_expand_v17 import DEST as EXPAND
from native_field_retrieval_v12 import DEST as FIELDS
from native_field_retrieval_v12 import SCHEMA, field_text, query_text, report_card
from native_header_alignment_v21 import DEST as ALIGN
from native_visible_periods_v17 import DATE_RE, MONTHS, calendar_start, date_mentions
from native_witness_inventory_v23 import DEST as WITNESSES
from pilot_features import encode_texts
from prepare import save
from score_native_binding_v17 import scoring_references

ISO, XI = "{http://www.xbrl.org/2003/iso4217}", "{http://www.xbrl.org/2003/instance}"
NUMERIC_TYPES = ("monetaryItemType", "sharesItemType", "perShareItemType", "decimalItemType", "percentItemType",
                 "pureItemType", "integerItemType", "nonNegativeIntegerItemType", "areaItemType", "volumeItemType",
                 "energyItemType", "massItemType", "lengthItemType", "durationItemType", "floatItemType", "doubleItemType")
YEAR_RE = re.compile(r"(?<!\d)(?:19|20|21)\d{2}(?!\d)")
DEST = WITNESSES / "posthoc_candidate_ceiling_v26"


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


def page_period_bank(words, report_month_days):
    """Whole-page period hypotheses from OCR only (no source year, no references)."""
    lines = text_lines(words)
    texts = [" ".join(words[i]["text"] for i in ids) for ids in lines]
    texts += [a + " " + b for a, b in zip(texts, texts[1:])]
    periods, month_days, years = {}, set(report_month_days), set()

    def add(period, basis):
        periods.setdefault(json.dumps(period, sort_keys=True), {"period": period, "basis": basis})

    for text in texts:
        years.update(int(y) for y in YEAR_RE.findall(text))
        for m in date_mentions(text):
            end = m["date"]
            month_days.add((end.month, end.day))
            add({"kind": "instant", "instant": end.isoformat()}, "explicit_date")
            add({"kind": "instant", "instant": (end - timedelta(days=1)).isoformat()}, "explicit_date_minus_one")
            start = calendar_start(end)
            if start:
                add({"kind": "duration", "start_date": start.isoformat(), "end_date": end.isoformat()}, "calendar_annual_at_explicit_date")
        mentions = date_mentions(text)
        for first, second in zip(mentions, mentions[1:]):
            between = text[first["end"]:second["start"]]
            if re.fullmatch(r"\s*(?:to|through|until|[-–—])\s*", between, re.I) and first["date"] <= second["date"]:
                add({"kind": "duration", "start_date": first["date"].isoformat(), "end_date": second["date"].isoformat()}, "explicit_range")
    # Printed column years × visible month/days (page first, then the report's other public pages).
    for year in years:
        if not 1990 <= year <= 2100:
            continue
        for month, day in month_days:
            try:
                end = date(year, month, day)
            except ValueError:
                continue
            add({"kind": "instant", "instant": end.isoformat()}, "printed_year_x_visible_month_day")
            add({"kind": "instant", "instant": (end - timedelta(days=1)).isoformat()}, "printed_year_x_visible_month_day_minus_one")
            start = calendar_start(end)
            if start:
                add({"kind": "duration", "start_date": start.isoformat(), "end_date": end.isoformat()}, "annual_printed_year_x_visible_month_day")
    return periods


UNIT_RE = re.compile(
    r"(?P<cur>US\$|A\$|C\$|NZ\$|HK\$|S\$|\$|£|€|GBP|EUR|USD|AUD|CAD|NZD|CHF|JPY|CNY|ZAR|HKD|SGD)\s*"
    r"(?P<scale>millions?|thousands?|billions?|000(?:[’']s)?|'000|mn|bn|m|k)?(?![A-Za-z])", re.I)
CODES = {"us$": "USD", "a$": "AUD", "c$": "CAD", "nz$": "NZD", "hk$": "HKD", "s$": "SGD", "$": "USD", "£": "GBP", "€": "EUR"}
SCALE_WORD_RE = re.compile(r"(?<![A-Za-z])(?:in\s+)?(?P<scale>millions?|thousands?|billions?|'000|000s|£m|\$m|€m)(?![A-Za-z])", re.I)


def scale_power(scale):
    s = (scale or "").lower()
    if s in {"billion", "billions", "bn"}:
        return 9
    if s in {"million", "millions", "mn", "m", "£m", "$m", "€m"}:
        return 6
    if s.startswith(("thousand", "000", "'000")) or s == "k":
        return 3
    return 0


def page_unit_bank(words):
    text = " ".join(w["text"] for w in words)
    bank = {}

    def add(unit, power, kind, basis):
        bank.setdefault(json.dumps([unit, power], sort_keys=True), {"unit": unit, "power": power, "kind": kind, "basis": basis})

    currencies = set()
    for m in UNIT_RE.finditer(text):
        cur = CODES.get(m["cur"].lower(), m["cur"].upper())
        currencies.add(cur)
        add({"numerator": [ISO + cur], "denominator": []}, scale_power(m["scale"]), "monetary", "visible_currency")
        if re.search(r"per\s+share|/\s*share", text[m.end():m.end() + 40], re.I):
            add({"numerator": [ISO + cur], "denominator": [XI + "shares"]}, 0, "currency_per_share", "visible_per_share")
    # Page-level scale words apply to every visible currency.
    powers = {scale_power(m["scale"]) for m in SCALE_WORD_RE.finditer(text)} | {0}
    for cur in currencies:
        for p in powers:
            add({"numerator": [ISO + cur], "denominator": []}, p, "monetary", "visible_currency_x_page_scale")
        # Pence/cents per share and pence amounts.
        if re.search(r"(?<![A-Za-z])(?:pence|p)(?![A-Za-z])", text) or re.search(r"(?<![A-Za-z])cents?(?![A-Za-z])", text, re.I):
            add({"numerator": [ISO + cur], "denominator": [XI + "shares"]}, -2, "currency_per_share", "visible_minor_unit_per_share")
            add({"numerator": [ISO + cur], "denominator": []}, -2, "monetary", "visible_minor_unit")
        add({"numerator": [ISO + cur], "denominator": [XI + "shares"]}, 0, "currency_per_share", "currency_present_per_share_hypothesis")
    if re.search(r"(?<![A-Za-z])shares?(?![A-Za-z])", text, re.I) or re.search(r"number\s+of", text, re.I):
        for p in powers:
            add({"numerator": [XI + "shares"], "denominator": []}, p, "shares", "visible_shares_word")
    if re.search(r"%|per\s*cent|percent|ratio|times|basis\s+points", text, re.I):
        add({"numerator": [XI + "pure"], "denominator": []}, 0, "rate", "visible_rate_word")
        add({"numerator": [XI + "pure"], "denominator": []}, -2, "rate", "visible_percent_as_fraction")
    return bank


def parse_printed(strings):
    values = set()
    for s in strings:
        t = s.strip().replace("−", "-").replace("–", "-").replace("—", "-")
        neg = t.startswith("(") and t.endswith(")")
        t = re.sub(r"[()$£€¥%\s]|US|GBP|EUR|USD|[A-Za-z]+", "", t)
        if not t:
            continue
        for variant in (t.replace(",", ""), t.replace(".", "").replace(",", ".")):
            try:
                v = Decimal(variant)
            except InvalidOperation:
                continue
            if v.is_finite():
                values.add(-v if neg else v)
    return values


def main():
    tasks = json.loads((ALIGN / "candidates/tasks.json").read_text())
    public = {t["task_id"]: t for t in json.loads((FIELDS / "public/tasks.json").read_text())}
    future = {t["task_id"]: t for t in tasks if t["source_year"] != 2023}
    references, _ = scoring_references(future)
    pages = json.loads((ALIGN / "localization/public_pages.json").read_text())
    anchors = json.loads((ALIGN / "localization/public_anchors.json").read_text())
    catalog = json.loads((SCHEMA / "field_catalog.json").read_text())
    namespaces = json.loads((SCHEMA / "report_namespaces.json").read_text())
    emb = np.load(FIELDS / "shared_embeddings/catalog.npz")["features"]
    strings = json.loads((FIELDS / "shared_embeddings/catalog_metadata.json").read_text())["strings"]
    vectors = {s: emb[i] for i, s in enumerate(strings)}

    pools = {}
    for report in sorted({t["report"] for t in future.values()}):
        ns = set(namespaces[report])
        cards = [c for c in (report_card(f, report) for f in catalog if f["namespace"] in ns) if c]
        pools[report] = cards
    queries = {tid: query_text(public[tid], public[tid].get("common_visual_observation") or {}) for tid in future}
    qvec = encode_texts(sorted({s for q in queries.values() for s in q.values()}))

    # Report-level visible month/days from all of that report's public pages.
    report_pages = defaultdict(set)
    for tid, t in future.items():
        report_pages[t["report"]].add(anchors[tid]["page_hash"])
    report_month_days = {}
    for report, hashes in report_pages.items():
        md = set()
        for h in hashes:
            text = " ".join(w["text"] for w in pages[h]["words"])
            md.update((m["date"].month, m["date"].day) for m in date_mentions(text))
        report_month_days[report] = md

    field_ranks = {"all": [], "numeric": []}
    counts = Counter()
    per_target = {}
    period_basis, unit_basis = Counter(), Counter()
    page_cache = {}
    for tid, t in future.items():
        ref = references[tid]
        cards = pools[t["report"]]
        card_ids = ["{" + c["namespace"] + "}" + c["name"] for c in cards]
        matrix = np.stack([vectors[field_text(c)] for c in cards])
        q = queries[tid]
        failed = public[tid].get("common_observation_failed")
        s = (.8 * (matrix @ qvec[q["native_row"]]) + .2 * (matrix @ qvec[q["section"]])) if failed else \
            (.65 * (matrix @ qvec[q["visual_row"]]) + .25 * (matrix @ qvec[q["native_row"]]) + .1 * (matrix @ qvec[q["section"]]))
        order = np.argsort(-s, kind="stable")
        in_pool = ref["field"] in card_ids
        rank_all = [card_ids[i] for i in order].index(ref["field"]) if in_pool else None
        numeric = [i for i in order if numeric_card(cards[i])]
        rank_num = [card_ids[i] for i in numeric].index(ref["field"]) if in_pool and numeric_card(cards[card_ids.index(ref["field"])]) else None
        field_ranks["all"].append(rank_all)
        field_ranks["numeric"].append(rank_num)
        counts["field_in_report_pool"] += in_pool
        counts["field_numeric_nonabstract"] += rank_num is not None
        counts["pool_size_all"] += len(cards)
        counts["pool_size_numeric"] += len(numeric)

        h = anchors[tid]["page_hash"]
        if h not in page_cache:
            page_cache[h] = (page_period_bank(pages[h]["words"], report_month_days[t["report"]]), page_unit_bank(pages[h]["words"]))
        pbank, ubank = page_cache[h]
        old_periods = {json.dumps({k: v for k, v in p["period"].items() if k != "complete"}, sort_keys=True)
                       for p in t["period_candidates"] if p["period"].get("complete")}
        rkey = json.dumps(ref["period"], sort_keys=True)
        old_p, new_p = rkey in old_periods, rkey in pbank
        counts["period_old"] += old_p
        counts["period_new"] += new_p or old_p
        counts["period_bank_size"] += len(pbank)
        if new_p:
            period_basis[pbank[rkey]["basis"]] += 1
        elif not old_p:
            period_basis["MISSED kind=" + ref["period"]["kind"]] += 1

        old_units = {(json.dumps(u["unit"], sort_keys=True), str(u["multiplier"])) for u in t["unit_candidates"]
                     if u.get("unit") and u.get("multiplier") is not None}
        ukey = json.dumps(ref["unit"], sort_keys=True)
        old_u = any(k == ukey for k, _ in old_units)
        new_u = any(json.dumps(v["unit"], sort_keys=True) == ukey for v in ubank.values())
        counts["unit_old"] += old_u
        counts["unit_new"] += new_u or old_u
        counts["unit_bank_size"] += len(ubank)
        if not (new_u or old_u):
            unit_basis["MISSED " + ukey[:60]] += 1

        # Quantity: printed literals × (old multipliers ∪ new powers) × sign.
        printed = parse_printed([q_["printed_value"] for q_ in t["quantity_candidates"]] + public[tid].get("native_ocr_strings", []) +
                                (public[tid].get("common_visual_observation") or {}).get("literal_readings", []))
        refq = Decimal(ref["quantity"])
        old_mults = {Decimal(str(u["multiplier"])) for u in t["unit_candidates"] if u.get("multiplier") is not None}
        new_mults = old_mults | {Decimal(10) ** v["power"] for v in ubank.values()}
        old_q = any(p * m * sgn == refq for p in printed for m in old_mults for sgn in (1, -1)) or (refq == 0 and any(s.strip() in "-−–—" for s in public[tid].get("native_ocr_strings", [])))
        new_q = any(p * m * sgn == refq for p in printed for m in new_mults for sgn in (1, -1)) or old_q
        counts["quantity_old"] += old_q
        counts["quantity_new"] += new_q
        # Unit×quantity pair with consistent multiplier.
        def pair_ok(bank_items):
            for unit, mult in bank_items:
                if unit == ukey and any(p * mult * sgn == refq for p in printed for sgn in (1, -1)):
                    return True
            return refq == 0 and old_q and any(unit == ukey for unit, _ in bank_items)
        old_pair = pair_ok([(k, Decimal(m)) for k, m in old_units])
        new_pair = old_pair or pair_ok([(json.dumps(v["unit"], sort_keys=True), Decimal(10) ** v["power"]) for v in ubank.values()])
        counts["unit_quantity_old"] += old_pair
        counts["unit_quantity_new"] += new_pair

        old_field = any(("{" + c["namespace"] + "}" + c["name"]) == ref["field"] for c in t["field_candidates"])
        counts["field_old64"] += old_field
        complete_old = old_field and old_p and old_pair
        complete_new = (rank_num is not None) and (new_p or old_p) and new_pair
        counts["complete_old"] += complete_old
        counts["complete_new"] += complete_new
        per_target[tid] = {"field_rank_all": rank_all, "field_rank_numeric": rank_num, "period_old": old_p, "period_new": new_p,
                           "unit_old": old_u, "unit_new": new_u, "quantity_old": old_q, "quantity_new": new_q,
                           "unit_quantity_old": old_pair, "unit_quantity_new": new_pair, "complete_old": complete_old, "complete_new": complete_new}

    n = len(future)
    def recall_at(ranks, k):
        return sum(r is not None and r < k for r in ranks) / n
    summary = {
        "development_only_posthoc_not_an_accuracy": True, "targets": n,
        "field": {"in_report_pool": counts["field_in_report_pool"], "numeric_nonabstract": counts["field_numeric_nonabstract"],
                  "old_top64": counts["field_old64"],
                  "mean_pool_all": counts["pool_size_all"] / n, "mean_pool_numeric": counts["pool_size_numeric"] / n,
                  "recall_at_k_all_pool": {k: recall_at(field_ranks["all"], k) for k in (64, 128, 256, 512, 1024, 100000)},
                  "recall_at_k_numeric_pool": {k: recall_at(field_ranks["numeric"], k) for k in (64, 128, 256, 512, 1024, 100000)}},
        "period": {"old": counts["period_old"], "new": counts["period_new"], "mean_bank": counts["period_bank_size"] / n, "basis": dict(period_basis)},
        "unit": {"old": counts["unit_old"], "new": counts["unit_new"], "mean_bank": counts["unit_bank_size"] / n, "missed": dict(unit_basis)},
        "quantity": {"old": counts["quantity_old"], "new": counts["quantity_new"]},
        "unit_quantity": {"old": counts["unit_quantity_old"], "new": counts["unit_quantity_new"]},
        "complete_binding_available": {"old_top64": counts["complete_old"], "new_uncut_numeric": counts["complete_new"]},
    }
    DEST.mkdir(parents=True, exist_ok=True)
    save(DEST / "ceiling_summary.json", summary)
    save(DEST / "per_target.json", per_target)
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

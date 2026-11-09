"""Reference-blind date candidates from the already-shared visible text.

Calendar annual/comparative completions are explicit uncertain hypotheses, not
known boundaries. Bare years cannot supply a month/day. Administrative source
year is never read. All candidates remain uncertified for the pointed cell.
"""

import calendar
from datetime import date, timedelta
import re

from prepare import opaque


MONTHS = {name.lower(): i for i in range(1, 13) for name in (calendar.month_name[i], calendar.month_abbr[i])}
MONTH_RE = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
DATE_RE = re.compile(
    r"(?<!\d)(?P<iso>\d{4}-\d{2}-\d{2})(?!\d)|"
    r"(?<!\d)(?P<day>\d{1,2})(?:st|nd|rd|th)?\s*(?P<month>" + MONTH_RE + r")\.?\s*,?\s*(?P<year>\d{4})(?!\d)|"
    r"(?P<month_first>" + MONTH_RE + r")\.?\s+(?P<day_after>\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(?P<year_after>\d{4})(?!\d)", re.I)
ANNUAL_RE = re.compile(r"(?:for\s+(?:the\s+)?)?(?:financial\s+)?year\s+(?:ended|ending|to)\s*$", re.I)


def date_mentions(text):
    result = []
    for match in DATE_RE.finditer(text):
        try:
            if match["iso"]:
                value = date.fromisoformat(match["iso"])
            elif match["day"]:
                value = date(int(match["year"]), MONTHS[match["month"].lower()], int(match["day"]))
            else:
                value = date(int(match["year_after"]), MONTHS[match["month_first"].lower()], int(match["day_after"]))
        except ValueError:
            continue
        if not 1800 <= value.year <= 2200:
            continue
        result.append({"date": value, "text": match.group(0), "start": match.start(), "end": match.end(),
                       "annual_end_anchor": bool(ANNUAL_RE.search(text[max(0, match.start()-100):match.start()])),
                       "opening_anchor": bool(re.search(r"(?:at|as\s+at|balance\s+at|opening)\s*$", text[max(0, match.start()-50):match.start()], re.I))})
    return result


def calendar_start(end):
    # Leap-day anniversaries need a convention. Do not fabricate one.
    try:
        return end.replace(year=end.year-1) + timedelta(days=1)
    except ValueError:
        return None


def visible_period_candidates(task, maximum=48):
    observation = task.get("common_visual_observation") or {}
    evidence = [{"kind": "native_top_text", "text": task.get("public_top_text", ""), "box": None},
                {"kind": "native_row_text", "text": task.get("public_row_text", ""), "box": None}]
    evidence += [{"kind": "observed_" + h["kind"], "text": h["text"], "box": h.get("box")}
                 for h in observation.get("headers", []) if h["kind"] in {"period", "version", "other"}]
    evidence += [{"kind": "observed_section_text", "text": observation.get("section_text", ""), "box": None}]
    candidates = {}
    def add(period, source, basis, assumption=False):
        key = tuple(sorted(period.items()))
        item = candidates.setdefault(key, {"candidate_id": opaque(["visible-period-v17", period]),
                    "period": {**period, "complete": True}, "displayed_year": None,
                    "header": None, "uncertain": True, "header_ownership_certified": False,
                    "source_year_used": False, "calendar_convention_assumed": True,
                    "supporting_visible_evidence": []})
        item["calendar_convention_assumed"] &= assumption
        support = {"basis": basis, "text": source["text"], "box": source["box"], "evidence_kind": source["kind"]}
        if support not in item["supporting_visible_evidence"]:
            item["supporting_visible_evidence"].append(support)
    all_mentions = [(source, mention) for source in evidence for mention in date_mentions(source["text"])]
    years = sorted({int(y) for source in evidence for y in re.findall(r"(?<!\d)(?:18|19|20|21|22)\d{2}(?!\d)", source["text"])})
    for source, mention in all_mentions:
        end = mention["date"]
        add({"kind": "instant", "instant": end.isoformat()}, source, "explicit_visible_calendar_date")
        if mention["opening_anchor"]:
            add({"kind": "instant", "instant": (end-timedelta(days=1)).isoformat()}, source,
                "possible_prior_close_for_visible_opening_date", True)
        if mention["annual_end_anchor"]:
            start = calendar_start(end)
            if start:
                add({"kind": "duration", "start_date": start.isoformat(), "end_date": end.isoformat()}, source,
                    "calendar_annual_interval_for_visible_year_ended_heading", True)
            # Use only other printed years, with a nearby visible full-date anchor.
            for year in years:
                if abs(year-end.year) > 2:
                    continue
                try:
                    comparative = end.replace(year=year)
                except ValueError:
                    continue
                begin = calendar_start(comparative)
                if begin:
                    add({"kind": "duration", "start_date": begin.isoformat(), "end_date": comparative.isoformat()}, source,
                        "printed_comparative_year_plus_visible_annual_month_day", True)
    # Explicit intervals are useful even when the document uses a nonannual period.
    for source in evidence:
        mentions = date_mentions(source["text"])
        for first, second in zip(mentions, mentions[1:]):
            between = source["text"][first["end"]:second["start"]]
            if re.fullmatch(r"\s*(?:to|through|until|[-–—])\s*", between, re.I) and first["date"] <= second["date"]:
                add({"kind": "duration", "start_date": first["date"].isoformat(), "end_date": second["date"].isoformat()},
                    source, "explicit_visible_start_to_end", False)
    # Prefer explicit boundaries, without looking at any private references.
    result = sorted(candidates.values(), key=lambda p: (p["calendar_convention_assumed"], p["candidate_id"]))
    return result[:maximum], max(0, len(result)-maximum)

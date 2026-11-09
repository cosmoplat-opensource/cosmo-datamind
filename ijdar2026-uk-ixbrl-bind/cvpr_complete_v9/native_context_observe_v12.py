"""Shared native visual hypotheses with no target-field or multiplier oracle.

Component failures remain unknown independently. Valid literal readings are
not discarded merely because a proposed context range is invalid. None of
these hypotheses is a certified scope/identity annotation.
"""

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from canonicalization_prepare import valid_box
from chart_native_repair_v2 import available_balance
from crop_observe_v3 import image_part
from native_baseline import MODEL
from native_context_crops_v12 import DEST
from openai import APIError, OpenAI
from prepare import opaque, save, sha

PROMPT = """Image 0 is the unchanged full report page. Each target has an original pixel target crop, a full-width local ROW context crop and a preceding COLUMN context crop. Image indexes and their full-page context boxes are provided. Read the row meaning and column/dimension meaning separately; do not substitute a column account/dimension label for the numerical row concept. Context crops are observation windows and can include unrelated headers, not certified scope ranges. Identify each pointed cell's visible meaning and supporting headers. You are NOT given its target taxonomy field or unit multiplier. Candidate field cards come from public-schema retrieval and can all be wrong. Do not force a field match or use report year as the cell's period.
Read literal numeric glyphs without applying a unit or canonical sign. Keep ambiguous locale parses. A dash is not automatically zero. Read row meaning, local statement title, period, units, entity/group and dimensions from their own visible evidence. A page can contain different tables and unit conventions; a global monetary scale may not apply to per-share amounts, rates or counts. Header coverage is a hypothesis: preserve ambiguous or unknown ranges. Never invent an exact date from a year alone. Do not infer hidden taxonomy semantics from an opaque target ID.
Return JSON {"targets":[{"task_id":"...","literal_readings":["printed numeric glyph"],"row_text":"visible row label or unknown","section_text":"visible title or unknown","presentation":"income_statement|cashflow_statement|balance_sheet|equity_change|note|other|unknown","quantity_role":"standalone_amount|component_contribution|net_change|rate|count|unknown","field_candidates":["f00001"],"headers":[{"id":"h0","kind":"field|unit|period|entity|dimension|version|other","text":"exact visible text","box":[left,top,right,bottom],"scope_boxes":[[left,top,right,bottom]],"uncertain":true}],"unit_candidates":[{"header_id":"h0","kind":"monetary|currency_per_share|rate|count|shares|other|unknown","currency":"GBP|USD|EUR|AUD|other|unknown","multiplier":"1000000 or null","uncertain":true}],"period_candidates":[{"header_id":"h0","kind":"instant|duration|unknown","displayed_year":2023,"start_date":"YYYY-MM-DD or null","end_date":"YYYY-MM-DD or null","uncertain":true}],"entity_text":"visible text or unknown","dimension_text":"visible text or unknown","version_text":"visible text or unknown","context_box":[left,top,right,bottom] or null,"uncertain":true}]}.
All boxes use normalized FULL PAGE coordinates. All requested IDs appear exactly once. Max 3 field candidates, 10 headers, 4 unit candidates, 4 period candidates, 4 literal readings per target. Header IDs are local to each target. Dates, multipliers, boxes and displayed_year may be null when unknown. All field, scope and semantic assignments remain hypotheses, not certified matches.
"""
PRESENTATIONS = {
    "income_statement",
    "cashflow_statement",
    "balance_sheet",
    "equity_change",
    "note",
    "other",
    "unknown",
}
ROLES = {
    "standalone_amount",
    "component_contribution",
    "net_change",
    "rate",
    "count",
    "unknown",
}
HEADER_KINDS = {"field", "unit", "period", "entity", "dimension", "version", "other"}
UNIT_KINDS = {
    "monetary",
    "currency_per_share",
    "rate",
    "count",
    "shares",
    "other",
    "unknown",
}


def date_or_none(value):
    if value is None:
        return None
    from datetime import date

    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("Malformed visible date")
    return date.fromisoformat(value).isoformat()


def multiplier_or_none(value):
    if value is None:
        return None
    number = Decimal(str(value))
    allowed = {Decimal(10) ** i for i in range(-9, 10)}
    if not number.is_finite() or number not in allowed:
        raise ValueError("Multiplier outside common public decimal scale vocabulary")
    return str(number)


def text(value, limit=500):
    return value[:limit] if isinstance(value, str) and value.strip() else "unknown"


def contains(box, target):
    return (
        box[0] <= (target[0] + target[2]) / 2 <= box[2]
        and box[1] <= (target[1] + target[3]) / 2 <= box[3]
    )


def validate(raw, tasks):
    body = json.loads(raw)
    rows = body.get("targets") if isinstance(body, dict) else None
    if not isinstance(rows, list) or len(rows) != len(tasks):
        raise ValueError("Incomplete native target inventory")
    registered = {t["task_id"]: t for t in tasks}
    if {r.get("task_id") for r in rows if isinstance(r, dict)} != set(registered):
        raise ValueError("Duplicate or unknown native target ID")
    result = []
    for row in rows:
        task = registered[row["task_id"]]
        flags = []
        field_ids = {c["field_id"] for c in task["field_candidates"]}
        fields = row.get("field_candidates", [])
        if not isinstance(fields, list):
            fields = []
            flags.append("field_candidates_invalid")
        fields = list(
            dict.fromkeys(
                i for i in fields[:3] if isinstance(i, str) and i in field_ids
            )
        )
        literals = row.get("literal_readings", [])
        if not isinstance(literals, list):
            literals = []
            flags.append("literal_readings_invalid")
        literals = list(
            dict.fromkeys(
                v for v in literals[:4] if isinstance(v, str) and 0 < len(v) <= 80
            )
        )
        headers = []
        header_ids = set()
        proposed = row.get("headers", [])
        if not isinstance(proposed, list):
            proposed = []
            flags.append("headers_invalid")
        for h in proposed[:10]:
            if (
                not isinstance(h, dict)
                or not isinstance(h.get("id"), str)
                or h["id"] in header_ids
                or h.get("kind") not in HEADER_KINDS
                or not valid_box(h.get("box"))
            ):
                flags.append("header_geometry_or_identity_invalid")
                continue
            header_ids.add(h["id"])
            scopes = h.get("scope_boxes")
            if not isinstance(scopes, list):
                scopes = []
            valid = [b for b in scopes[:8] if valid_box(b)]
            headers.append(
                {
                    "id": h["id"],
                    "kind": h["kind"],
                    "text": text(h.get("text")),
                    "box": h["box"],
                    "scope_boxes": valid,
                    "uncertain": h.get("uncertain") is not False
                    or len(valid) != len(scopes),
                    "scope_is_model_hypothesis_not_gold": True,
                }
            )
        units = []
        proposed = row.get("unit_candidates", [])
        if not isinstance(proposed, list):
            proposed = []
        for u in proposed[:4]:
            if (
                not isinstance(u, dict)
                or u.get("header_id") not in header_ids
                or u.get("kind") not in UNIT_KINDS
            ):
                flags.append("unit_reference_invalid")
                continue
            try:
                multiplier = multiplier_or_none(u.get("multiplier"))
            except (ValueError, InvalidOperation):
                multiplier = None
                flags.append("unit_multiplier_unknown_or_invalid")
            units.append(
                {
                    "header_id": u["header_id"],
                    "kind": u["kind"],
                    "currency": text(u.get("currency"), 30),
                    "multiplier": multiplier,
                    "uncertain": u.get("uncertain") is not False or multiplier is None,
                }
            )
        periods = []
        proposed = row.get("period_candidates", [])
        if not isinstance(proposed, list):
            proposed = []
        for p in proposed[:4]:
            if (
                not isinstance(p, dict)
                or p.get("header_id") not in header_ids
                or p.get("kind") not in ("instant", "duration", "unknown")
            ):
                flags.append("period_reference_invalid")
                continue
            year = p.get("displayed_year")
            if type(year) is not int or not 1800 <= year <= 2200:
                year = None
            try:
                start, end = (
                    date_or_none(p.get("start_date")),
                    date_or_none(p.get("end_date")),
                )
                if start and end and start > end:
                    raise ValueError("Inverted visible interval")
            except ValueError:
                start = end = None
                flags.append("period_dates_invalid")
            periods.append(
                {
                    "header_id": p["header_id"],
                    "kind": p["kind"],
                    "displayed_year": year,
                    "start_date": start,
                    "end_date": end,
                    "uncertain": p.get("uncertain") is not False
                    or end is None
                    or p["kind"] == "unknown"
                    or (p["kind"] == "duration" and start is None),
                }
            )
        context = row.get("context_box")
        if context is not None and (
            not valid_box(context) or not contains(context, task["target_box"])
        ):
            context = None
            flags.append("context_scope_unknown_or_invalid")
        result.append(
            {
                "task_id": task["task_id"],
                "literal_readings": literals,
                "field_candidates": fields,
                "headers": headers,
                "unit_candidates": units,
                "period_candidates": periods,
                "row_text": text(row.get("row_text")),
                "section_text": text(row.get("section_text")),
                "presentation": row.get("presentation")
                if row.get("presentation") in PRESENTATIONS
                else "unknown",
                "quantity_role": row.get("quantity_role")
                if row.get("quantity_role") in ROLES
                else "unknown",
                "entity_text": text(row.get("entity_text")),
                "dimension_text": text(row.get("dimension_text")),
                "version_text": text(row.get("version_text")),
                "context_box": context,
                "uncertain": row.get("uncertain") is not False
                or bool(flags)
                or not fields
                or not units
                or not periods,
                "component_validation_flags": flags,
                "full_visible_binding_not_certified": True,
            }
        )
    return result


def request(tasks):
    targets, parts = [], [image_part(tasks[0]["image"], tasks[0]["image_sha256"])]
    for task in tasks:
        row = {k: task[k] for k in ("task_id", "target_box", "native_ocr_strings", "crop_ocr_hypotheses", "public_row_text", "public_top_text", "field_candidates")}
        row["crop_image_index"] = len(parts)
        parts.append(image_part(task["crop_image"], task["crop_image_sha256"]))
        for kind in ("row", "column"):
            row[kind+"_context_image_index"] = len(parts)
            row[kind+"_context_box"] = task[kind+"_context_box"]
            parts.append(image_part(task[kind+"_context_image"], task[kind+"_context_sha256"]))
        targets.append(row)
    return [{"role": "user", "content": [{"type": "text", "text": PROMPT+json.dumps({"targets": targets}, ensure_ascii=False)}]+parts}]


def run(limit=0):
    tasks_file = DEST / "public/tasks.json"
    tasks = json.loads(tasks_file.read_text())
    protocol = json.loads((DEST / "protocol.json").read_text())
    if sha(tasks_file) != protocol["hashes"]["public_tasks"]:
        raise RuntimeError("Frozen native pointed inputs changed")
    grouped = defaultdict(list)
    for task in tasks:
        grouped[task["page_id"]].append(task)
    batches = []
    for page, values in sorted(grouped.items()):
        values.sort(key=lambda t: t["task_id"])
        for start in range(0, len(values), 2):
            group = values[start : start + 2]
            batches.append(
                (
                    opaque(
                        [
                            "native-context-crops-v12",
                            page,
                            [t["task_id"] for t in group],
                        ]
                    ),
                    group,
                )
            )
    folder = DEST / "common_visual"
    design = {
        "model": MODEL,
        "thinking": "disabled",
        "temperature": 0,
        "max_output_tokens": 5000,
        "targets_per_batch": 2,
        "maximum_images": 7,
        "maximum_transport_attempts": 4,
        "format_errors_not_retried": True,
        "component_failures_preserved_as_unknown": True,
        "expected_targets": len(tasks),
        "expected_batches": len(batches),
        "prompt": PROMPT,
        "public_tasks_sha256": sha(tasks_file),
        "runner_sha256": sha(__file__),
        "same_raw_visual_responses_for_all_controls": True,
        "private_teacher_or_reference_files_read": False,
        "target_field_or_multiplier_given": False,
        "human_runtime_confirmations": 0,
    }
    frozen = folder / "design.json"
    if frozen.exists() and json.loads(frozen.read_text()) != design:
        raise RuntimeError("Frozen native observation protocol changed")
    save(frozen, design)
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("BIRD_AGENT_API_KEY")
    if not key:
        raise RuntimeError("Dedicated DeepSeek credential missing")
    quota = httpx.Client(timeout=15)
    available, initial = available_balance(quota, key)
    if not available or initial < 10:
        raise RuntimeError("Insufficient available DeepSeek balance")
    client = OpenAI(
        api_key=key, base_url="https://api.deepseek.com", timeout=120, max_retries=0
    )
    rows = []
    try:
        for j, (ident, group) in enumerate(batches):
            if limit and j >= limit:
                break
            final = folder / "batches" / (ident + ".json")
            if final.exists():
                record = json.loads(final.read_text())
                if record["task_ids"] != [t["task_id"] for t in group]:
                    raise RuntimeError("Frozen native batch identities changed")
                rows.append(record)
                continue
            if j % 20 == 0:
                ok, current = available_balance(quota, key)
                save(
                    folder / "account_probe.json",
                    {
                        "initial_balance_cny": initial,
                        "current_balance_cny": current,
                        "debit_includes_concurrent_work": True,
                    },
                )
                if not ok or current < 10 or initial - current > 60:
                    raise RuntimeError("Predeclared account limit reached")
            messages = request(group)
            record = None
            for attempt in range(1, 5):
                marker = folder / "attempts" / f"{ident}_{attempt}.json"
                raw_file = folder / "raw" / f"{ident}_{attempt}.json"
                if raw_file.exists():
                    raw = json.loads(raw_file.read_text())
                elif marker.exists():
                    previous = json.loads(marker.read_text())
                    if previous["status"] == "transport_failure":
                        continue
                    raise RuntimeError(
                        "Interrupted native API attempt needs uniform recovery review"
                    )
                else:
                    save(
                        marker,
                        {
                            "status": "started",
                            "utc": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    begin = time.monotonic()
                    try:
                        reply = client.chat.completions.create(
                            model=MODEL,
                            temperature=0,
                            max_tokens=5000,
                            response_format={"type": "json_object"},
                            extra_body={"thinking": {"type": "disabled"}},
                            messages=messages,
                        )
                        raw = {
                            "content": reply.choices[0].message.content,
                            "finish_reason": reply.choices[0].finish_reason,
                            "returned_model": reply.model,
                            "usage": reply.usage.model_dump() if reply.usage else {},
                            "seconds": time.monotonic() - begin,
                        }
                        save(raw_file, raw)
                        save(marker, {"status": "response_saved"})
                    except (APIError, httpx.TransportError) as exc:
                        status = getattr(exc, "status_code", None)
                        retryable = (
                            isinstance(exc, httpx.TransportError)
                            or type(exc).__name__
                            in ("APIConnectionError", "APITimeoutError")
                            or status in (408, 429, 500, 502, 503, 504)
                        )
                        failure = {
                            "status": "transport_failure"
                            if retryable
                            else "terminal_api_failure",
                            "http_status": status,
                            "error_type": type(exc).__name__,
                            "error": str(exc).replace(key, "[REDACTED]")[:600],
                        }
                        save(marker, failure)
                        if status in (401, 402, 403):
                            raise RuntimeError(
                                "DeepSeek authorization/account failure; queue stopped"
                            ) from None
                        if retryable:
                            continue
                        record = failure
                        break
                try:
                    if raw["finish_reason"] != "stop":
                        raise ValueError("Incomplete native correspondence generation")
                    targets = validate(raw["content"], group)
                    record = {
                        "status": "completed",
                        "targets": targets,
                        "raw_response_sha256": sha(raw_file),
                        "returned_model": raw["returned_model"],
                    }
                except (ValueError, TypeError, KeyError) as exc:
                    record = {
                        "status": "invalid_model_format",
                        "error": str(exc)[:500],
                        "raw_response_sha256": sha(raw_file),
                    }
                break
            record = record or {"status": "transport_attempt_budget_exhausted"}
            record.update(
                batch_id=ident,
                task_ids=[t["task_id"] for t in group],
                image_sha256=group[0]["image_sha256"],
            )
            save(final, record)
            rows.append(record)
            save(
                folder / "progress.json",
                {
                    "processed_batches": len(rows),
                    "expected_batches": len(batches),
                    "status_counts": dict(Counter(r["status"] for r in rows)),
                },
            )
            print(
                "Native field/period/unit correspondence",
                len(rows),
                "/",
                len(batches),
                record["status"],
                flush=True,
            )
        save(
            folder / ("smoke_manifest.json" if limit else "manifest.json"),
            {
                "complete": not limit and len(rows) == len(batches),
                "processed_batches": len(rows),
                "expected_batches": len(batches),
                "records": rows,
                "public_tasks_sha256": sha(tasks_file),
                "design_sha256": sha(frozen),
                "publisher_teacher_or_reference_files_read": False,
                "target_field_or_multiplier_supplied": False,
                "all_components_are_uncertified_visual_hypotheses": True,
            },
        )
    finally:
        client.close()
        quota.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("Nonnegative smoke limit required")
    run(args.limit)

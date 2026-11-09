"""Identical local/global DeepSeek visual evidence for every learning control.

Local table boundaries and quantity roles are model hypotheses, not gold labels.
The original page, crop, corrected OCR and field declarations are shared. No
feedback, publisher transform or reference quantity enters the visual calls.
"""

import base64
from collections import defaultdict
import json
import os
from pathlib import Path
import time

import httpx
from openai import OpenAI
from PIL import Image

from canonicalization_observe import request_targets, validate as validate_base
from canonicalization_prepare import valid_box
from crop_candidates_v3 import DEST
from native_baseline import MODEL, balance
from prepare import opaque, save, sha

ROLES = {
    "standalone_amount",
    "component_contribution",
    "net_change",
    "rate",
    "count",
    "unknown",
}
PROMPT = (
    "Image 0 is the full source page. The following images are exact original-pixel "
    "crops of the listed targets, in crop_image_index order. Field identity, target "
    "region and unit multiplier are explicit shared oracle inputs in this diagnostic. "
    "Use the crop to read the literal quantity and the full page to locate the target's "
    "own table or section. A page can contain different tables and conventions. "
    "Choose only a listed numeric candidate; otherwise choose null. Do not force a "
    "dash or absent glyph to zero. Preserve alternative number conventions. "
    "The value required is the canonical supplied field, not necessarily the signed "
    "contribution printed in the statement. Expenses/outflows can be displayed as "
    "negative contributions while their field amount is positive; net results can "
    "remain signed. Debit/credit alone is not a universal rule. Notes and statements "
    "can use different conventions for the same field. "
    "Also describe the visible quantity role: standalone_amount, component_contribution, "
    "net_change, rate, count, or unknown. This is a presentation hypothesis, not a "
    "reference annotation. Return a context_box enclosing the relevant local table "
    "or section on the FULL PAGE, normalized coordinates, or null if ambiguous. "
    "Do not claim a complete period, entity or semantic binding from missing evidence. "
    'Return JSON {"targets":[{"task_id":"...","candidate_index":0 or null,'
    '"row_text":"literal visible row label","section_text":"literal local title",'
    '"presentation":"income_statement|cashflow_statement|balance_sheet|equity_change|note|other|unknown",'
    '"quantity_role":"standalone_amount|component_contribution|net_change|rate|count|unknown",'
    '"context_box":[left,top,right,bottom] or null,"uncertain":true}]}. '
    "Every requested ID must appear once. Both context and roles remain hypotheses.\n"
)


def validate(raw, tasks):
    rows = validate_base(raw, tasks)
    by_id = {t["task_id"]: t for t in tasks}
    for row in rows:
        if row.get("quantity_role") not in ROLES:
            raise ValueError("Unknown presentation quantity-role vocabulary")
        box = row.get("context_box")
        if box is not None:
            if not valid_box(box):
                raise ValueError("Invalid local context box")
            target = by_id[row["task_id"]]["target_box"]
            x = (target[0] + target[2]) / 2
            y = (target[1] + target[3]) / 2
            if not box[0] <= x <= box[2] or not box[1] <= y <= box[3]:
                raise ValueError("Proposed local context excludes its own target")
    return rows


def image_part(path, digest):
    path = Path(path)
    if sha(path) != digest:
        raise RuntimeError("Frozen common visual pixels changed")
    with Image.open(path) as im:
        if max(im.size) > 8192:
            raise ValueError(
                "Source violates API dimension limit; common source adapter required"
            )
        mime = Image.MIME[im.format]
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:"
            + mime
            + ";base64,"
            + base64.b64encode(path.read_bytes()).decode(),
            "detail": "high",
        },
    }


def messages(tasks):
    targets = request_targets(tasks)
    for index, (target, task) in enumerate(zip(targets, tasks), 1):
        target["crop_image_index"] = index
        target["crop_OCR_hypotheses"] = task["crop_OCR_hypotheses"]
    text = PROMPT + json.dumps({"targets": targets}, ensure_ascii=False)
    parts = [
        {"type": "text", "text": text},
        image_part(tasks[0]["image"], tasks[0]["image_sha256"]),
    ]
    parts.extend(image_part(t["crop_image"], t["crop_image_sha256"]) for t in tasks)
    return [{"role": "user", "content": parts}]


def run(limit=0):
    file = DEST / "public/tasks.json"
    tasks = json.loads(file.read_text())
    protocol = json.loads((DEST / "protocol.json").read_text())
    if sha(file) != protocol["public_tasks_sha256"]:
        raise RuntimeError("Frozen common target inputs changed")
    grouped = defaultdict(list)
    for task in tasks:
        grouped[task["page_id"]].append(task)
    batches = []
    for page, values in sorted(grouped.items()):
        values.sort(key=lambda t: t["task_id"])
        for offset in range(0, len(values), 8):
            group = values[offset : offset + 8]
            batches.append((opaque([page, [t["task_id"] for t in group]]), group))
    dest = DEST / "common_visual"
    design = {
        "model": MODEL,
        "thinking": "disabled",
        "temperature": 0,
        "max_output_tokens": 3000,
        "max_attempts_per_batch": 2,
        "targets_per_batch": 8,
        "max_images_per_batch": 9,
        "expected_batches": len(batches),
        "expected_targets": len(tasks),
        "image_detail": "high",
        "prompt": PROMPT,
        "public_tasks_sha256": sha(file),
        "all_controls_receive_identical_raw_responses": True,
        "local_scope_and_quantity_roles_not_gold": True,
        "publisher_references_or_feedback_read": False,
        "human_confirmations": 0,
        "runner_sha256": sha(__file__),
    }
    design_file = dest / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise RuntimeError("Frozen common crop observation design changed")
    save(design_file, design)
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("BIRD_AGENT_API_KEY")
    if not key:
        raise RuntimeError("Dedicated DeepSeek credential missing")
    client = OpenAI(
        api_key=key, base_url="https://api.deepseek.com", timeout=90, max_retries=0
    )
    quota = httpx.Client(timeout=30)
    available, initial = balance(quota, key)
    if not available or initial < 10:
        raise RuntimeError("Insufficient available API balance")
    records = []
    try:
        for i, (ident, group) in enumerate(batches):
            if limit and i >= limit:
                break
            target = dest / "batches" / (ident + ".json")
            if target.exists():
                record = json.loads(target.read_text())
                if record["task_ids"] != [t["task_id"] for t in group]:
                    raise RuntimeError("Frozen batch ID changed")
                records.append(record)
                continue
            if i % 25 == 0:
                available, current = balance(quota, key)
                save(
                    dest / "account_probe.json",
                    {
                        "current_balance_cny": current,
                        "debit_includes_concurrent_work": True,
                    },
                )
                if not available or current < 10 or initial - current > 50:
                    break
            input_messages = messages(group)
            result = None
            for attempt in (1, 2):
                marker = dest / "attempts" / f"{ident}_{attempt}.json"
                raw_file = dest / "raw" / f"{ident}_{attempt}.json"
                if raw_file.exists():
                    raw = json.loads(raw_file.read_text())
                elif marker.exists():
                    continue
                else:
                    save(
                        marker,
                        {
                            "status": "started",
                            "task_ids": [t["task_id"] for t in group],
                        },
                    )
                    start = time.monotonic()
                    try:
                        reply = client.chat.completions.create(
                            model=MODEL,
                            temperature=0,
                            max_tokens=3000,
                            response_format={"type": "json_object"},
                            extra_body={"thinking": {"type": "disabled"}},
                            messages=input_messages,
                        )
                        raw = {
                            "content": reply.choices[0].message.content,
                            "finish_reason": reply.choices[0].finish_reason,
                            "returned_model": reply.model,
                            "usage": reply.usage.model_dump() if reply.usage else {},
                            "seconds": time.monotonic() - start,
                        }
                        save(raw_file, raw)
                        save(marker, {"status": "response_saved"})
                    except Exception as exc:
                        status = getattr(exc, "status_code", None)
                        save(
                            dest / "failures" / f"{ident}_{attempt}.json",
                            {
                                "error_type": type(exc).__name__,
                                "http_status": status,
                                "error": str(exc).replace(key, "[REDACTED]")[:700],
                            },
                        )
                        if status in (400, 401, 402, 403):
                            raise RuntimeError(
                                "API input/account failure; preserve progress"
                            ) from exc
                        continue
                try:
                    if raw["finish_reason"] != "stop":
                        raise ValueError("Incomplete shared crop response")
                    result = {
                        "status": "completed",
                        "targets": validate(raw["content"], group),
                        "raw_response_sha256": sha(raw_file),
                        "returned_model": raw["returned_model"],
                    }
                    break
                except (ValueError, KeyError, TypeError) as exc:
                    save(
                        dest / "failures" / f"{ident}_{attempt}_format.json",
                        {"error": str(exc)[:400]},
                    )
            if result is None:
                result = {"status": "attempt_budget_exhausted", "targets": []}
            result.update(
                batch_id=ident,
                task_ids=[t["task_id"] for t in group],
                image_sha256=group[0]["image_sha256"],
            )
            save(target, result)
            records.append(result)
            save(
                dest / "progress.json",
                {
                    "processed_batches": len(records),
                    "expected_batches": len(batches),
                    "successful_batches": sum(
                        r["status"] == "completed" for r in records
                    ),
                },
            )
            print(
                "Shared crop/local context",
                len(records),
                "/",
                len(batches),
                result["status"],
                flush=True,
            )
        save(
            dest / "manifest.json",
            {
                "complete": len(records) == len(batches),
                "records": records,
                "expected_batches": len(batches),
                "processed_batches": len(records),
                "public_tasks_sha256": sha(file),
                "design_sha256": sha(design_file),
                "scope_hypotheses_not_ownership_gold": True,
                "private_reference_or_feedback_files_read": False,
            },
        )
    finally:
        client.close()
        quota.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    run(parser.parse_args().limit)

"""Reference-blind ChartQAPro API qualification, preserving every old record.

Only uniform image-input changes and preidentified transport failures qualify
for new calls. Incomplete model answers receive no correctness-based retries.
All future conditions must use the same image policy and four-attempt maximum.
"""

import base64
import json
import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx
from chart_api_inputs_v2 import DEST
from chart_native import response_record
from native_baseline import MODEL, balance
from openai import APIError, OpenAI
from PIL import Image
from prepare import OUT, save, sha
from public_benchmarks import ROOT

ORIGINAL = OUT / "chart_native_baseline"
RETRYABLE = {408, 429, 500, 502, 503, 504}


def qualification(old, image_changed):
    if image_changed:
        return "uniform_image_input_changed", 4
    if old["status"] == "transport_failure":
        return "registered_transport_failure", 2
    return "byte_identical_preserved", 0


def request(task, record):
    path = Path(record["api_image"])
    if sha(path) != record["api_image_sha256"]:
        raise RuntimeError("Uniform API image changed")
    with Image.open(path) as im:
        mime = Image.MIME[im.format]
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": task["prompt"]},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:"
                        + mime
                        + ";base64,"
                        + base64.b64encode(path.read_bytes()).decode(),
                        "detail": "high",
                    },
                },
            ],
        }
    ]


def available_balance(client, key):
    for attempt in range(3):
        try:
            return balance(client, key)
        except (httpx.TransportError, httpx.HTTPStatusError):
            if attempt == 2:
                raise RuntimeError(
                    "Balance endpoint unavailable; inference queue not advanced"
                ) from None
    raise AssertionError("Unreachable")


def run():
    public = ROOT / "chartqapro/public/tasks.json"
    tasks = sorted(json.loads(public.read_text()), key=lambda t: t["task_id"])
    inputs_file = DEST / "input_manifest.json"
    inputs = json.loads(inputs_file.read_text())
    old_manifest_file = ORIGINAL / "inference_manifest.json"
    old_manifest = json.loads(old_manifest_file.read_text())
    old_outcomes_file = ORIGINAL / "outcomes.json"
    if (
        len(tasks) != 1948
        or len({t["task_id"] for t in tasks}) != 1948
        or not inputs["complete"]
        or inputs["public_tasks_sha256"] != sha(public)
        or not old_manifest["complete"]
        or old_manifest["outcomes_sha256"] != sha(old_outcomes_file)
    ):
        raise RuntimeError(
            "Complete frozen public and old inference inventories required"
        )
    old_rows = json.loads(old_outcomes_file.read_text())
    old = {r["task_id"]: r for r in old_rows}
    if len(old) != len(old_rows) or set(old) != {t["task_id"] for t in tasks}:
        raise RuntimeError("Duplicate/incomplete old chart inventory")
    ledger = []
    for task in tasks:
        ident = task["task_id"]
        source = ORIGINAL / "responses" / (ident + ".json")
        if json.loads(source.read_text()) != old[ident]:
            raise RuntimeError("Old saved response differs from frozen outcomes")
        reason, allowed = qualification(
            old[ident], inputs["images"][task["image_sha256"]]["changed"]
        )
        ledger.append(
            {
                "task_id": ident,
                "reason": reason,
                "new_transport_attempts_allowed": allowed,
                "original_response_sha256": sha(source),
                "original_status": old[ident]["status"],
            }
        )
    design = {
        "stage": "E3 corrected native baseline inference qualification only",
        "model": MODEL,
        "thinking": "disabled",
        "temperature": 0,
        "max_output_tokens": 1024,
        "image_detail": "high",
        "OCR_supplied": False,
        "prompt_and_prior_conversation_context_unchanged": True,
        "qualification_rule": "all changed API image inputs, including old successes; all unchanged-input transport failures; every other success and model failure byte-identically preserved",
        "legacy_unchanged_transport_attempts": 2,
        "uniform_maximum_transport_attempts_per_input": 4,
        "changed_image_input_fresh_transport_attempts": 4,
        "future_controls_must_use_same_image_and_four_attempt_policy": True,
        "format_errors_retried": False,
        "terminal_answers_and_scoring_files_read": False,
        "scoring_deferred_until_main_method_freeze": True,
        "human_confirmations": 0,
        "maximum_observed_account_debit_cny": 10,
        "minimum_balance_cny": 10,
        "input_manifest_sha256": sha(inputs_file),
        "public_tasks_sha256": sha(public),
        "original_outcomes_sha256": sha(old_outcomes_file),
        "original_manifest_sha256": sha(old_manifest_file),
        "original_design_sha256": sha(ORIGINAL / "design.json"),
        "runner_sha256": sha(__file__),
        "shared_response_validator_sha256": sha(
            Path(__file__).with_name("chart_native.py")
        ),
    }
    frozen = DEST / "inference_design.json"
    if frozen.exists() and json.loads(frozen.read_text()) != design:
        raise RuntimeError("Frozen reference-blind qualification policy changed")
    save(frozen, design)
    save(DEST / "qualification_ledger.json", ledger)
    snapshot = DEST / "runner_snapshot.py"
    if snapshot.exists() and sha(snapshot) != sha(__file__):
        raise RuntimeError("Frozen qualification implementation changed")
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
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
    qualifiers = {r["task_id"]: r for r in ledger}

    def infer(task):
        ident = task["task_id"]
        record = qualifiers[ident]
        final = DEST / "responses" / (ident + ".json")
        if final.exists():
            if (
                record["new_transport_attempts_allowed"] == 0
                and sha(final) != record["original_response_sha256"]
            ):
                raise RuntimeError("Preserved same-input record changed")
            return json.loads(final.read_text())
        if record["new_transport_attempts_allowed"] == 0:
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes((ORIGINAL / "responses" / (ident + ".json")).read_bytes())
            return old[ident]
        image = inputs["images"][task["image_sha256"]]
        messages = request(task, image)
        attempts = record["new_transport_attempts_allowed"]
        for attempt in range(1, attempts + 1):
            marker = DEST / "attempts" / f"{ident}_{attempt}.json"
            rawfile = DEST / "raw" / f"{ident}_{attempt}.json"
            if rawfile.exists():
                raw = json.loads(rawfile.read_text())
                if raw["api_image_sha256"] != image["api_image_sha256"]:
                    raise RuntimeError("Cached response image changed")
                result = response_record(ident, raw)
                save(final, result)
                return result
            if marker.exists():
                prior = json.loads(marker.read_text())
                if prior["status"] == "transport_failure":
                    if attempt == attempts:
                        save(final, prior)
                        return prior
                    continue
                return {
                    "task_id": ident,
                    "status": "unresolved_interrupted_attempt",
                    "answer": None,
                }
            save(
                marker,
                {
                    "status": "started",
                    "api_image_sha256": image["api_image_sha256"],
                    "started_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
            started = time.monotonic()
            try:
                reply = client.chat.completions.create(
                    model=MODEL,
                    temperature=0,
                    max_tokens=1024,
                    extra_body={"thinking": {"type": "disabled"}},
                    messages=messages,
                )
                raw = {
                    "content": reply.choices[0].message.content,
                    "finish_reason": reply.choices[0].finish_reason,
                    "returned_model": reply.model,
                    "usage": reply.usage.model_dump() if reply.usage else {},
                    "seconds": time.monotonic() - started,
                    "image_sha256": task["image_sha256"],
                    "api_image_sha256": image["api_image_sha256"],
                }
                save(rawfile, raw)
                result = response_record(ident, raw)
                save(final, result)
                save(
                    marker,
                    {
                        "status": result["status"],
                        "api_image_sha256": image["api_image_sha256"],
                    },
                )
                return result
            except (APIError, httpx.TransportError) as exc:
                status = getattr(exc, "status_code", None)
                retryable = (
                    isinstance(exc, httpx.TransportError)
                    or type(exc).__name__ in ("APIConnectionError", "APITimeoutError")
                    or status in RETRYABLE
                )
                result = {
                    "task_id": ident,
                    "status": "transport_failure"
                    if retryable
                    else "terminal_api_failure",
                    "answer": None,
                    "http_status": status,
                    "error_type": type(exc).__name__,
                    "error": str(exc).replace(key, "[REDACTED]")[:800],
                    "api_image_sha256": image["api_image_sha256"],
                }
                save(marker, result)
                if status in (401, 402, 403):
                    save(final, result)
                    raise RuntimeError(
                        "DeepSeek authorization/account failure; queue stopped"
                    ) from None
                if not retryable or attempt == attempts:
                    save(final, result)
                    return result
        raise AssertionError("Unreachable attempt loop")

    try:
        rows = []
        preserved = [
            t
            for t in tasks
            if qualifiers[t["task_id"]]["new_transport_attempts_allowed"] == 0
        ]
        pending = [
            t
            for t in tasks
            if qualifiers[t["task_id"]]["new_transport_attempts_allowed"] > 0
        ]
        rows.extend(infer(t) for t in preserved)
        # Submit at most two calls ahead, so balance/account faults stop the queue.
        with ThreadPoolExecutor(max_workers=2) as pool:
            for start in range(0, len(pending), 2):
                futures = [
                    pool.submit(infer, task) for task in pending[start : start + 2]
                ]
                for future in as_completed(futures):
                    rows.append(future.result())
                ok, current = available_balance(quota, key)
                save(
                    DEST / "account_probe.json",
                    {
                        "initial_balance_cny": initial,
                        "current_balance_cny": current,
                        "available": ok,
                        "account_debit_includes_other_concurrent_work": True,
                    },
                )
                save(
                    DEST / "progress.json",
                    {
                        "processed": len(rows),
                        "expected": len(tasks),
                        "qualified_new_input_or_transport_records": min(
                            start + 2, len(pending)
                        ),
                        "expected_qualified": len(pending),
                        "statuses": dict(Counter(r["status"] for r in rows)),
                    },
                )
                print(
                    "Chart API qualification",
                    min(start + 2, len(pending)),
                    "/",
                    len(pending),
                    flush=True,
                )
                if not ok or current < 10 or initial - current > 10:
                    raise RuntimeError("Predeclared account limit reached")
        rows.sort(key=lambda r: r["task_id"])
        if len(rows) != len(tasks) or {r["task_id"] for r in rows} != set(old):
            raise RuntimeError("Incomplete complete-inventory qualification matrix")
        save(DEST / "outcomes.json", rows)
        save(
            DEST / "inference_manifest.json",
            {
                "complete": True,
                "expected_tasks": len(tasks),
                "records": len(rows),
                "statuses": dict(Counter(r["status"] for r in rows)),
                "byte_identical_preserved_records": len(preserved),
                "registered_new_input_or_transport_records": len(pending),
                "terminal_reference_files_read": False,
                "scoring_run": False,
                "outcomes_sha256": sha(DEST / "outcomes.json"),
                "design_sha256": sha(frozen),
                "input_manifest_sha256": sha(inputs_file),
                "qualification_ledger_sha256": sha(DEST / "qualification_ledger.json"),
            },
        )
    finally:
        client.close()
        quota.close()


if __name__ == "__main__":
    run()

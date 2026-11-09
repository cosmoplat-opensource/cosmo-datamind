"""Frozen DeepSeek native-image ChartQAPro baseline; inference only.

The endorsed Direct prompt and official prior conversation context are used
unchanged. Terminal references and scores remain unopened until method freeze.
"""

import argparse
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import json
import os
import threading
import time

import httpx
from openai import OpenAI
from PIL import Image

from native_baseline import MODEL, balance
from prepare import OUT, save, sha
from public_benchmarks import ROOT


def messages(task, root):
    image = root / task["image"]
    if sha(image) != task["image_sha256"]:
        raise RuntimeError("Frozen original chart changed")
    with Image.open(image) as im:
        mime = Image.MIME[im.format]
    content = [
        {"type": "text", "text": task["prompt"]},
        {
            "type": "image_url",
            "image_url": {
                "url": "data:"
                + mime
                + ";base64,"
                + base64.b64encode(image.read_bytes()).decode(),
                "detail": "high",
            },
        },
    ]
    return [{"role": "user", "content": content}]


def response_record(task_id, raw):
    complete = (
        raw["finish_reason"] == "stop"
        and isinstance(raw["content"], str)
        and bool(raw["content"].strip())
    )
    return dict(
        raw,
        task_id=task_id,
        status="completed" if complete else "incomplete_or_empty_model_answer",
        answer=raw["content"].strip() if complete else None,
    )


def run(workers=2, limit=0):
    root = ROOT / "chartqapro"
    tasks_file = root / "public/tasks.json"
    tasks = sorted(json.loads(tasks_file.read_text()), key=lambda t: t["task_id"])
    if len(tasks) != 1948 or len({t["task_id"] for t in tasks}) != 1948:
        raise RuntimeError("Official public chart inventory differs")
    if any(t["terminal_reference_disclosed"] is not False for t in tasks):
        raise RuntimeError("Terminal answer must be private")
    if limit:
        tasks = tasks[:limit]
    dest = OUT / ("chart_native_smoke" if limit else "chart_native_baseline")
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("BIRD_AGENT_API_KEY")
    if not key:
        raise RuntimeError("Dedicated DeepSeek credential missing")
    client = OpenAI(
        api_key=key, base_url="https://api.deepseek.com", timeout=120, max_retries=0
    )
    quota = httpx.Client(timeout=30)
    available, initial = balance(quota, key)
    if not available or initial < 10:
        raise RuntimeError("Insufficient available DeepSeek balance")
    design = {
        "stage": "E3 native-image official Direct baseline, inference only",
        "model": MODEL,
        "thinking": "disabled",
        "temperature": 0,
        "max_output_tokens": 1024,
        "image_detail": "high",
        "expected_tasks": len(tasks),
        "max_transport_attempts": 2,
        "format_errors_retried": False,
        "original_publisher_direct_prompt_unchanged": True,
        "OCR_supplied": False,
        "official_prior_conversation_answer_context_shared_for_all_controls": True,
        "terminal_answers_and_scoring_files_opened": False,
        "scoring_deferred_until_main_method_freeze": True,
        "human_confirmations": 0,
        "retryable_http_statuses": [408, 429, 500, 502, 503, 504],
        "maximum_observed_account_debit_cny": 60,
        "minimum_balance_cny": 10,
        "public_tasks_sha256": sha(tasks_file),
        "public_protocol_sha256": sha(root / "protocol.json"),
        "runner_sha256": sha(__file__),
    }
    design_file = dest / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise RuntimeError("Frozen native chart design changed")
    save(design_file, design)
    stop = threading.Event()

    def infer(task):
        ident = task["task_id"]
        final = dest / "responses" / (ident + ".json")
        if final.exists():
            return json.loads(final.read_text())
        public_messages = messages(task, root)
        for attempt in (1, 2):
            marker = dest / "attempts" / f"{ident}_{attempt}.json"
            raw_file = dest / "raw" / f"{ident}_{attempt}.json"
            if raw_file.exists():
                record = response_record(ident, json.loads(raw_file.read_text()))
                save(final, record)
                return record
            if marker.exists():
                previous = json.loads(marker.read_text())
                if previous["status"] != "transport_failure":
                    return {
                        "task_id": ident,
                        "status": "unresolved_interrupted_attempt",
                        "answer": None,
                    }
                continue
            if stop.is_set():
                return None
            save(
                marker,
                {
                    "status": "started",
                    "image_sha256": task["image_sha256"],
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
                    messages=public_messages,
                )
                raw = {
                    "content": reply.choices[0].message.content,
                    "finish_reason": reply.choices[0].finish_reason,
                    "returned_model": reply.model,
                    "usage": reply.usage.model_dump() if reply.usage else {},
                    "seconds": time.monotonic() - started,
                    "image_sha256": task["image_sha256"],
                }
                save(raw_file, raw)
                record = response_record(ident, raw)
                save(final, record)
                save(marker, {"status": record["status"]})
                return record
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                retryable = (
                    isinstance(exc, httpx.TransportError)
                    or type(exc).__name__ in ("APIConnectionError", "APITimeoutError")
                    or status in design["retryable_http_statuses"]
                )
                record = {
                    "task_id": ident,
                    "status": "transport_failure"
                    if retryable
                    else "terminal_api_failure",
                    "answer": None,
                    "http_status": status,
                    "error_type": type(exc).__name__,
                    "error": str(exc).replace(key, "[REDACTED]")[:800],
                }
                save(marker, record)
                if status in (401, 402, 403):
                    stop.set()
                if not retryable or attempt == 2:
                    save(final, record)
                    return record
        return {
            "task_id": ident,
            "status": "transport_attempt_budget_exhausted",
            "answer": None,
        }

    rows = []
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            iterator = iter(tasks)
            pending = {}
            for _ in range(workers):
                task = next(iterator, None)
                if task:
                    pending[pool.submit(infer, task)] = task
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    record = future.result()
                    if record:
                        rows.append(record)
                    if len(rows) % 50 == 0:
                        ok, current = balance(quota, key)
                        save(
                            dest / "account_probe.json",
                            {
                                "initial_balance_cny": initial,
                                "current_balance_cny": current,
                                "debit_includes_concurrent_work": True,
                            },
                        )
                        if not ok or current < 10 or initial - current > 60:
                            stop.set()
                    save(
                        dest / "progress.json",
                        {
                            "completed_records": len(rows),
                            "expected_tasks": len(tasks),
                            "statuses": {
                                s: sum(r["status"] == s for r in rows)
                                for s in {r["status"] for r in rows}
                            },
                            "stopped_for_api_or_account": stop.is_set(),
                            "seconds": time.monotonic() - started,
                        },
                    )
                    if len(rows) % 20 == 0:
                        print(
                            "Chart native inference",
                            len(rows),
                            "/",
                            len(tasks),
                            flush=True,
                        )
                    task = next(iterator, None) if not stop.is_set() else None
                    if task:
                        pending[pool.submit(infer, task)] = task
        save(dest / "outcomes.json", sorted(rows, key=lambda r: r["task_id"]))
        save(
            dest / "inference_manifest.json",
            {
                "complete": len(rows) == len(tasks),
                "expected_tasks": len(tasks),
                "written_records": len(rows),
                "scoring_run": False,
                "terminal_reference_files_read": False,
                "public_inputs_only": True,
                "outcomes_sha256": sha(dest / "outcomes.json"),
                "design_sha256": sha(design_file),
            },
        )
    finally:
        client.close()
        quota.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("Limit must be nonnegative")
    run(args.workers, args.limit)

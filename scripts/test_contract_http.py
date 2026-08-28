#!/usr/bin/env python3
"""Exercise the deployed read-only service against the contract delivery database."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


READ_ONLY_ENDPOINTS = [
    "/",
    "/api/health",
    "/api/db/check",
    "/api/overview",
    "/api/tables",
    "/api/table/dim_validation_case",
    "/api/table/dim_customer",
    "/api/table/fact_gl_entry_line",
    "/api/table/DWS_SAFETY_DAILY/info",
    "/api/graphs",
    "/api/routes",
    "/api/ont/metadata",
    "/api/metrics",
    "/api/glossary",
    "/api/skills",
    "/api/build/defaults",
    "/api/build/sources",
    "/api/conn/tables",
]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def fetch(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    request = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        content_type = response.headers.get("Content-Type", "")
        decoded = None
        if "json" in content_type:
            decoded = json.loads(body)
        return {
            "path": path,
            "status": response.status,
            "bytes": len(body),
            "content_type": content_type,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "json": decoded,
        }


def run(
    base_url: str,
    expected_tables: int,
    expected_rows: int,
    workers: int,
    requests: int,
) -> dict[str, Any]:
    endpoint_results = []
    endpoint_errors = []
    for path in READ_ONLY_ENDPOINTS:
        try:
            endpoint_results.append(fetch(base_url, path, 20.0))
        except Exception as exc:  # pragma: no cover - retained in test evidence
            endpoint_errors.append({"path": path, "error": repr(exc)})

    decoded = {
        item["path"]: item["json"]
        for item in endpoint_results
        if item["json"] is not None
    }
    route_count = int((decoded.get("/api/routes") or {}).get("count", 0))
    checks = {
        "all_endpoints_http_200": (
            not endpoint_errors
            and len(endpoint_results) == len(READ_ONLY_ENDPOINTS)
            and all(item["status"] == 200 for item in endpoint_results)
        ),
        "health_ok": (decoded.get("/api/health") or {}).get("ok") is True,
        "db_check_ok": (decoded.get("/api/db/check") or {}).get("ok") is True,
        "db_table_count_exact": (
            decoded.get("/api/db/check") or {}
        ).get("tables")
        == expected_tables,
        "overview_table_count_exact": (
            (decoded.get("/api/overview") or {}).get("kpi") or {}
        ).get("tables")
        == expected_tables,
        "overview_row_count_exact": (
            (decoded.get("/api/overview") or {}).get("kpi") or {}
        ).get("rows")
        == expected_rows,
        "documented_routes_at_least_8": route_count >= 8,
    }

    successful_paths = [
        item["path"] for item in endpoint_results if item["status"] == 200
    ]
    latencies: list[float] = []
    concurrent_errors: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                fetch,
                base_url,
                successful_paths[index % len(successful_paths)],
                20.0,
            ): successful_paths[index % len(successful_paths)]
            for index in range(requests)
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                result = future.result()
                if result["status"] != 200:
                    concurrent_errors.append(
                        {"path": path, "status": result["status"]}
                    )
                latencies.append(result["latency_ms"])
            except Exception as exc:  # pragma: no cover - retained in evidence
                concurrent_errors.append({"path": path, "error": repr(exc)})
    checks["concurrent_request_errors_zero"] = not concurrent_errors
    return {
        "ok": all(checks.values()),
        "tested_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "checks": checks,
        "endpoint_count": len(READ_ONLY_ENDPOINTS),
        "route_count": route_count,
        "endpoint_results": [
            {key: value for key, value in item.items() if key != "json"}
            for item in endpoint_results
        ],
        "endpoint_errors": endpoint_errors,
        "concurrency": {
            "workers": workers,
            "requests": requests,
            "errors": len(concurrent_errors),
            "error_examples": concurrent_errors[:5],
            "latency_ms": {
                "mean": round(statistics.mean(latencies), 3) if latencies else 0.0,
                "median": round(statistics.median(latencies), 3) if latencies else 0.0,
                "p95": round(percentile(latencies, 0.95), 3),
                "max": round(max(latencies), 3) if latencies else 0.0,
            },
        },
        "scope_note": (
            "This is a deployed read-only HTTP operability test. Route count does not "
            "by itself prove contract C-13 third-party integration acceptance."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8093")
    parser.add_argument("--expected-tables", type=int, default=200)
    parser.add_argument("--expected-rows", type=int, required=True)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--requests", type=int, default=320)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run(
        args.base_url,
        args.expected_tables,
        args.expected_rows,
        args.workers,
        args.requests,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_handle = tempfile.NamedTemporaryFile(
        prefix="contract_http_", suffix=".json", dir=args.output.parent, delete=False
    )
    temp_path = Path(temp_handle.name)
    temp_handle.close()
    try:
        temp_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, args.output)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

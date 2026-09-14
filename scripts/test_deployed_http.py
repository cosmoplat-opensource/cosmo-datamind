#!/usr/bin/env python3
"""Bounded HTTP acceptance against an already deployed COSMO DataMind service.

No server imports, configuration reads, object writes, LLM calls, or external
data-source fetches. Responses are validated in memory; reports omit response
bodies, business rows, connection names, URLs, credentials, and cookies.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import http.client
import json
import math
from pathlib import Path
import re
import statistics
import time
from typing import Any
from urllib.parse import urlsplit
import uuid


MAX_BODY = 2_000_000
_UNSET = object()


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_ms: float

    def json(self):
        try:
            return json.loads(self.body)
        except (ValueError, UnicodeError):
            return None


class Client:
    def __init__(self, base_url: str, timeout: float = 10):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("URL must be an http(s) origin without credentials, path, query, or fragment")
        if not 0 < (parsed.port or 80) < 65536 or not 2 <= timeout <= 30:
            raise ValueError("invalid port or timeout (allowed timeout: 2..30 seconds)")
        self.base_url = base_url.rstrip("/")
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.scheme = parsed.scheme
        self.timeout = timeout

    def request(self, method: str, path: str, *, body: Any = _UNSET,
                raw: bytes | None = None, headers: dict | None = None) -> Response:
        if not path.startswith("/") or path.startswith("//") or re.search(r"[\r\n\x00]", path):
            raise ValueError("only same-server absolute request paths are allowed")
        if method not in ("GET", "HEAD", "POST"):
            raise ValueError("unsupported probe method")
        if method == "POST" and path not in ("/api/query", "/api/sparql"):
            raise ValueError("POST probes are restricted to read-only query endpoints")
        request_headers = {"User-Agent": "DataMind-Deployment-Acceptance/1.0", "Accept": "application/json"}
        request_headers.update(headers or {})
        encoded = raw
        if body is not _UNSET:
            encoded = json.dumps(body).encode("utf-8")
        if encoded is not None:
            request_headers.setdefault("Content-Type", "application/json")
        cls = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        connection = cls(self.host, self.port, timeout=self.timeout)
        started = time.monotonic()
        try:
            # http.client preserves encoded traversal probes; requests normalizes
            # dot segments before transmission and would weaken these checks.
            connection.request(method, path, body=encoded, headers=request_headers)
            response = connection.getresponse()
            payload = response.read(MAX_BODY + 1)
            if len(payload) > MAX_BODY:
                raise ValueError("response exceeded the bounded probe size")
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, payload,
                            round((time.monotonic() - started) * 1000, 3))
        finally:
            connection.close()


def _query_ok(response):
    payload = response.json()
    return (isinstance(payload, dict) and payload.get("columns") == ["probe"]
            and payload.get("rows") == [{"probe": 1}])


def _guard_error(response, phrase):
    payload = response.json()
    return isinstance(payload, dict) and phrase in str(payload.get("error", ""))


def _no_store(response):
    return "no-store" in response.headers.get("cache-control", "").lower()


class Audit:
    def __init__(self, client: Client):
        self.client = client
        self.results: list[dict] = []
        self.baseline_tables = None

    def probe(self, name, method, path, *, statuses=(200,), validate=None, **kwargs):
        response = None
        result = {"name": name, "method": method, "path": path, "expected_status": list(statuses)}
        started = time.monotonic()
        try:
            response = self.client.request(method, path, **kwargs)
            valid = response.status in statuses and (validate is None or bool(validate(response)))
            result.update(ok=valid, status=response.status, elapsed_ms=response.elapsed_ms,
                          bytes=len(response.body))
            if not valid:
                result["failure"] = "status_or_contract_mismatch"
        except Exception as exc:
            # Exceptions may contain deployment paths or endpoint details. Record
            # their type only; never persist exception messages or response bodies.
            result.update(ok=False, status=None, elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                          failure=type(exc).__name__)
        self.results.append(result)
        print(f"{'PASS' if result['ok'] else 'FAIL'} {name} {result['elapsed_ms']:.1f}ms", flush=True)
        return response

    def standard_checks(self):
        health = lambda r: isinstance(r.json(), dict) and r.json().get("ok") is True
        self.probe("health_before", "GET", "/api/health", validate=health)
        db = self.probe("database_ready", "GET", "/api/db/check",
                        validate=lambda r: health(r) and isinstance(r.json().get("tables"), int)
                        and r.json()["tables"] > 0)
        if db and isinstance(db.json(), dict):
            self.baseline_tables = db.json().get("tables")
        root = self.probe("html_entry_no_store", "GET", "/", validate=lambda r: _no_store(r)
                          and "text/html" in r.headers.get("content-type", ""))
        self.probe("html_head_no_store", "HEAD", "/", validate=_no_store)
        self.probe("ui_version_no_store", "GET", "/api/uiver", validate=lambda r: _no_store(r)
                   and isinstance(r.json(), dict) and isinstance(r.json().get("v"), int)
                   and r.json()["v"] > 0)
        self.probe("build_references_no_store", "GET", "/api/build/references", validate=_no_store)
        self.probe("vendor_local_echarts", "GET", "/vendor/echarts.min.js",
                   validate=lambda r: len(r.body) > 1000 and "javascript" in r.headers.get("content-type", ""))
        # Extract only known self-hosted asset paths. Never follow server-supplied
        # URLs, arbitrary hrefs, or remote scripts.
        assets = sorted(set(re.findall(rb'/assets/(?:modules|styles)/[A-Za-z0-9_.-]+\.(?:js|css)',
                                       root.body if root else b"")))[:16]
        for asset in assets:
            path = asset.decode("ascii")
            self.probe("asset_" + path.rsplit("/", 1)[-1], "GET", path,
                       validate=lambda r: _no_store(r) and len(r.body) > 0)
        self.results.append({"name": "entry_references_modular_assets", "method": "LOCAL", "path": "/",
                             "ok": len(assets) >= 2, "elapsed_ms": 0, "asset_count": len(assets)})

        def sources_ready(response):
            payload = response.json()
            sources = payload.get("sources") if isinstance(payload, dict) else None
            if not isinstance(sources, list):
                return False
            demo = next((s for s in sources if isinstance(s, dict) and s.get("id") == "demo"), None)
            return bool(demo and demo.get("ready") and demo.get("tables") == self.baseline_tables)

        self.probe("builtin_source_matches_database_health", "GET", "/api/build/sources", validate=sources_ready)
        self.probe("sql_readonly_literal", "POST", "/api/query", body={"sql": "SELECT 1 AS probe", "src": "demo"},
                   validate=_query_ok)
        self.probe("sql_cte", "POST", "/api/query",
                   body={"sql": "WITH p(probe) AS (SELECT 1) SELECT probe FROM p", "src": "demo"}, validate=_query_ok)
        self.probe("sql_trailing_comment", "POST", "/api/query",
                   body={"sql": "SELECT 1 AS probe; -- acceptance comment", "src": "demo"}, validate=_query_ok)

        # Denial tests use a random nonexistent source as an extra safeguard:
        # even a missing SQL guard cannot route these statements to a real DB.
        absent_source = "__codex_http_probe_missing_" + uuid.uuid4().hex
        rejected_sql = [
            ("sql_delete_denied", 'DELETE FROM "__codex_probe_missing" WHERE 0', "只读"),
            ("sql_attach_denied", "ATTACH DATABASE ':memory:' AS probe", "只读"),
            ("sql_select_into_denied", 'SELECT 1 INTO "__codex_probe_missing"', "只读"),
            ("sql_stacking_denied", "SELECT 1; SELECT 2", "单条"),
            ("sql_comment_quote_stacking_denied", "SELECT 1 /* ' */; SELECT 2; -- '", "单条"),
            ("sql_executable_comment_denied", "SELECT 1 /*!50000 INTO OUTFILE '/__codex_probe_missing/out' */", "只读"),
        ]
        for name, sql, phrase in rejected_sql:
            self.probe(name, "POST", "/api/query", body={"sql": sql, "src": absent_source}, statuses=(400,),
                       validate=lambda r, phrase=phrase: _guard_error(r, phrase))
        self.probe("sql_type_denied", "POST", "/api/query", body={"sql": ["SELECT 1"], "src": absent_source},
                   statuses=(400,), validate=lambda r: _guard_error(r, "只读"))

        for name, payload in [("null", None), ("array", []), ("nonempty_array", [1]),
                              ("string", "SELECT 1"), ("number", 7), ("boolean", True)]:
            self.probe("json_" + name + "_denied", "POST", "/api/query", body=payload, statuses=(400,),
                       validate=lambda r: _guard_error(r, "JSON 对象"))
        self.probe("malformed_json_denied", "POST", "/api/query", raw=b"{", statuses=(400,),
                   validate=lambda r: _guard_error(r, "JSON 对象"))

        safe_query = {"sql": "SELECT 1 AS probe", "src": "demo"}
        self.probe("same_origin_allowed", "POST", "/api/query", body=safe_query,
                   headers={"Origin": self.client.base_url}, validate=_query_ok)
        self.probe("same_origin_referer_allowed", "POST", "/api/query", body=safe_query,
                   headers={"Referer": self.client.base_url + "/?acceptance=1"}, validate=_query_ok)
        for label, origin in [("opaque", "null"), ("foreign", "https://cross-origin.audit.invalid"),
                              ("malformed", "http://["), ("empty", "")]:
            self.probe("origin_" + label + "_denied", "POST", "/api/query", body=safe_query,
                       headers={"Origin": origin}, statuses=(403,), validate=lambda r: _guard_error(r, "CSRF"))
        self.probe("cross_origin_referer_denied", "POST", "/api/query", body=safe_query,
                   headers={"Referer": "https://cross-origin.audit.invalid/"}, statuses=(403,),
                   validate=lambda r: _guard_error(r, "CSRF"))
        for host in ("rebind.audit.invalid", "localhost.attacker.invalid", "127.0.0.1.attacker.invalid"):
            self.probe("host_denied_" + host, "GET", "/api/health",
                       headers={"Host": host, "Origin": "http://" + host}, statuses=(400,),
                       validate=lambda r: _guard_error(r, "Host"))

        for name, path, statuses in [
            ("vendor_sourcemap_denied", "/vendor/echarts.min.js.map", (404,)),
            ("vendor_traversal_denied", "/vendor/%2e%2e/%2e%2e/server.py", (400, 404)),
            ("asset_traversal_denied", "/assets/modules/%2e%2e/%2e%2e/server.py", (400, 404)),
            ("asset_source_denied", "/assets/server.py", (404,)),
            ("doc_traversal_denied", "/doc/%2e%2e", (400, 404)),
            ("output_absolute_path_denied", "/api/outputs/file?p=/__codex_http_probe_missing", (403,)),
            ("graph_key_traversal_denied", "/api/ont/review?graph=..%2F__codex_http_probe_missing", (400,)),
            ("preview_identifier_denied", "/api/conn/preview?src=demo&table=bad%22name", (400,)),
        ]:
            self.probe(name, "GET", path, statuses=statuses)
        self.probe("sparql_service_denied", "POST", "/api/sparql",
                   body={"graph": absent_source, "query": "SELECT ?s WHERE { SERVICE <http://127.0.0.1:1> { ?s ?p ?o } }"},
                   statuses=(400,), validate=lambda r: _guard_error(r, "禁用"))

    def bounded_load(self, concurrency, requests, latency_budget_ms):
        if not 1 <= concurrency <= 8 or not 1 <= requests <= 64:
            raise ValueError("load probe bounds: concurrency 1..8, requests 1..64")

        def one(index):
            started = time.monotonic()
            try:
                if index % 2:
                    response = self.client.request("POST", "/api/query", body={"sql": "SELECT 1 AS probe", "src": "demo"})
                    valid = response.status == 200 and _query_ok(response)
                else:
                    response = self.client.request("GET", "/api/health")
                    payload = response.json()
                    valid = response.status == 200 and isinstance(payload, dict) and payload.get("ok") is True
                return {"ok": valid, "status": response.status, "elapsed_ms": response.elapsed_ms,
                        "bytes": len(response.body)}
            except Exception as exc:
                return {"ok": False, "status": None, "failure": type(exc).__name__,
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            samples = list(pool.map(one, range(requests)))
        duration = time.monotonic() - started
        elapsed = sorted(x["elapsed_ms"] for x in samples)
        p95 = elapsed[math.ceil(len(elapsed) * 0.95) - 1]
        passed = all(x["ok"] for x in samples) and p95 <= latency_budget_ms
        summary = {"name": "bounded_concurrency", "ok": passed, "concurrency": concurrency, "requests": requests,
                   "successful_requests": sum(x["ok"] for x in samples), "duration_ms": round(duration * 1000, 3),
                   "requests_per_second": round(requests / duration, 2), "p50_ms": round(statistics.median(elapsed), 3),
                   "p95_ms": p95, "max_ms": max(elapsed), "latency_budget_ms": latency_budget_ms,
                   "samples": samples}
        self.results.append(summary)
        print(f"{'PASS' if passed else 'FAIL'} bounded_concurrency {requests} requests, p95={p95:.1f}ms", flush=True)
        self.probe("health_after_load", "GET", "/api/health",
                   validate=lambda r: isinstance(r.json(), dict) and r.json().get("ok") is True)
        self.probe("database_table_count_unchanged", "GET", "/api/db/check",
                   validate=lambda r: isinstance(r.json(), dict) and r.json().get("ok") is True
                   and r.json().get("tables") == self.baseline_tables)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--requests", type=int, choices=range(1, 65), default=32)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--latency-budget-ms", type=float, default=5000)
    args = parser.parse_args()
    if args.latency_budget_ms <= 0:
        parser.error("latency budget must be positive")
    client = Client(args.url, timeout=args.timeout)
    audit = Audit(client)
    started = time.time()
    audit.standard_checks()
    load = audit.bounded_load(args.concurrency, args.requests, args.latency_budget_ms)
    passed = sum(bool(result["ok"]) for result in audit.results)
    report = {"started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
              "url": client.base_url, "duration_seconds": round(time.time() - started, 3),
              "passed": passed, "failed": len(audit.results) - passed, "total": len(audit.results),
              "safety": {"mutating_endpoints_called": False, "llm_called": False, "external_sources_queried": False,
                         "response_bodies_recorded": False, "max_concurrency": args.concurrency,
                         "load_requests": args.requests, "max_response_bytes": MAX_BODY},
              "scope_limit": "有限 HTTP 回归与轻量并发；未测绝对容量、长期泄漏、CPU/RSS 或公网渗透。",
              "load": {k: v for k, v in load.items() if k != "samples"}, "checks": audit.results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "failed": report["failed"], "report": str(args.report)}, ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

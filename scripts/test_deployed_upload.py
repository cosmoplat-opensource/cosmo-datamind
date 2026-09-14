#!/usr/bin/env python3
"""Exercise CSV upload/failed refresh/cleanup using only a new acceptance filename."""
import argparse
import json
from pathlib import Path
import uuid

import requests  # type: ignore[import-untyped]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    session = requests.Session()
    session.trust_env = False
    table = "codex_acceptance_" + uuid.uuid4().hex[:10]
    filename = table + ".csv"
    checks = []

    def check(name, condition):
        checks.append({"name": name, "passed": bool(condition)})
        print(f"{'PASS' if condition else 'FAIL'} {name}", flush=True)

    def upload(content):
        result = session.post(args.url + "/api/build/upload", files={"files": (filename, content, "text/csv")}, timeout=30)
        result.raise_for_status()
        return result.json()

    def query():
        response = session.post(args.url + "/api/query", json={"src": "uploads", "sql": f'SELECT * FROM "{table}" ORDER BY id'}, timeout=15)
        return response.json()

    try:
        initial = upload(b"id,amount\n1,10\n2,20\n")
        check("new CSV materialized", any(t.get("table") == table and t.get("rows") == 2 for t in initial.get("tables", [])))
        before = query()
        check("uploaded rows queryable", before.get("rows") == [{"id": "1", "amount": "10"}, {"id": "2", "amount": "20"}])
        invalid = upload(b"id,id\n3,99\n")
        check("duplicate headers rejected", any(t.get("error") for t in invalid.get("tables", [])))
        check("failed refresh preserves original table", query() == before)
    finally:
        response = session.post(args.url + "/api/build/asset/delete", json={"name": filename}, timeout=15)
        result = response.json()
        check("own uploaded fixture removed", response.ok and result.get("ok"))
        check("own uploaded table removed", bool(query().get("error")))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"filename": filename, "checks": checks, "passed": all(x["passed"] for x in checks)}, ensure_ascii=False, indent=2) + "\n")
        session.close()
    return int(not all(x["passed"] for x in checks))


if __name__ == "__main__":
    raise SystemExit(main())

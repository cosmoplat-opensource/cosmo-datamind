"""Collect public registry identity metadata without opening filing assets.

Only GLEIF identity/relationship JSON endpoints are permitted. Corporate,
fund and successor relationships are retained as risks for a later audit;
missing links or reporting exceptions do not certify independence. Roster
roles, source report URLs and reserved report assets are never changed.
"""

import hashlib
import json
import re
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from native_source_metadata_audit import PLAN, SOURCE
from prepare import EXP, save, sha

DEST = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/source_qualification/registry_metadata_v1"
ROLES = {"additional_development", "calibration", "reserved_independent_test"}
RELATIONS = {"direct-parent", "ultimate-parent", "successor-entities", "associated-entity",
             "fund-manager", "umbrella-fund", "master-fund", "head-office"}
API_REFERENCE = "https://www.gleif.org/en/lei-data/gleif-api/"
API_RELATION_REFERENCE = "https://www.gleif.org/content/4_lei-data/1_access-and-use-lei-data/6_supporting-documents/GLEIF-API-Changes-Documentation.html"
RATE_LOCK = threading.Lock()
NEXT_REQUEST = 0.


def validate_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "api.gleif.org" or parsed.port is not None
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or not re.fullmatch(r"/api/v1/(?:lei-records|relationship-records)/[A-Za-z0-9/_-]+", parsed.path)):
        raise ValueError("Only official GLEIF identity/relationship metadata endpoints are allowed")
    return url


def discover_links(response):
    record = response.get("data")
    if not isinstance(record, dict):
        return [], []
    requests, unsupported = [], []
    for relation, value in record.get("relationships", {}).items():
        if relation not in RELATIONS:
            continue
        for link_type, url in value.get("links", {}).items():
            if not isinstance(url, str):
                unsupported.append({"relation": relation, "link_type": link_type, "reason": "nonstring_metadata_link"})
                continue
            try:
                validate_url(url)
            except ValueError:
                unsupported.append({"relation": relation, "link_type": link_type, "url": url,
                                    "reason": "outside_allowed_metadata_endpoint"})
                continue
            requests.append({"relation": relation, "link_type": link_type, "url": url})
    return requests, unsupported


def pace():
    global NEXT_REQUEST
    with RATE_LOCK:
        scheduled = max(time.monotonic(), NEXT_REQUEST)
        NEXT_REQUEST = scheduled + .5  # at most two requests/second
    delay = scheduled - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def fetch(url):
    validate_url(url)
    key = hashlib.sha256(url.encode()).hexdigest()
    metadata = DEST / "response_metadata" / (key + ".json")
    if metadata.exists():
        previous = json.loads(metadata.read_text())
        if previous["url"] != url:
            raise ValueError("Registry response URL/hash collision")
        if previous["complete"]:
            if sha(previous["body_file"]) != previous["body_sha256"]:
                raise ValueError("Frozen registry response changed")
            return previous
        attempts = previous["attempts"]
    else:
        attempts = []
    for _ in range(2):
        pace()
        begin = time.monotonic()
        try:
            result = subprocess.run(["curl", "-sS", "--connect-timeout", "10", "--max-time", "30",
                                     "--write-out", "\n%{http_code}", url], capture_output=True, timeout=35)
        except subprocess.TimeoutExpired as error:
            result = subprocess.CompletedProcess(["curl", url], 124, error.stdout or b"", b"Registry transport subprocess timed out")
        body, _, status = result.stdout.rpartition(b"\n")
        code = int(status) if status.isdigit() else 0
        body_file = DEST / "responses" / key / (str(len(attempts) + 1) + ".body")
        body_file.parent.mkdir(parents=True, exist_ok=True)
        body_file.write_bytes(body)
        try:
            decoded = json.loads(body)
            valid_json = isinstance(decoded, dict)
        except (ValueError, UnicodeDecodeError):
            valid_json = False
        attempt = {"requested_utc": datetime.now(timezone.utc).isoformat(), "http_status": code,
                   "curl_exit_code": result.returncode, "elapsed_seconds": time.monotonic() - begin,
                   "body_file": str(body_file), "body_sha256": sha(body_file), "json_object_response": valid_json,
                   "transport_error": result.stderr.decode(errors="replace")[:400]}
        attempts.append(attempt)
        complete = result.returncode == 0 and code in {200, 400, 404, 410, 422} and valid_json
        record = {"url": url, "complete": complete, "attempts": attempts, **attempt}
        save(metadata, record)
        if complete:
            return record
        time.sleep(1)
    return record


def fetch_group(urls, phase):
    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(fetch, url): url for url in sorted(set(urls))}
        for future in as_completed(jobs):
            url = jobs[future]
            results[url] = future.result()
            save(DEST / "progress.json", {"phase": phase, "updated_utc": datetime.now(timezone.utc).isoformat(),
                "responses_observed_in_phase": len(results), "expected_requests_in_phase": len(jobs),
                "http_200_in_phase": sum(r["http_status"] == 200 for r in results.values()),
                "report_assets_or_answers_opened": False, "independent_source_eligibility_certified": False})
            if len(results) % 20 == 0:
                print("Registry metadata", phase, len(results), "/", len(jobs), flush=True)
    return results


def collect():
    output = DEST / "collection.json"
    if output.exists():
        raise RuntimeError("Completed registry collection is frozen")
    roster = json.loads(SOURCE.read_text())
    plan = json.loads(PLAN.read_text())
    if not roster["metadata_only"] or sha(SOURCE) != plan["reservation_sha256"]:
        raise ValueError("Original source reservation changed")
    selected = [{"lei": e["lei"], "provided_name": e["name"], "tentative_role": e["tentative_role"]}
                for e in roster["entities"] if e["tentative_role"] in ROLES]
    selected.sort(key=lambda row: row["lei"])
    if len(selected) != 84 or len({s["lei"] for s in selected}) != 84:
        raise ValueError("Sixteen development, eight calibration and sixty reserved identifiers required")
    if any(not re.fullmatch(r"[A-Z0-9]{20}", row["lei"]) for row in selected):
        raise ValueError("Unexpected roster identifier format")
    design = {"metadata_reservation_sha256": sha(SOURCE), "development_plan_sha256": sha(PLAN),
              "selected_identifiers": selected, "request_policy": "two workers, at most two requests/second, two attempts/request",
              "allowed_endpoint_host": "api.gleif.org", "redirects_followed": False,
              "relation_keys": sorted(RELATIONS), "code_sha256": sha(__file__),
              "official_api_reference": API_REFERENCE, "official_relationship_reference": API_RELATION_REFERENCE,
              "all_source_roles_and_reports_remain_frozen": True}
    design_file = DEST / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Frozen registry collection code/protocol changed")
    save(design_file, design)
    (DEST / "collector_snapshot.py").write_bytes(Path(__file__).read_bytes())
    urls = {row["lei"]: "https://api.gleif.org/api/v1/lei-records/" + row["lei"] for row in selected}
    # One synchronous request checks transport before launching the complete
    # metadata-only queue. Failures never trigger filing downloads instead.
    first = fetch(urls[selected[0]["lei"]])
    if not first["complete"]:
        raise RuntimeError("Official registry transport preflight failed; partial responses preserved")
    base = fetch_group(urls.values(), "identity_records")
    links, unsupported, relation_urls = {}, {}, set()
    for row in selected:
        response = base[urls[row["lei"]]]
        if response["http_status"] != 200 or not response["json_object_response"]:
            links[row["lei"]] = []
            continue
        data = json.loads(Path(response["body_file"]).read_bytes())
        identity = data.get("data")
        if not isinstance(identity, dict):
            links[row["lei"]] = []
            unsupported[row["lei"]] = [{"reason": "HTTP_200_without_identity_record"}]
            continue
        if identity.get("id") != row["lei"]:
            raise ValueError("Official registry response identifier differs from request")
        requested, unknown = discover_links(data)
        links[row["lei"]], unsupported[row["lei"]] = requested, unknown
        relation_urls.update(r["url"] for r in requested)
    related = fetch_group(relation_urls, "parent_successor_and_fund_relationships")
    all_responses = {**base, **related}
    status_counts = dict(Counter(str(r["http_status"]) for r in all_responses.values()))
    save(output, {"collection_complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "design_sha256": sha(design_file), "selected_identifiers": selected,
        "identity_url_by_lei": urls, "discovered_related_metadata_requests": links,
        "unsupported_links_retained": unsupported, "responses": all_responses,
        "counts": {"roster_identifiers": len(selected), "unique_metadata_urls_attempted": len(all_responses),
                   "identity_http_200": sum(base[url]["http_status"] == 200 for url in urls.values()),
                   "unresolved_transport_or_response_requests": sum(not r["complete"] for r in all_responses.values()),
                   "http_status_counts": status_counts},
        "only_official_identity_and_relationship_metadata_requested": True,
        "parent_omission_or_reporting_exception_is_not_independence_certification": True,
        "current_registry_snapshot_does_not_certify_report_year_identity_or_parent_history": True,
        "no_roster_reassignment_or_quality_replacements": True,
        "calibration_or_reserved_report_images_instance_facts_or_answers_opened": False,
        "legal_group_template_or_original_publication_independence_certified": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Registry metadata collection complete", status_counts, flush=True)


if __name__ == "__main__":
    collect()

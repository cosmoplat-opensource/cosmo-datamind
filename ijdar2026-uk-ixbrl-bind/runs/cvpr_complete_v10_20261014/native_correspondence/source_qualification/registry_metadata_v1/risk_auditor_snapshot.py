"""Current registry alias/group risk audit; report-year independence is pending.

Only the frozen metadata collection is read. Equal names do not merge legal
entities; explicit current parent/successor/registration links form separate
overlap-risk components. Fund managers are reported as template risks, not
treated as ownership proof. Missing/exception data remain unknown.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from native_registry_metadata_v24 import DEST
from native_source_metadata_audit import name_key
from prepare import save, sha

PARENT_RISKS = {"direct-parent", "ultimate-parent", "successor-entities", "head-office", "umbrella-fund", "master-fund"}


def response_data(response):
    if response["http_status"] != 200 or not response["json_object_response"]:
        return None
    path = Path(response["body_file"])
    if sha(path) != response["body_sha256"]:
        raise ValueError("Frozen registry response body changed")
    return json.loads(path.read_bytes()).get("data")


def related_entity_ids(link, response):
    if link["link_type"] not in {"related", "lei-record"}:
        return []  # exceptions and relationship record IDs are not parent LEIs
    data = response_data(response)
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    result = []
    for row in rows:
        if row.get("type") != "lei-records":
            continue
        value = row.get("attributes", {}).get("lei") or row.get("id")
        if isinstance(value, str) and re.fullmatch(r"[A-Z0-9]{20}", value):
            result.append(value)
    return sorted(set(result))


def components(identifiers, edges):
    parent = {i: i for i in identifiers}

    def find(i):
        parent.setdefault(i, i)
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in edges:
        root_a, root_b = find(a), find(b)
        parent[max(root_a, root_b)] = min(root_a, root_b)
    grouped = defaultdict(list)
    for ident in identifiers:
        grouped[find(ident)].append(ident)
    return [sorted(group) for group in grouped.values()]


def audit():
    output = DEST / "current_registry_risk_audit.json"
    if output.exists():
        raise RuntimeError("Completed registry risk audit is frozen")
    source = DEST / "collection.json"
    collection = json.loads(source.read_text())
    if not collection["collection_complete"] or len(collection["selected_identifiers"]) != 84:
        raise ValueError("The complete original 84-identifier metadata collection is required")
    design_file = DEST / "design.json"
    if sha(design_file) != collection["design_sha256"]:
        raise ValueError("Frozen registry request design changed")
    selected = collection["selected_identifiers"]
    known = {e["lei"]: e for e in selected}
    aliases, registrations, fund_managers = defaultdict(set), defaultdict(list), defaultdict(set)
    rows, edges, counts, per_role = {}, [], Counter(), defaultdict(Counter)
    for source_row in selected:
        lei, role = source_row["lei"], source_row["tentative_role"]
        response = collection["responses"][collection["identity_url_by_lei"][lei]]
        data = response_data(response)
        local = Counter({"identifiers": 1})
        row = {"provided_name": source_row["provided_name"], "tentative_role": role,
               "current_identity_record_resolved": False, "current_related_entities": [],
               "reporting_exceptions": [], "unknown_relationship_requests": [],
               "report_year_legal_name_or_group_history_certified": False,
               "independent_source_eligibility_certified": False}
        if isinstance(data, dict) and data.get("id") == lei:
            attrs = data["attributes"]
            entity = attrs["entity"]
            current = entity.get("legalName", {}).get("name")
            names = [current] + [r.get("name") for r in entity.get("otherNames", []) + entity.get("transliteratedOtherNames", [])]
            keys = {name_key(n) for n in names if n}
            for key in keys:
                aliases[key].add(lei)
            row.update(current_identity_record_resolved=True, current_legal_name=current,
                       current_and_previous_registered_names=[n for n in names if n],
                       provided_name_matches_current_or_recorded_alias=name_key(source_row["provided_name"]) in keys,
                       entity_status=entity.get("status"), registration_status=attrs.get("registration", {}).get("status"),
                       current_record_last_update=attrs.get("registration", {}).get("lastUpdateDate"),
                       legal_entity_events=entity.get("eventGroups", []))
            local["current_identity_records_resolved"] += 1
            local["current_name_differs_from_provided_name"] += name_key(current) != name_key(source_row["provided_name"])
            local["provided_name_found_in_current_or_previous_registry_names"] += row["provided_name_matches_current_or_recorded_alias"]
            authority = (entity.get("registeredAt") or {}).get("id")
            registration = entity.get("registeredAs")
            if authority and authority != "RA999999" and registration and registration.strip().upper() not in {"N/A", "NA", "NONE"}:
                registrations[authority, registration.strip().upper()].append(lei)
            for successor in entity.get("successorEntities", []):
                ident = successor.get("lei")
                if isinstance(ident, str) and re.fullmatch(r"[A-Z0-9]{20}", ident):
                    edges.append((lei, ident))
                    row["current_related_entities"].append({"relation": "declared_successor_entity", "identifier": ident})
            for link in collection["discovered_related_metadata_requests"].get(lei, []):
                related = collection["responses"][link["url"]]
                if related["http_status"] != 200 or not related["json_object_response"]:
                    row["unknown_relationship_requests"].append({"relation": link["relation"], "url": link["url"],
                                                                 "http_status": related["http_status"]})
                    continue
                if link["link_type"] == "reporting-exception":
                    exception = response_data(related)
                    if isinstance(exception, dict):
                        row["reporting_exceptions"].append({"relation": link["relation"], "attributes": exception.get("attributes")})
                    continue
                ids = related_entity_ids(link, related)
                for ident in ids:
                    row["current_related_entities"].append({"relation": link["relation"], "identifier": ident})
                    if link["relation"] in PARENT_RISKS:
                        edges.append((lei, ident))
                    elif link["relation"] == "fund-manager":
                        fund_managers[ident].add(lei)
            local["identifiers_with_reporting_exceptions"] += bool(row["reporting_exceptions"])
            local["identifiers_with_unresolved_relationship_requests"] += bool(row["unknown_relationship_requests"])
        else:
            local["unresolved_current_identity_records"] += 1
        rows[lei] = row
        counts.update(local)
        per_role[role].update(local)
    registration_collisions = []
    for (authority, number), ids in registrations.items():
        if len(ids) > 1:
            registration_collisions.append({"authority": authority, "registered_as": number, "identifiers": ids})
            edges.extend((ids[0], i) for i in ids[1:])
    name_collisions = [{"normalized_registered_alias": name, "identifiers": sorted(ids)}
                       for name, ids in aliases.items() if len(ids) > 1]
    groups = []
    for ids in components(sorted(known), edges):
        if len(ids) <= 1:
            continue
        roles = sorted({known[i]["tentative_role"] for i in ids})
        groups.append({"identifiers": ids, "tentative_roles": roles,
                       "crosses_development_and_reserved_roles": "additional_development" in roles and "reserved_independent_test" in roles,
                       "current_snapshot_overlap_risk_not_report_year_group_certification": True})
    managers = [{"fund_manager_identifier": manager, "selected_fund_identifiers": sorted(ids),
                 "template_or_service_provider_overlap_risk_not_ownership_proof": True}
                for manager, ids in fund_managers.items() if len(ids) > 1]
    snapshot = DEST / "risk_auditor_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {"complete": True, "audited_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "current registry identity/alias/group risks only; no financial facts or filing assets",
        "counts": dict(counts), "counts_by_tentative_role": {k: dict(v) for k, v in per_role.items()},
        "entities": rows, "current_overlap_risk_components": groups,
        "exact_registered_alias_collisions_not_used_to_merge_entities": name_collisions,
        "exact_registration_identity_collisions": registration_collisions,
        "shared_fund_manager_template_risks_not_ownership_groups": managers,
        "metadata_collection_sha256": sha(source), "auditor_snapshot_sha256": sha(snapshot),
        "no_source_names_roles_or_selection_replaced_using_current_registry_names": True,
        "missing_parent_or_reporting_exception_not_interpreted_as_independence": True,
        "current_relationships_not_assumed_valid_for_report_years": True,
        "fin_tabnet_issuer_alias_and_native_template_checks_still_required": True,
        "actual_original_publication_and_version_history_still_required": True,
        "calibration_or_reserved_report_images_instance_facts_or_answers_opened": False,
        "independent_source_eligibility_certified": False, "human_runtime_confirmations": 0,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False})
    print("Current registry risk audit complete", dict(counts), "overlap-risk components", len(groups), flush=True)


if __name__ == "__main__":
    audit()

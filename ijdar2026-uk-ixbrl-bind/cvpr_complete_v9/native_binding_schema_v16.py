"""Shared public entity/dimension hypotheses from source-local declarations.

This is development candidate preparation after the v12/v15 field scores.
No facts, context declarations, teacher or scorer files are opened. A report
LEI is common source metadata, not a learned visual identity. Declaration
roles do not establish dimension-axis/member edges or visible ownership.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import re

from lxml import etree
import numpy as np
import torch

from native_binding_prepare_v14 import DEST as BINDING
from native_field_retrieval_v12 import DEST as FIELD, field_text, report_card
from native_schema_catalog_v10 import DEST as SCHEMAS
from native_schema_labels_v2 import DEST as LABELS
from pilot_features import TEXT_MODEL, encode_texts
from prepare import opaque, save, sha


DEST = BINDING.parent / "binding_candidates_v16_corrected_members_v2"
XS = "{http://www.w3.org/2001/XMLSchema}"
XI = "http://www.xbrl.org/2003/instance"
DT = "http://xbrl.org/2005/xbrldt"
LEI_SCHEME = "http://standards.iso.org/iso/17442"
AXES, MEMBERS, SINGLES, PAIRS = 8, 16, 64, 63
NUMERIC_TYPES = {
    "decimalItemType", "floatItemType", "doubleItemType", "integerItemType",
    "nonPositiveIntegerItemType", "negativeIntegerItemType", "longItemType",
    "intItemType", "shortItemType", "byteItemType", "nonNegativeIntegerItemType",
    "unsignedLongItemType", "unsignedIntItemType", "unsignedShortItemType",
    "unsignedByteItemType", "positiveIntegerItemType", "monetaryItemType",
    "sharesItemType", "pureItemType", "fractionItemType",
}


def qname(value, node):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Empty declaration QName")
    pieces = value.strip().split(":")
    if len(pieces) > 2 or any(not re.fullmatch(r"[A-Za-z_][\w.-]*", p) for p in pieces):
        raise ValueError("Malformed declaration QName")
    namespace = node.nsmap.get(pieces[0] if len(pieces) == 2 else None)
    if not namespace:
        raise ValueError("Unresolved declaration QName")
    return "{" + namespace + "}" + pieces[-1]


def type_role(type_name, substitution_group):
    if substitution_group == "{" + DT + "}dimensionItem":
        return "dimension_axis"
    if substitution_group == "{" + DT + "}hypercubeItem":
        return "hypercube"
    if not type_name or not type_name.startswith("{") or "}" not in type_name:
        return "unknown"
    namespace, name = type_name[1:].split("}", 1)
    if namespace == XI and name in NUMERIC_TYPES:
        return "numeric_item"
    if namespace.startswith("http://www.xbrl.org/dtr/type/"):
        if name == "domainItemType":
            return "domain_or_member"
        if "/numeric" in namespace:
            return "numeric_item"
        if "/nonNumeric" in namespace or name == "textBlockItemType":
            return "nonnumeric_item"
    if namespace == XI and name in {"stringItemType", "booleanItemType", "dateItemType", "dateTimeItemType", "anyURIItemType"}:
        return "nonnumeric_item"
    return "unknown"


def declaration_roles(body):
    tree = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    if tree.getroottree().docinfo.doctype or tree.tag != XS + "schema":
        raise ValueError("Expected safe public XML schema")
    namespace = tree.get("targetNamespace")
    if not namespace:
        raise ValueError("Missing public schema namespace")
    result = []
    for element in tree.findall(XS + "element"):
        if not element.get("name"):
            continue
        kind = qname(element.get("type"), element)
        substitution = qname(element.get("substitutionGroup"), element)
        result.append({
            "field": "{" + namespace + "}" + element.get("name"),
            "namespace": namespace, "name": element.get("name"),
            "type": kind, "substitution_group": substitution,
            "declaration_role": type_role(kind, substitution),
            "abstract": element.get("abstract", "false") in ("true", "1"),
            "typed_domain_ref": element.get("{" + DT + "}typedDomainRef"),
            "schema_id": element.get("id"),
            "axis_member_membership_certified": False,
        })
    return result


def valid_lei(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z0-9]{18}\d{2}", value):
        return False
    digits = "".join(c if c.isdigit() else str(ord(c)-55) for c in value)
    return int(digits) % 97 == 1


def dimension_declaration_candidate(row):
    # Abstract is about instantiating an item as a fact, not whether its QName
    # can be used as an explicit dimension value. Keep domains/members even
    # when abstract=true; membership edges remain unknown for every control.
    return row["declaration_role"] in {"dimension_axis", "domain_or_member"}


def entity_candidates(task):
    observation = task.get("common_visual_observation") or {}
    text = observation.get("entity_text", "unknown")
    identifiers = {s: "visible_text_lei" for s in re.findall(r"(?<![A-Z0-9])[A-Z0-9]{20}(?![A-Z0-9])", text) if valid_lei(s)}
    company = task["company"]
    if valid_lei(company):
        identifiers.setdefault(company, "common_report_index_lei")
    return [{
        "candidate_id": opaque(["entity-v16", ident]),
        "entity": {"scheme": LEI_SCHEME, "identifier": ident},
        "basis": basis, "entity_visible_text": text,
        "visible_text_to_entity_mapping_certified": False,
        "common_metadata_assistance_not_visual_identity_learning": basis == "common_report_index_lei",
    } for ident, basis in sorted(identifiers.items())]


def dimension_queries(task):
    observation = task.get("common_visual_observation") or {}
    meaning = " | ".join(str(observation.get(k, "unknown")) for k in ("dimension_text", "version_text", "entity_text"))
    context = " | ".join((observation.get("section_text", "unknown"), observation.get("row_text", "unknown"), task["public_row_text"]))
    return meaning[:1500], context[:2500]


def dimension_hypotheses(axes, members):
    singles = []
    for axis in axes:
        if axis.get("typed_domain_ref"):
            continue  # A typed value cannot be invented from a member name.
        for member in members:
            if axis["field"] == member["field"]:
                continue
            score = (axis["retrieval_score"] + member["retrieval_score"]) / 2
            singles.append({"dimensions": [{"axis": axis["field"], "kind": "explicit", "member": member["field"]}],
                            "retrieval_score": score})
    singles.sort(key=lambda row: (-row["retrieval_score"], json.dumps(row["dimensions"], sort_keys=True)))
    pair_map = {}
    # Equal fixed beam for every method; different axes only, no duplicate axes.
    for first, second in combinations(singles[:SINGLES], 2):
        if first["dimensions"][0]["axis"] == second["dimensions"][0]["axis"]:
            continue
        dims = sorted(first["dimensions"] + second["dimensions"], key=lambda d: d["axis"])
        key = json.dumps(dims, sort_keys=True)
        pair_map[key] = {"dimensions": dims, "retrieval_score": (first["retrieval_score"] + second["retrieval_score"]) / 2}
    pairs = sorted(pair_map.values(), key=lambda row: (-row["retrieval_score"], json.dumps(row["dimensions"], sort_keys=True)))[:PAIRS]
    hypotheses = [{"dimensions": [], "retrieval_score": 0.0, "absence_not_certified": True}] + singles[:SINGLES] + pairs
    return [{**row, "candidate_id": opaque(["dimension-v16", row["dimensions"]]),
             "axis_member_membership_certified": False, "visible_dimension_binding_certified": False}
            for row in hypotheses]


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Shared binding candidate inventory already frozen")
    torch.set_num_threads(2)
    schema_file = SCHEMAS / "manifest.json"
    labels_file = LABELS / "field_catalog.json"
    public_file = FIELD / "public/tasks.json"
    component_file = BINDING / "public_component_candidates/tasks.json"
    schema_meta = json.loads(schema_file.read_text())
    label_meta = json.loads((LABELS / "manifest.json").read_text())
    field_meta = json.loads((FIELD / "manifest.json").read_text())
    component_meta = json.loads((BINDING / "public_component_candidates/manifest.json").read_text())
    if not all(m["complete"] for m in (schema_meta, label_meta, field_meta, component_meta)):
        raise ValueError("Complete shared public input inventories required")
    if sha(labels_file) != label_meta["catalog_sha256"] or sha(public_file) != field_meta["public_target_sha256"] or sha(component_file) != component_meta["candidate_inventory_sha256"]:
        raise ValueError("Frozen public input checksum differs")
    if component_meta["public_input_sha256"] != sha(public_file):
        raise ValueError("Component hypotheses do not share the field retrieval input")
    tasks = json.loads(public_file.read_text())
    components = json.loads(component_file.read_text())
    if len(tasks) != 840 or [t["task_id"] for t in tasks] != [t["task_id"] for t in components]:
        raise ValueError("Complete ordered public target inventory required")
    roles, asset_hashes = defaultdict(list), {}
    for asset in schema_meta["public_assets"]:
        path = Path(asset["path"])
        if path.suffix.lower() != ".xsd":
            continue
        if sha(path) != asset["sha256"]:
            raise ValueError("Source public schema asset changed")
        report = asset.get("report", "public_standard")
        asset_hashes[str(path)] = asset["sha256"]
        for row in declaration_roles(path.read_bytes()):
            roles[(report, row["field"])].append(row)
    fields = json.loads(labels_file.read_text())
    namespaces_file = LABELS / "report_namespaces.json"
    if sha(namespaces_file) != label_meta["report_namespaces_sha256"]:
        raise ValueError("Public source namespace list changed")
    namespaces = json.loads(namespaces_file.read_text())
    pools, role_rows = {}, []
    for report in sorted({t["report"] for t in tasks}):
        cards = []
        for field in fields:
            if field["namespace"] not in namespaces[report] or (card := report_card(field, report)) is None:
                continue
            name = "{" + card["namespace"] + "}" + card["name"]
            options = roles.get((report, name), []) + roles.get(("public_standard", name), [])
            if not options:
                continue
            role_rows.append({"report": report, "field": name, "declaration_variants": options})
            # Preserve conflicts as unknown, never choose a favorable declaration.
            serialized = {json.dumps(row, sort_keys=True) for row in options}
            if len(serialized) != 1:
                continue
            role = options[0]
            if dimension_declaration_candidate(role):
                cards.append({**card, **role})
        pools[report] = cards
    embedding_meta_file = FIELD / "shared_embeddings/catalog_metadata.json"
    embedding_file = FIELD / "shared_embeddings/catalog.npz"
    embedding_meta = json.loads(embedding_meta_file.read_text())
    if sha(embedding_file) != embedding_meta["cache_sha256"] or sha(labels_file) != embedding_meta["catalog_sha256"]:
        raise ValueError("Source-local shared declaration embeddings changed")
    if any(sha(TEXT_MODEL / name) != digest for name, digest in embedding_meta["text_model_files"].items()):
        raise ValueError("Shared semantic encoder changed since declaration embedding freeze")
    with np.load(embedding_file) as archive:
        vectors = dict(zip(embedding_meta["strings"], archive["features"], strict=True))
    queries = {t["task_id"]: dimension_queries(t) for t in tasks}
    query_strings = sorted({s for pair in queries.values() for s in pair})
    query_vectors = encode_texts(query_strings)
    records, counts = [], Counter({"targets": len(tasks), "reports": len(pools)})
    for task, component in zip(tasks, components, strict=True):
        cards = pools[task["report"]]
        ranked = {"dimension_axis": [], "domain_or_member": []}
        meaning, context = queries[task["task_id"]]
        for card in cards:
            vector = vectors[field_text(card)]
            score = float(.8*(vector @ query_vectors[meaning]) + .2*(vector @ query_vectors[context]))
            ranked[card["declaration_role"]].append({**card, "retrieval_score": score})
        for bucket in ranked.values():
            bucket.sort(key=lambda c: (-c["retrieval_score"], c["field"]))
        axes = ranked["dimension_axis"][:AXES]
        members = ranked["domain_or_member"][:MEMBERS]
        entities = entity_candidates(task)
        dims = dimension_hypotheses(axes, members)
        records.append({**component, "entity_candidates": entities,
                        "dimension_axis_candidates": axes, "dimension_member_candidates": members,
                        "dimension_tuple_candidates": dims,
                        "canonical_entity_dimension_candidate_preparation_complete": True,
                        "canonical_entity_and_dimension_resolution_complete": False,
                        "dimension_membership_networks_not_loaded": True,
                        "automatic_teacher_or_future_reference_used_for_candidate_generation": False})
        counts["targets_with_common_metadata_entity_candidate"] += any(e["common_metadata_assistance_not_visual_identity_learning"] for e in entities)
        counts["targets_with_visible_lei_candidate"] += any(e["basis"] == "visible_text_lei" for e in entities)
        counts["targets_with_axis_candidate"] += bool(axes)
        counts["targets_with_member_candidate"] += bool(members)
        counts["targets_with_nonempty_dimension_tuple_hypothesis"] += any(d["dimensions"] for d in dims)
        counts["typed_axis_candidates_preserved_without_inventing_value"] += sum(bool(a["typed_domain_ref"]) for a in axes)
        counts["failed_visual_observations_retained"] += task["common_observation_failed"]
    snapshot = DEST / "candidate_builder_snapshot.py"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "public/tasks.json", records)
    save(DEST / "public/declaration_roles.json", role_rows)
    save(DEST / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "stage": "shared_entity_dimension_candidate_preparation_not_model_evaluation",
        "version": "v16_corrected_members_v2",
        "supersedes_ineligible_initial_preparation": "binding_candidates_v16",
        "abstract_domain_member_declarations_not_excluded": True,
        "input_sha256": {str(p): sha(p) for p in (schema_file, labels_file, namespaces_file, public_file, component_file, embedding_meta_file, embedding_file)},
        "public_schema_assets_sha256": asset_hashes,
        "candidate_inventory_sha256": sha(DEST / "public/tasks.json"),
        "declaration_roles_sha256": sha(DEST / "public/declaration_roles.json"),
        "builder_snapshot_sha256": sha(snapshot),
        "query_text_encoder_files": {p.name: sha(p) for p in TEXT_MODEL.iterdir() if p.is_file()},
        "candidate_caps": {"axes": AXES, "members": MEMBERS, "single_dimension_tuples": SINGLES, "two_dimension_tuples": PAIRS},
        "report_local_aliases_only_no_future_alias_pooling_into_historical_tasks": True,
        "all_controls_receive_same_entities_dimensions_unit_period_and_quantity_candidates": True,
        "common_report_lei_assistance_explicit_not_claimed_as_visual_entity_learning": True,
        "declaration_roles_are_not_axis_member_edges_or_header_ownership_supervision": True,
        "source_year_used_to_supply_period_or_boundaries": False,
        "any_teacher_fact_instance_or_scoring_reference_file_read": False,
        "dimension_membership_networks_not_loaded": True,
        "human_runtime_confirmations": 0,
        "learned_full_binding_method_or_independent_test_complete": False,
    })
    print("Frozen shared entity/dimension hypotheses", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

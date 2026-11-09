"""Publisher definition-linkbase retrieval hints, never instance/context tags.

Checksum-verified report packages are opened only for public definition XML.
Raw declaration paths improve the shared candidate pool; this is not a full
DTS validator. Unresolved locators/roles/prohibitions remain recorded, and
these paths never certify a header's ownership of a visible numerical cell.
"""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import unquote, urlparse
import zipfile

from lxml import etree
import requests

from native_binding_schema_v16 import declaration_roles
from native_schema_catalog_v10 import DEST as SCHEMAS, MAX_PACKAGE_BYTES, fetch
from native_schema_labels_v2 import DEST as LABELS
from native_binding_prepare_v14 import DEST as BINDING
from prepare import opaque, save, sha


DEST = BINDING.parent / "public_dimension_networks_v17"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"
DT = "{http://xbrl.org/2005/xbrldt}"
ARC = "http://xbrl.org/int/dim/arcrole/"


def definition_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError("Unsafe report package member path")
    return path.suffix.lower() == ".xml" and bool(re.search(r"(?:^|[_-])(?:def|definition)(?:[_-]|\.)", path.name, re.I))


def package_assets(package):
    checkpoint = DEST / "packages" / (package["report"] + ".json")
    if checkpoint.exists():
        record = json.loads(checkpoint.read_text())
        if record["package_sha256"] != package["package_sha256"] or any(sha(a["path"]) != a["sha256"] for a in record["assets"]):
            raise ValueError("Frozen public definition source changed")
        return record
    with requests.Session() as session:
        body = fetch(session, package["package_url"], MAX_PACKAGE_BYTES)
    if sha256(body).hexdigest() != package["package_sha256"]:
        raise ValueError("Complete publisher package checksum differs")
    assets = []
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for entry in archive.infolist():
            if entry.is_dir() or not definition_member(entry.filename):
                continue
            if entry.file_size > 8 * 1024**2:
                raise ValueError("Oversized public definition linkbase")
            content = archive.read(entry)  # ZIP CRC verified; no instance member is opened.
            path = DEST / "assets" / package["report"] / PurePosixPath(entry.filename)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            assets.append({"report": package["report"], "source_member": entry.filename,
                           "path": str(path), "sha256": sha(path)})
    record = {"report": package["report"], "package_sha256": package["package_sha256"],
              "package_url": package["package_url"], "package_bytes": len(body), "assets": assets,
              "instance_or_report_members_read": False}
    save(checkpoint, record)
    return record


def resolve_locator(href, index):
    parsed = urlparse(href or "")
    if parsed.scheme not in {"", "http", "https"} or not parsed.fragment:
        return None
    key = (PurePosixPath(unquote(parsed.path)).name, unquote(parsed.fragment))
    choices = index.get(key, set())
    return next(iter(choices)) if len(choices) == 1 else None


def definition_arcs(body, index, source):
    root = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    if root.getroottree().docinfo.doctype or root.tag != LINK + "linkbase":
        raise ValueError("Expected safe public definition linkbase")
    arcs, unresolved = [], []
    for link in root.findall(LINK + "definitionLink"):
        role = link.get(XLINK + "role", "http://www.xbrl.org/2003/role/link")
        locators = {}
        for locator in link.findall(LINK + "loc"):
            label = locator.get(XLINK + "label")
            value = resolve_locator(locator.get(XLINK + "href"), index)
            if label in locators and locators[label] != value:
                raise ValueError("Ambiguous public locator label")
            locators[label] = value
        for arc in link.findall(LINK + "definitionArc"):
            row = {"role": role, "arcrole": arc.get(XLINK + "arcrole"),
                   "source": locators.get(arc.get(XLINK + "from")),
                   "target": locators.get(arc.get(XLINK + "to")),
                   "target_role": arc.get(DT + "targetRole", role),
                   "usable": arc.get(DT + "usable", "true") not in ("false", "0"),
                   "use": arc.get("use", "optional"), "priority": arc.get("priority", "0"),
                   "closed": arc.get(DT + "closed"), "context_element": arc.get(DT + "contextElement"),
                   "source_asset": source}
            row["arc_id"] = opaque(["definition-arc-v17", row])
            if not row["source"] or not row["target"]:
                unresolved.append(row)
            else:
                arcs.append(row)
    return arcs, unresolved


def relationship_hints(arcs):
    # Conservative prohibitions for candidate retrieval. Full DTS precedence,
    # equivalence, defaults and hypercube validation remain explicitly uncertified.
    grouped = defaultdict(list)
    for arc in arcs:
        grouped[(arc["role"], arc["arcrole"], arc["source"], arc["target"])].append(arc)
    active = []
    for group in grouped.values():
        try:
            highest = max(int(a["priority"]) for a in group)
        except ValueError:
            continue
        winners = [a for a in group if int(a["priority"]) == highest]
        if not any(a["use"] == "prohibited" for a in winners):
            active.extend(a for a in winners if a["use"] == "optional")
    outgoing = defaultdict(list)
    for arc in active:
        outgoing[(arc["role"], arc["source"], arc["arcrole"])].append(arc)
    defaults = {(a["source"], a["target"]) for a in active if a["arcrole"] == ARC + "dimension-default"}
    pairs = {}
    def walk(role, node, path):
        queue, seen, result = [(role, node, path)], set(), []
        while queue:
            current_role, current, trail = queue.pop()
            if (current_role, current) in seen:
                continue
            seen.add((current_role, current))
            for arc in outgoing.get((current_role, current, ARC + "domain-member"), []):
                next_path = trail + [arc["arc_id"]]
                result.append((arc["target"], arc["usable"], next_path))
                if len(next_path) < 64:
                    queue.append((arc["target_role"], arc["target"], next_path))
        return result
    for arc in active:
        if arc["arcrole"] != ARC + "dimension-domain":
            continue
        candidates = [(arc["target"], arc["usable"], [arc["arc_id"]])]
        candidates += walk(arc["target_role"], arc["target"], [arc["arc_id"]])
        for member, usable, path in candidates:
            if not usable or (arc["source"], member) in defaults:
                continue
            key = (arc["source"], member)
            pairs.setdefault(key, {"axis": key[0], "member": key[1], "source_path_arc_ids": path,
                                   "public_relationship_hint_not_full_dts_membership_certification": True})
    field_axes = defaultdict(set)
    for arc in active:
        if arc["arcrole"] != ARC + "all":
            continue
        axes = [a["target"] for a in outgoing.get((arc["target_role"], arc["target"], ARC + "hypercube-dimension"), [])]
        items = [arc["source"]] + [node for node, _, _ in walk(arc["role"], arc["source"], [])]
        for item in items:
            field_axes[item].update(axes)
    return {"axis_member_hints": sorted(pairs.values(), key=lambda p: (p["axis"], p["member"])),
            "field_axis_hints": {f: sorted(axes) for f, axes in sorted(field_axes.items())},
            "dimension_defaults_not_proposed_as_explicit_members": sorted([list(v) for v in defaults]),
            "full_dts_effective_base_sets_and_hypercube_validity_certified": False}


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Public dimension relationship inventory already frozen")
    source_file = SCHEMAS / "manifest.json"
    source = json.loads(source_file.read_text())
    namespaces_file = LABELS / "report_namespaces.json"
    label_manifest = json.loads((LABELS / "manifest.json").read_text())
    if not source["complete"] or sha(namespaces_file) != label_manifest["report_namespaces_sha256"]:
        raise ValueError("Complete source-matched public schemas required")
    namespaces = json.loads(namespaces_file.read_text())
    indexes = {r: defaultdict(set) for r in namespaces}
    for asset in source["public_assets"]:
        path = Path(asset["path"])
        if path.suffix.lower() != ".xsd":
            continue
        if sha(path) != asset["sha256"]:
            raise ValueError("Public schema checksum changed")
        rows = declaration_roles(path.read_bytes())
        base = PurePosixPath(urlparse(asset["url"]).path).name if asset.get("standard_schema") else PurePosixPath(asset["source_member"]).name
        for report, allowed in namespaces.items():
            if asset.get("report", report) != report:
                continue
            for row in rows:
                if row["namespace"] in allowed and row["schema_id"]:
                    indexes[report][(base, row["schema_id"])].add(row["field"])
    records, failures = [], []
    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = {workers.submit(package_assets, p): p["report"] for p in source["packages"]}
        for future in as_completed(futures):
            try:
                records.append(future.result())
            except (requests.RequestException, ValueError, zipfile.BadZipFile, OSError) as exc:
                failures.append({"report": futures[future], "error_type": type(exc).__name__, "error": str(exc)[:300]})
            save(DEST / "progress.json", {"processed_packages": len(records)+len(failures), "expected_packages": len(futures),
                                          "failures": failures, "updated_utc": datetime.now(timezone.utc).isoformat()})
            print("Public definition packages", len(records)+len(failures), "/", len(futures), "failed", len(failures), flush=True)
    if failures or len(records) != 42:
        raise RuntimeError("Incomplete public definition packages; resume from verified per-package checkpoints")
    graphs, counts = {}, defaultdict(int)
    for package in sorted(records, key=lambda p: p["report"]):
        arcs, unresolved = [], []
        for asset in package["assets"]:
            rows, missing = definition_arcs(Path(asset["path"]).read_bytes(), indexes[package["report"]], asset["sha256"])
            arcs.extend(rows)
            unresolved.extend(missing)
        graphs[package["report"]] = {**relationship_hints(arcs), "raw_resolved_arcs": arcs, "unresolved_arcs_retained": unresolved}
        counts["resolved_public_arcs"] += len(arcs)
        counts["unresolved_public_arcs"] += len(unresolved)
        counts["axis_member_retrieval_hints"] += len(graphs[package["report"]]["axis_member_hints"])
    save(DEST / "public/report_graphs.json", graphs)
    snapshot = DEST / "network_preparer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "packages": records,
        "counts": dict(counts), "reports": len(graphs), "source_schema_manifest_sha256": sha(source_file),
        "source_namespaces_sha256": sha(namespaces_file), "public_graphs_sha256": sha(DEST / "public/report_graphs.json"),
        "runner_snapshot_sha256": sha(snapshot), "all_controls_receive_same_public_definition_hints": True,
        "no_fact_instance_context_teacher_or_scoring_files_read": True,
        "raw_graph_is_retrieval_assistance_not_full_dts_or_visual_header_ownership_certificate": True,
        "source_package_cryptographic_hashes_checked_before_linkbase_reads": True,
        "references": ["https://www.xbrl.org/guidance/technical-dimensions-faq/"],
    })
    print("Public dimension retrieval graphs prepared", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

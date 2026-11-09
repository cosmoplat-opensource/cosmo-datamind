"""Held-out companies, step D: public schema catalog, report namespaces and dimension graphs.

Approved download (2026-10-20): the six ESEF report packages of the two held-out companies from
filings.xbrl.org, checksum-verified against the frozen local source index. Only declaration
schemas (.xsd), label linkbases and definition linkbases are read from the packages; no
instance/report member is opened. Standard IFRS schemas and labels are the locally cached
copies already used for the development companies. Output mirrors the development stages
(public_schema_report_labels_v2 catalog format, public_dimension_networks_v17 graph format)
under heldout_v1/schema/.
"""

import os
import io
import json
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import numpy as np
import requests
from native_binding_schema_v16 import declaration_roles
from native_dimension_network_v17 import definition_arcs, definition_member, relationship_hints
from native_field_retrieval_v12 import field_text, report_card
from native_schema_catalog_v10 import MAX_PACKAGE_BYTES, declaration_rows, english_labels, fetch, selected_package_asset
from pilot_features import encode_texts
from prepare import save, sha

EXP = Path(__file__).resolve().parents[1]
INDEX = EXP / "ext/xbrl_scope_source_index"
STANDARD = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/public_schema_extension_v1"
LABELS_V2 = EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/public_schema_report_labels_v2"
DEST = Path(os.environ.get("HELDOUT_ROOT", str(EXP / "runs/cvpr_complete_v10_20261014/native_correspondence/heldout_v1"))) / "schema"
SOURCES = Path(os.environ.get("HELDOUT_SOURCES", str(EXP / "ext/xbrl_scope_additional_development")))
FILINGS = {k: tuple(v) for k, v in json.loads((DEST.parent / "selection.json").read_text())["filings"].items()} if (DEST.parent / "selection.json").exists() else {"213800F59868OZQU6E56": ("2023-12-31", "2024-12-31", "2025-12-31"), "213800PFUQT4HHFYZJ63": ("2023-06-30", "2024-06-30", "2025-06-30")}


def index_records():
    wanted = {lei + "-" + d: (lei, d) for lei, dates in FILINGS.items() for d in dates}
    found = {}
    for f in sorted(INDEX.glob("*.json")):
        for item in json.loads(f.read_text()).get("data", []):
            a = item.get("attributes", {})
            for key, (lei, d) in wanted.items():
                if a.get("fxo_id", "").startswith(key + "-ESEF") and key not in found:
                    found[key] = {"report": lei + "_" + d[:4], "package_url": urljoin("https://filings.xbrl.org", a["package_url"]), "sha256": a["sha256"]}
    missing = set(wanted) - set(found)
    if missing:
        raise ValueError("Index records missing for " + str(sorted(missing)))
    return found


def main():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("heldout schema already frozen")
    DEST.mkdir(parents=True, exist_ok=True)
    records = index_records()
    standard_manifest = json.loads((STANDARD / "manifest.json").read_text())
    standard_assets = [a for a in standard_manifest["public_assets"] if a.get("standard_schema") or a.get("standard_label_linkbase")]
    packages = []
    with requests.Session() as session:
        for key, rec in sorted(records.items()):
            checkpoint = DEST / "packages" / (rec["report"] + ".json")
            if checkpoint.exists():
                packages.append(json.loads(checkpoint.read_text())); continue
            body = fetch(session, rec["package_url"], MAX_PACKAGE_BYTES)
            if sha256(body).hexdigest() != rec["sha256"]:
                raise ValueError("Package checksum differs from the frozen index for " + rec["report"])
            assets = []
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                for entry in archive.infolist():
                    if entry.is_dir() or not (selected_package_asset(entry.filename) or definition_member(entry.filename)):
                        continue
                    if entry.file_size > 8 * 1024 ** 2:
                        raise ValueError("Oversized package member")
                    content = archive.read(entry)
                    file = DEST / "assets" / rec["report"] / PurePosixPath(entry.filename)
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(content)
                    assets.append({"path": str(file), "sha256": sha(file), "source_member": entry.filename, "report": rec["report"],
                                   "kind": "definition" if definition_member(entry.filename) else ("schema" if file.suffix.lower() == ".xsd" else "label")})
            done = {"report": rec["report"], "package_url": rec["package_url"], "package_sha256": sha256(body).hexdigest(), "package_bytes": len(body),
                    "assets": assets, "fact_or_report_instance_members_parsed": False}
            save(checkpoint, done); packages.append(done)
            print("package", rec["report"], len(body) // 1024, "KB", len(assets), "assets", flush=True)
    # declared standard namespaces from the report headers
    declared = {}
    for lei, dates in FILINGS.items():
        for d in dates:
            report = lei + "_" + d[:4]
            opening = (SOURCES / (lei + "_" + d) / "original.xhtml").read_bytes()[:32768].decode("utf-8", errors="ignore")
            declared[report] = sorted(set(re.findall(r'xmlns:[\w-]+=["\'](https?://xbrl\.ifrs\.org/taxonomy/\d{4}-\d{2}-\d{2}/ifrs-full)["\']', opening)))
    # catalog: start from the development report-labels catalog for the standard namespaces in use
    base = json.loads((LABELS_V2 / "field_catalog.json").read_text())
    standard_ns = {ns for v in declared.values() for ns in v}
    catalog = {(c["namespace"], c["name"]): c for c in base if c["namespace"] in standard_ns}
    report_namespaces = {}
    id_to_field = {}  # (report, schema_id) -> "{ns}name" for extension + standard declarations
    standard_ids = {}
    for asset in standard_assets:
        if asset.get("standard_schema"):
            for row in declaration_rows(Path(asset["path"]).read_bytes())[0]:
                if row["schema_id"]:
                    standard_ids[row["schema_id"]] = "{" + row["namespace"] + "}" + row["name"]
    for package in packages:
        report = package["report"]
        ns_here = set()
        for asset in package["assets"]:
            if asset["kind"] != "schema":
                continue
            for row in declaration_rows(Path(asset["path"]).read_bytes())[0]:
                ns_here.add(row["namespace"])
                ident = (row["namespace"], row["name"])
                entry = catalog.setdefault(ident, {"namespace": ident[0], "name": ident[1], "declaration_variants": [],
                                                   "field_id": "s" + sha256(json.dumps(list(ident)).encode()).hexdigest()[:18], "labels": [], "labels_by_report": {}})
                existing = next((v for v in entry["declaration_variants"] if v["declaration"] == row), None)
                if existing is None:
                    entry["declaration_variants"].append({"declaration": row, "reports": [report]})
                elif report not in existing["reports"]:
                    existing["reports"].append(report)
                if row["schema_id"]:
                    id_to_field[(report, row["schema_id"])] = "{" + row["namespace"] + "}" + row["name"]
        for asset in package["assets"]:
            if asset["kind"] != "label":
                continue
            for ident_id, values in english_labels(Path(asset["path"]).read_bytes()).items():
                target = id_to_field.get((report, ident_id)) or standard_ids.get(ident_id)
                if not target:
                    continue
                ns, name = target[1:].split("}", 1)
                entry = catalog.get((ns, name))
                if entry is None:
                    continue
                entry.setdefault("labels_by_report", {}).setdefault(report, [])
                for v in values:
                    if v not in entry["labels_by_report"][report]:
                        entry["labels_by_report"][report].append(v)
        report_namespaces[report] = sorted(ns_here | set(declared[report]))
    rows = []
    for ident, entry in sorted(catalog.items()):
        variants = entry["declaration_variants"]
        for key in ("type", "balance", "period_type", "abstract"):
            values = {v["declaration"][key] for v in variants}
            entry[key] = next(iter(values)) if len(values) == 1 else None
        entry["source_specific_declarations_required"] = len(variants) > 1
        entry["labels_by_report"] = {r: sorted(v) for r, v in sorted(entry.get("labels_by_report", {}).items())}
        entry["labels"] = entry["labels_by_report"].get("public_standard", entry.get("labels", []))
        rows.append(entry)
    save(DEST / "field_catalog.json", rows)
    save(DEST / "report_namespaces.json", report_namespaces)
    # dimension graphs
    graphs, counts = {}, defaultdict(int)
    for package in packages:
        report = package["report"]
        index = defaultdict(set)
        for asset in package["assets"]:
            if asset["kind"] == "schema":
                for row in declaration_roles(Path(asset["path"]).read_bytes()):
                    if row["namespace"] in report_namespaces[report] and row["schema_id"]:
                        index[(PurePosixPath(asset["source_member"]).name, row["schema_id"])].add(row["field"])
        for asset in standard_assets:
            if asset.get("standard_schema"):
                for row in declaration_roles(Path(asset["path"]).read_bytes()):
                    if row["namespace"] in report_namespaces[report] and row["schema_id"]:
                        index[(PurePosixPath(urlparse(asset["url"]).path).name, row["schema_id"])].add(row["field"])
        arcs, unresolved = [], []
        for asset in package["assets"]:
            if asset["kind"] == "definition":
                a, u = definition_arcs(Path(asset["path"]).read_bytes(), index, asset["sha256"])
                arcs.extend(a); unresolved.extend(u)
        graphs[report] = {**relationship_hints(arcs), "raw_resolved_arcs": arcs, "unresolved_arcs_retained": unresolved}
        counts["resolved"] += len(arcs); counts["unresolved"] += len(unresolved); counts["axis_member_hints"] += len(graphs[report]["axis_member_hints"])
        print("graph", report, "arcs", len(arcs), "unresolved", len(unresolved), "hints", len(graphs[report]["axis_member_hints"]), flush=True)
    (DEST / "public").mkdir(exist_ok=True)
    save(DEST / "public/report_graphs.json", graphs)
    # catalog embeddings for every report card (same recipe as native_field_retrieval_v12.catalog_cache)
    strings = sorted({field_text(card) for report, namespaces in report_namespaces.items() for field in rows if field["namespace"] in namespaces
                      if (card := report_card(field, report)) is not None})
    print("Encoding", len(strings), "field cards", flush=True)
    vectors = encode_texts(strings)
    folder = DEST / "shared_embeddings"; folder.mkdir(exist_ok=True)
    np.savez_compressed(folder / "catalog.npz", features=np.stack([vectors[s] for s in strings]))
    save(folder / "catalog_metadata.json", {"strings": strings, "cache_sha256": sha(folder / "catalog.npz")})
    save(DEST / "manifest.json", {"complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "packages": packages, "fields": len(rows),
                                  "report_namespaces": report_namespaces, "graph_counts": dict(counts), "catalog_sha256": sha(DEST / "field_catalog.json"),
                                  "report_namespaces_sha256": sha(DEST / "report_namespaces.json"), "public_graphs_sha256": sha(DEST / "public/report_graphs.json"),
                                  "standard_assets_reused_from": str(STANDARD), "download_approved_by_user": "2026-10-20",
                                  "fact_or_report_instance_members_parsed": False, "runner_sha256": sha(Path(__file__))})
    print("heldout schema frozen:", len(rows), "fields;", {r: len(v) for r, v in report_namespaces.items()})


if __name__ == "__main__":
    main()

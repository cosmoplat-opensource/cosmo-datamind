"""Public declaration catalogs for the frozen annual-report source cohort.

Only XSD and label linkbases are extracted from checksum-verified packages.
Fact instances, iXBRL tags, numerical facts and scorer/teacher files are not
parsed. Source selection uses the pre-existing report download plan only.
"""

from collections import defaultdict
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
import time
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
import zipfile

import requests
from native_correspondence_prepare_v10 import DEST as NATIVE
from prepare import EXP, save, sha
from public_schema import XS, XBRLI

DEST = NATIVE / "public_schema_extension_v1"
SOURCE = EXP / "runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1/download_plan.json"
RESERVATION = EXP / "runs/visual_scope_relations_v8_20261009/native_source_reservation_v1/metadata_reservation.json"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"
MAX_PACKAGE_BYTES = 100 * 1024**2


def parse_xml(body):
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise ValueError("XML entity declarations are not allowed")
    return ET.fromstring(body)


def declaration_rows(body):
    root = parse_xml(body)
    if root.tag != XS + "schema":
        raise ValueError("Public declaration asset is not an XML schema")
    namespace = root.get("targetNamespace")
    # iterparse entries contain (event, (prefix, URI)); prefix spelling is not
    # a type identity. Resolve QName aliases before comparing declarations.
    bindings = {prefix: uri for _, (prefix, uri) in ET.iterparse(io.BytesIO(body), events=["start-ns"])}
    rows = []
    if namespace:
        for e in root.findall(XS + "element"):
            if not e.get("name"):
                continue
            type_name = e.get("type")
            if type_name:
                prefix, local = type_name.split(":", 1) if ":" in type_name else ("", type_name)
                if prefix and prefix not in bindings:
                    raise ValueError("Undeclared type QName prefix")
                type_name = "{" + bindings.get(prefix, "") + "}" + local
            rows.append({"name": e.get("name"), "namespace": namespace,
                         "type": type_name, "balance": e.get(XBRLI + "balance"),
                         "period_type": e.get(XBRLI + "periodType"),
                         "abstract": e.get("abstract") in ("true", "1"),
                         "schema_id": e.get("id")})
    imports = [e.get("schemaLocation") for e in root.findall(XS + "import") if e.get("schemaLocation")]
    return rows, imports


def english_labels(body):
    root = parse_xml(body)
    if root.tag != LINK + "linkbase":
        return {}
    labels = defaultdict(list)
    for link in root.findall(LINK + "labelLink"):
        locators = {e.get(XLINK + "label"): (e.get(XLINK + "href") or "").split("#")[-1]
                    for e in link.findall(LINK + "loc")}
        resources = {}
        for e in link.findall(LINK + "label"):
            lang = e.get("{http://www.w3.org/XML/1998/namespace}lang", "")
            value = " ".join("".join(e.itertext()).split())
            if lang.lower().startswith("en") and value:
                resources[e.get(XLINK + "label")] = value[:1000]
        for arc in link.findall(LINK + "labelArc"):
            ident = locators.get(arc.get(XLINK + "from"))
            value = resources.get(arc.get(XLINK + "to"))
            if ident and value and value not in labels[ident]:
                labels[ident].append(value)
    return dict(labels)


def selected_package_asset(name):
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("Unsafe package path")
    return p.suffix.lower() == ".xsd" or (
        p.suffix.lower() == ".xml" and re.search(r"(?:lab|label)", p.name, re.I) is not None)


def fetch(session, url, max_bytes):
    for attempt in range(2):
        try:
            with session.get(url, stream=True, timeout=(20, 90)) as reply:
                reply.raise_for_status()
                if int(reply.headers.get("content-length", 0)) > max_bytes:
                    raise ValueError("Public schema source exceeds predeclared byte cap")
                data, size = [], 0
                for block in reply.iter_content(1024**2):
                    size += len(block)
                    if size > max_bytes:
                        raise ValueError("Public schema source exceeds byte cap")
                    data.append(block)
                return b"".join(data)
        except requests.RequestException:
            if attempt:
                raise
            time.sleep(2)


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Public schema catalog already frozen")
    plan = json.loads(SOURCE.read_text())
    reports = [r for r in plan["files"] if r["kind"] == "native_report"]
    reserved = json.loads(RESERVATION.read_text())["entities"]
    metadata = {(e["lei"], r["attributes"]["period_end"]): r
                for e in reserved for r in e["reports"]}
    public_files, report_fields, package_records, failures = [], {}, [], []
    schema_imports = set()
    declared_standard = {}
    for report in reports:
        # Read only the opening document header to obtain public namespace URIs.
        with Path(report["path"]).open("rb") as file:
            opening = file.read(32768).decode("utf-8", errors="strict")
        name = report["lei"] + "_" + report["period_end"][:4]
        namespaces = re.findall(r'xmlns:[\w-]+=["\'](https?://xbrl\.ifrs\.org/taxonomy/\d{4}-\d{2}-\d{2}/ifrs-full)["\']', opening)
        declared_standard[name] = sorted(set(namespaces))
        for namespace in namespaces:
            date = namespace.split("/taxonomy/")[1].split("/")[0]
            schema_imports.add(f"https://xbrl.ifrs.org/taxonomy/{date}/full_ifrs/full_ifrs-cor_{date}.xsd")
    session = requests.Session()
    try:
        for i, report in enumerate(reports, 1):
            record = metadata[(report["lei"], report["period_end"])]
            if record["filing_id"] != report["filing_id"]:
                raise ValueError("Source package differs from frozen native report")
            name = report["lei"] + "_" + report["period_end"][:4]
            package_url = urljoin("https://filings.xbrl.org", record["attributes"]["package_url"])
            checkpoint = DEST / "packages" / (name + ".json")
            if checkpoint.exists():
                done = json.loads(checkpoint.read_text())
                if done["package_sha256"] != record["attributes"]["sha256"]:
                    raise ValueError("Frozen public package checksum changed")
                package_records.append(done)
                public_files.extend(done["assets"])
                schema_imports.update(done["imports"])
                report_fields[name] = done["namespaces"]
                report_fields[name] = sorted(set(report_fields[name]) | set(declared_standard[name]))
                continue
            try:
                body = fetch(session, package_url, MAX_PACKAGE_BYTES)
                if sha256(body).hexdigest() != record["attributes"]["sha256"]:
                    raise ValueError("Complete source package differs from index SHA-256")
                assets, namespaces, imports = [], set(), set()
                with zipfile.ZipFile(io.BytesIO(body)) as archive:
                    for entry in archive.infolist():
                        if entry.is_dir() or not selected_package_asset(entry.filename):
                            continue
                        if entry.file_size > 8 * 1024**2:
                            raise ValueError("Oversized declaration/linkbase member")
                        content = archive.read(entry)  # ZIP CRC verified; no report/instance member is read.
                        file = DEST / "assets" / name / PurePosixPath(entry.filename)
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_bytes(content)
                        asset = {"path": str(file), "sha256": sha(file), "source_member": entry.filename,
                                 "source_package_sha256": sha256(body).hexdigest(), "report": name}
                        assets.append(asset)
                        if file.suffix.lower() == ".xsd":
                            rows, locations = declaration_rows(content)
                            namespaces.update(r["namespace"] for r in rows)
                            imports.update(locations)
                done = {"report": name, "package_url": package_url,
                        "package_sha256": sha256(body).hexdigest(), "package_bytes": len(body),
                        "assets": assets, "imports": sorted(imports), "namespaces": sorted(namespaces),
                        "fact_or_report_instance_members_parsed": False}
                save(checkpoint, done)
                package_records.append(done)
                public_files.extend(assets)
                schema_imports.update(imports)
                report_fields[name] = sorted(namespaces)
                report_fields[name] = sorted(set(report_fields[name]) | set(declared_standard[name]))
            except (requests.RequestException, ValueError, zipfile.BadZipFile) as exc:
                failures.append({"report": name, "error_type": type(exc).__name__, "error": str(exc)[:300]})
            save(DEST / "progress.json", {"processed": i, "expected": len(reports), "failures": failures})
            print("Public declaration packages", i, "/", len(reports), "failed", len(failures), flush=True)
        # Import only issuer-declared IFRS schemas; never learn the version list from teacher facts.
        for url in sorted(schema_imports):
            parsed = urlparse(url)
            if parsed.hostname not in ("xbrl.ifrs.org", "www.ifrs.org") or not parsed.path.endswith(".xsd"):
                continue
            url = parsed._replace(scheme="https").geturl()
            name = sha256(url.encode()).hexdigest()[:20] + ".xsd"
            file = DEST / "standard_schemas" / name
            file.parent.mkdir(parents=True, exist_ok=True)
            if not file.exists():
                file.write_bytes(fetch(session, url, 8 * 1024**2))
            rows, _ = declaration_rows(file.read_bytes())
            public_files.append({"path": str(file), "sha256": sha(file), "url": url, "standard_schema": True})
            root = parse_xml(file.read_bytes())
            for link in root.iter(LINK + "linkbaseRef"):
                location = link.get(XLINK + "href")
                role = link.get(XLINK + "role", "")
                if not location or not role.endswith("labelLinkbaseRef"):
                    continue
                label_url = urljoin(url, location)
                parsed_label = urlparse(label_url)
                if parsed_label.hostname != "xbrl.ifrs.org" or not parsed_label.path.lower().endswith(".xml"):
                    continue
                label_url = parsed_label._replace(scheme="https").geturl()
                label_file = DEST / "standard_labels" / (sha256(label_url.encode()).hexdigest()[:20] + ".xml")
                label_file.parent.mkdir(parents=True, exist_ok=True)
                if not label_file.exists():
                    label_file.write_bytes(fetch(session, label_url, 8 * 1024**2))
                english_labels(label_file.read_bytes())
                public_files.append({"path": str(label_file), "sha256": sha(label_file),
                                     "url": label_url, "standard_label_linkbase": True})
            for namespace in {r["namespace"] for r in rows}:
                for package in package_records:
                    if any(namespace.split("/ifrs-full")[0].replace("http://", "https://")
                           in imp.replace("http://", "https://") for imp in package["imports"]):
                        if namespace not in report_fields[package["report"]]:
                            report_fields[package["report"]].append(namespace)
        fields, labels = {}, defaultdict(list)
        for asset in public_files:
            file = Path(asset["path"])
            if sha(file) != asset["sha256"]:
                raise ValueError("Frozen public declaration asset changed")
            if file.suffix.lower() == ".xsd":
                for row in declaration_rows(file.read_bytes())[0]:
                    ident = (row["namespace"], row["name"])
                    variants = fields.setdefault(ident, [])
                    source_report = asset.get("report", "public_standard")
                    existing = next((v for v in variants if v["declaration"] == row), None)
                    if existing is None:
                        variants.append({"declaration": row, "reports": [source_report]})
                    elif source_report not in existing["reports"]:
                        existing["reports"].append(source_report)
            else:
                for ident, values in english_labels(file.read_bytes()).items():
                    for value in values:
                        if value not in labels[ident]:
                            labels[ident].append(value)
        catalog = []
        for ident, variants in sorted(fields.items()):
            row = {"namespace": ident[0], "name": ident[1], "declaration_variants": variants,
                   "field_id": "s" + sha256(json.dumps(ident).encode()).hexdigest()[:18]}
            row["labels"] = sorted({label for v in variants
                                   for label in labels.get(v["declaration"]["schema_id"], [])})
            for key in ("type", "balance", "period_type", "abstract"):
                values = {v["declaration"][key] for v in variants}
                row[key] = next(iter(values)) if len(values) == 1 else None
            row["source_specific_declarations_required"] = len(variants) > 1
            catalog.append(row)
        save(DEST / "field_catalog.json", catalog)
        save(DEST / "report_namespaces.json", report_fields)
        save(DEST / "manifest.json", {
            "complete": not failures, "expected_reports": len(reports), "packages": package_records,
            "failures": failures, "fields": len(catalog), "public_assets": public_files,
            "fields_with_source_specific_declaration_variants": sum(len(r["declaration_variants"]) > 1 for r in catalog),
            "source_plan_sha256": sha(SOURCE), "reservation_sha256": sha(RESERVATION),
            "catalog_sha256": sha(DEST / "field_catalog.json"),
            "report_namespaces_sha256": sha(DEST / "report_namespaces.json"),
            "source_fact_values_locations_names_or_teacher_files_parsed": False,
            "only_public_schema_declarations_and_label_linkbases_given_to_all_controls": True,
            "all_package_checksums_match_preexisting_publisher_index": True,
            "references": ["https://filings.xbrl.org/docs/api", "https://www.xbrl.org/guidance/faq-report-packages/"],
            "runner_sha256": sha(__file__),
        })
        if failures:
            raise RuntimeError("Public catalog source preparation incomplete; all failures retained")
    finally:
        session.close()


if __name__ == "__main__":
    prepare()

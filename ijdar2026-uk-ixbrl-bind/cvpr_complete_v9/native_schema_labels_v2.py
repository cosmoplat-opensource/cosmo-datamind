"""Keep public label aliases local to their actual report/schema source.

The earlier aggregate catalog remains preserved as source inventory. Its
cross-year pooled aliases are not eligible for chronological learning. Fixed
language encoders may process future public inputs at inference, but the past
field card must not acquire a future report's newly published label aliases.
"""

from collections import defaultdict
import json
from pathlib import Path

from native_schema_catalog_v10 import DEST as SOURCE, english_labels
from prepare import save, sha

DEST = SOURCE.parent / "public_schema_report_labels_v2"


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Report-local public label catalog already frozen")
    source_manifest = json.loads((SOURCE / "manifest.json").read_text())
    labels = defaultdict(lambda: defaultdict(set))
    for asset in source_manifest["public_assets"]:
        file = Path(asset["path"])
        if sha(file) != asset["sha256"]:
            raise ValueError("Frozen public declaration/label file changed")
        if file.suffix.lower() != ".xml":
            continue
        report = asset.get("report", "public_standard")
        for ident, values in english_labels(file.read_bytes()).items():
            labels[report][ident].update(values)
    catalog = json.loads((SOURCE / "field_catalog.json").read_text())
    new = []
    for field in catalog:
        card = dict(field)
        by_report = {}
        for variant in field["declaration_variants"]:
            schema_id = variant["declaration"]["schema_id"]
            for report in labels:
                values = labels[report].get(schema_id, set())
                if values:
                    by_report.setdefault(report, set()).update(values)
        card["labels_by_report"] = {r: sorted(v) for r, v in sorted(by_report.items())}
        card["labels"] = card["labels_by_report"].get("public_standard", [])
        card["cross_report_label_alias_pooling_disabled"] = True
        new.append(card)
    save(DEST / "field_catalog.json", new)
    namespaces = json.loads((SOURCE / "report_namespaces.json").read_text())
    save(DEST / "report_namespaces.json", namespaces)
    snapshot = DEST / "label_preparer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "manifest.json", {
        "complete": True, "fields": len(new), "catalog_sha256": sha(DEST / "field_catalog.json"),
        "report_namespaces_sha256": sha(DEST / "report_namespaces.json"),
        "source_catalog_sha256": sha(SOURCE / "field_catalog.json"),
        "source_manifest_sha256": sha(SOURCE / "manifest.json"),
        "historical_cards_use_only_same_report_or_fixed_public_standard_labels": True,
        "future_report_aliases_are_available_only_for_future_report_inputs": True,
        "source_fact_or_teacher_files_read": False,
        "runner_sha256": sha(snapshot),
    })
    print("Prepared report-local public schema labels", len(new), flush=True)


if __name__ == "__main__":
    prepare()

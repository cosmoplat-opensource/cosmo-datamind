"""Independent source-transform checks for recovered private annotation keys.

This checks the original inline transform against exported values after semantic
matching; it never chooses a match using the displayed value. It does not
certify visible header ownership or make publisher metadata student evidence.
"""

from collections import Counter
from decimal import Decimal
import importlib.util
import json
from pathlib import Path

from lxml import etree

from canonicalization_prepare import ROOT
from prepare import EXP, save, sha


def main():
    auditor_path = EXP / "visual_scope_relations_v8/longitudinal_probe.py"
    spec = importlib.util.spec_from_file_location(
        "pinned_source_transform_auditor", auditor_path
    )
    auditor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(auditor)
    source_file = ROOT / "render_source_manifest.json"
    sources = {
        (r["entity"], r["period_end"]): r
        for r in json.loads(source_file.read_text())["reports"]
    }
    recovery = ROOT / "publisher_identity_v2"
    records = []
    unresolved = []
    counts = Counter()
    for path in (recovery / "scoring_only").glob("*.json"):
        report = json.loads(path.read_text())
        source = next(
            r for r in sources.values() if r["xhtml_sha256"] == report["xhtml_sha256"]
        )
        if (
            sha(source["xhtml"]) != report["xhtml_sha256"]
            or sha(source["facts_scoring_only"]) != report["facts_sha256"]
        ):
            raise RuntimeError("Private original data changed")
        tree = etree.parse(
            source["xhtml"],
            etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True),
        )
        nodes = list(tree.iter("{http://www.xbrl.org/2013/inlineXBRL}nonFraction"))
        published = json.loads(Path(source["facts_scoring_only"]).read_text())
        namespaces = published["documentInfo"]["namespaces"]
        for row in report["rows"]:
            node = nodes[row["private_dom_ordinal"]]
            if not row["published_fact_found"]:
                prefix = row["concept"].split(":")[0]
                unresolved.append(
                    {
                        "source_report": path.name,
                        "concept": row["concept"],
                        "inline_reporting_target": node.get("target"),
                        "native_concept_namespace": node.nsmap.get(prefix),
                        "concept_namespace_in_export": node.nsmap.get(prefix)
                        in namespaces.values(),
                        "no_reference_amount_selected_by_value_similarity": True,
                    }
                )
                continue
            if not row.get("matched_by_full_declared_semantic_key"):
                continue
            counts["recovered_semantic_matches"] += 1
            try:
                value = auditor.displayed_number(
                    "".join(node.itertext()),
                    node.get("format", ""),
                    node.get("sign", ""),
                    node.get("scale", "0"),
                )
                match = value == Decimal(row["canonical_value"])
                status = (
                    "declared_inline_transform_matches_export"
                    if match
                    else "source_transform_mismatch"
                )
                counts[status] += 1
            except (ValueError, TypeError, ArithmeticError):
                status = "unsupported_declared_inline_transform"
                counts[status] += 1
            records.append(
                {
                    "source_report": path.name,
                    "private_dom_ordinal": row["private_dom_ordinal"],
                    "concept": row["concept"],
                    "status": status,
                }
            )
    save(
        recovery / "source_transform_validation.json",
        {
            "counts": dict(counts),
            "recovered_records": records,
            "unresolved_records": unresolved,
            "declared_transform_checked_after_matching_not_used_to_choose_reference": True,
            "original_native_pixels_not_modified": True,
            "not_independent_visible_header_or_semantic_scope_certification": True,
            "future_years_quality_audit_only_not_training": True,
            "human_confirmations": 0,
            "source_transform_auditor_sha256": sha(auditor_path),
            "runner_sha256": sha(__file__),
        },
    )
    print("Recovered publisher source transform check:", dict(counts), flush=True)


if __name__ == "__main__":
    main()

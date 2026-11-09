"""Score source-declared semantic tuples only after all predictions are frozen."""

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from lxml import etree
import numpy as np

from native_binding_learning_v17 import FOLDER, METHODS, POLICIES, SEEDS
from native_binding_expand_v17 import DEST
from native_binding_prepare_v14 import XI, context, expanded, unique_ids, unit
from native_correspondence_prepare_v10 import DEST as NATIVE
from prepare import EXP, OUT, save, sha


def component_correct(prediction, reference):
    result = {key: prediction.get(key) is not None and prediction[key] == reference[key]
              for key in ("field", "entity", "period", "unit")}
    result["dimensions"] = prediction.get("dimensions") is not None and sorted(prediction["dimensions"], key=lambda d: d["axis"]) == sorted(reference["dimensions"], key=lambda d: d["axis"])
    result["quantity"] = prediction.get("quantity") is not None and Decimal(prediction["quantity"]) == Decimal(reference["quantity"])
    result["semantic_tuple_exact"] = all(result.values())
    return result


def scoring_references(future):
    # This function is called only after the complete prediction manifest and
    # each configuration's cryptographic hashes and full inventory are checked.
    # Release: without the issuer filings (ext/xbrl_scope_additional_development, not redistributed) the development
    # references are read from scoring_only/references_resolved_dev.json, written by make_release_ijdar.py with this
    # very function on the original filings.  Re-fetch the filings to recompute them from the source.
    _resolved = NATIVE / "scoring_only/references_resolved_dev.json"
    if _resolved.exists() and not (EXP / "ext/xbrl_scope_additional_development").is_dir():
        _cached = json.loads(_resolved.read_text())
        if not set(future) <= set(_cached):
            raise ValueError("Resolved development references do not cover the requested targets")
        return {k: _cached[k] for k in future}, {str(_resolved): sha(_resolved)}
    reference_file = NATIVE / "scoring_only/references.json"
    references = json.loads(reference_file.read_text())
    plan_file = EXP / "runs/visual_scope_relations_v8_20261009/scope_boundary_development_v1/download_plan.json"
    sources = [r for r in json.loads(plan_file.read_text())["files"] if r["kind"] == "native_report" and not r["period_end"].startswith("2023-")]
    result, hashes = {}, {str(reference_file): sha(reference_file), str(plan_file): sha(plan_file)}
    for source in sources:
        report = source["lei"]+"_"+source["period_end"][:4]
        selected = [ident for ident, t in future.items() if t["report"] == report]
        if not selected:
            continue
        audit_file = OUT / "expanded_development_sources/full_publisher_reference_view_v2/scoring_only" / (source["lei"]+"_"+source["period_end"]+".json")
        audit = json.loads(audit_file.read_text())
        path = Path(source["path"])
        audit_hash, source_hash = sha(audit_file), sha(path)
        if audit["xhtml_sha256"] != source_hash:
            raise ValueError("Scorer source XHTML differs from reference provenance")
        hashes[str(audit_file)], hashes[str(path)] = audit_hash, source_hash
        rows = {r["region_id"]: r for r in audit["rows"] if r.get("region_id")}
        tree = etree.parse(str(path), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True))
        if tree.docinfo.doctype:
            raise ValueError("Unsafe original scorer report")
        contexts = unique_ids(tree.iter(XI+"context"))
        units = unique_ids(tree.iter(XI+"unit"))
        for ident in selected:
            ref = references[ident]
            if ref["source_year"] == 2023 or ref["source_audit_sha256"] != audit_hash:
                raise ValueError("Future scorer reference provenance differs")
            row = rows[ref["publisher_region_id"]]
            field = "{"+ref["field_namespace"]+"}"+ref["field_name"]
            if expanded(row["concept"], tree.getroot().nsmap) != field:
                raise ValueError("Scorer reference field disagrees with native declaration")
            result[ident] = {"field": field, **context(contexts[row["context_ref"]]),
                             "unit": unit(units[row["unit_ref"]]), "quantity": ref["canonical_value"]}
    if set(result) != set(future):
        raise ValueError("Complete 560-target semantic scorer denominator required")
    return result, hashes


def run():
    if (FOLDER / "development_results.json").exists():
        raise RuntimeError("Complete semantic-tuple development results already frozen")
    manifest_file = FOLDER / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if not manifest["complete"] or manifest["prediction_files"] != 1260:
        raise ValueError("All complete-binding predictions must freeze before label access")
    if sha(FOLDER / "design.json") != manifest["design_sha256"]:
        raise ValueError("Frozen complete-binding design differs")
    future = {t["task_id"]: t for t in json.loads((DEST / "public/tasks.json").read_text()) if t["source_year"] != 2023}
    if len(future) != 560:
        raise ValueError("Full future development target inventory required")
    companies = sorted({t["company"] for t in future.values()})
    data = {}
    for filename, digest in manifest["predictions_sha256"].items():
        file = FOLDER / filename
        if sha(file) != digest:
            raise ValueError("Complete-binding prediction changed before scoring")
        record = json.loads(file.read_text())
        expected = {ident for ident, task in future.items() if task["company"] == record["company"]}
        if record["design_sha256"] != manifest["design_sha256"] or len(record["predictions"]) != len(expected) or {p["task_id"] for p in record["predictions"]} != expected:
            raise ValueError("Changed/missing/duplicate prediction configuration")
        key = (record["policy"], record["method"], record["seed"], record["automatic_feedback_cases"])
        if record["company"] in data.setdefault(key, {}):
            raise ValueError("Duplicate company configuration")
        data[key][record["company"]] = record
    # No future source context, unit or tuple is read above this boundary.
    references, source_hashes = scoring_references(future)
    outcomes, results, means = {}, {}, {}
    for policy in POLICIES:
        for method in METHODS:
            for seed in SEEDS:
                for budget in (0, 5, 10):
                    key = (policy, method, seed, budget)
                    if set(data[key]) != set(companies):
                        raise ValueError("Full company configuration required")
                    combined = {p["task_id"]: p for r in data[key].values() for p in r["predictions"]}
                    if set(combined) != set(future):
                        raise ValueError("Scorer target denominator changed")
                    rows = {ident: component_correct(p["predicted_binding"], references[ident]) for ident, p in combined.items()}
                    label = f"{policy}/{method}/{seed}/{budget}"
                    outcomes[label] = {ident: r["semantic_tuple_exact"] for ident, r in rows.items()}
                    results[label] = {"correct": sum(outcomes[label].values()), "total": len(future),
                                      "semantic_tuple_exact_accuracy": sum(outcomes[label].values())/len(future),
                                      "component_accuracy": {c: sum(r[c] for r in rows.values())/len(future) for c in next(iter(rows.values())) if c != "semantic_tuple_exact"},
                                      "returned_complete_semantic_tuple_coverage": sum(p["semantic_tuple_complete"] for p in combined.values())/len(future),
                                      "certified_visual_header_scope_coverage": 0,
                                      "per_company": {c: sum(v for ident, v in outcomes[label].items() if future[ident]["company"] == c) for c in companies}}
    for policy in POLICIES:
        for method in METHODS:
            for budget in (0, 5, 10):
                values = [results[f"{policy}/{method}/{s}/{budget}"] for s in SEEDS]
                means[f"{policy}/{method}/{budget}"] = {
                    "correct": float(np.mean([v["correct"] for v in values])), "total": 560,
                    "semantic_tuple_exact_accuracy": float(np.mean([v["semantic_tuple_exact_accuracy"] for v in values])),
                    "returned_complete_semantic_tuple_coverage": float(np.mean([v["returned_complete_semantic_tuple_coverage"] for v in values])),
                    "component_accuracy": {c: float(np.mean([v["component_accuracy"][c] for v in values])) for c in values[0]["component_accuracy"]}}
    paired, rng = {}, np.random.default_rng(20261015)
    for policy in POLICIES:
        for budget in (0, 5, 10):
            for baseline in ("public_binding_prior", "generic_joint", "learned_semantic_memory", "frozen_semantic_memory"):
                cluster = defaultdict(list)
                for seed in SEEDS:
                    a = outcomes[f"{policy}/factor_binding/{seed}/{budget}"]
                    b = outcomes[f"{policy}/{baseline}/{seed}/{budget}"]
                    for ident in future:
                        cluster[future[ident]["company"]].append(int(a[ident])-int(b[ident]))
                values = [float(np.mean(cluster[c])) for c in companies]
                draws = np.mean(np.array(values)[rng.integers(0, len(values), size=(4000, len(values)))], 1)
                paired[f"{policy}/{budget}/{baseline}"] = {"mean_semantic_tuple_accuracy_difference": float(np.mean(values)),
                    "company_identifier_cluster_bootstrap_95_ci": np.quantile(draws, [.025, .975]).tolist(),
                    "development_only_not_independent_test_or_multiple_testing_corrected_claim": True}
    snapshot = FOLDER / "scorer_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(FOLDER / "development_results.json", {
        "complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(), "companies": len(companies), "unique_future_targets": len(future),
        "per_configuration": results, "seed_means": means, "paired_comparisons": paired,
        "prediction_manifest_sha256": sha(manifest_file), "scoring_reference_and_source_sha256": source_hashes,
        "scorer_snapshot_sha256": sha(snapshot), "automatic_feedback_budgets": [0, 5, 10], "human_runtime_confirmations": 0,
        "entity_is_common_metadata_assistance_not_visual_learning": True,
        "semantic_tuple_exact_metric_does_not_certify_visual_header_ownership": True,
        "independent_native_test_completed": False, "ontology_unique_advantage_established": False,
    })
    print("Complete semantic-tuple development scoring finished", flush=True)


if __name__ == "__main__":
    run()

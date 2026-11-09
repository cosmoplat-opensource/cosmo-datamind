"""Post-prediction development scoring of annotated grid-header coverage."""

from collections import defaultdict
import json

import numpy as np

from fintab_scope_prepare_v10 import DEST, coverage
from fintab_scope_train_v10 import METHODS, SEEDS
from prepare import save, sha


def safe_ratio(a, b):
    return a / b if b else 0.0


def score(record, prediction, edge_key="coverage_edges"):
    owners, cells = record["word_cell_ids"], record["cells"]
    known_words = {i for i, owner in enumerate(owners) if owner is not None}
    headers = {i for i in known_words if cells[owners[i]]["is_column_header"]}
    body = {i for i in known_words if not cells[owners[i]]["is_column_header"]
            and not cells[owners[i]]["is_projected_row_header"]}
    expected = {(h, t) for h in headers for t in body
                if coverage(cells[owners[h]], cells[owners[t]]) is True}
    head_predictions = set(prediction["header_words"])
    proposed = {tuple(pair) for pair in prediction[edge_key]}
    if any(type(i) is not int or not 0 <= i < len(owners) for i in head_predictions) or any(
        len(pair) != 2 or any(type(i) is not int or not 0 <= i < len(owners) for i in pair) for pair in proposed):
        raise ValueError("Prediction word index outside frozen native inventory")
    judged, unknown = set(), set()
    for h, t in proposed:
        if h not in known_words or t not in body:
            unknown.add((h, t))
        elif h not in headers or coverage(cells[owners[h]], cells[owners[t]]) is not None:
            judged.add((h, t))
        else:
            unknown.add((h, t))
    tp, fp, fn = len(judged & expected), len(judged - expected), len(expected - judged)
    return {
        "word_count": len(owners), "unmapped_word_count": len(owners) - len(known_words),
        "header_tp": len(head_predictions & headers),
        "header_fp": len((head_predictions & known_words) - headers),
        "header_fn": len(headers - head_predictions),
        "unmapped_header_predictions": len(head_predictions - known_words),
        "coverage_tp": tp, "coverage_fp": fp, "coverage_fn": fn,
        "coverage_unjudgeable_predictions": len(unknown),
        "coverage_f1": safe_ratio(2*tp, 2*tp+fp+fn),
        "known_scope_graph_exact": not fp and not fn,
        "all_predictions_verified_scope_graph_exact": not fp and not fn and not unknown,
        "grid_coverage_not_financial_semantic_binding": True,
    }


def aggregate(rows):
    counts = {k: sum(r[k] for r in rows) for k in (
        "word_count", "unmapped_word_count", "header_tp", "header_fp", "header_fn",
        "unmapped_header_predictions", "coverage_tp", "coverage_fp", "coverage_fn",
        "coverage_unjudgeable_predictions")}
    tp, fp, fn = (counts["coverage_" + k] for k in ("tp", "fp", "fn"))
    return counts | {
        "header_precision_known": safe_ratio(counts["header_tp"], counts["header_tp"] + counts["header_fp"]),
        "header_recall_known": safe_ratio(counts["header_tp"], counts["header_tp"] + counts["header_fn"]),
        "coverage_precision_known": safe_ratio(tp, tp+fp),
        "coverage_precision_unknown_counted_unverified": safe_ratio(tp, tp+fp+counts["coverage_unjudgeable_predictions"]),
        "coverage_recall_known": safe_ratio(tp, tp+fn),
        "coverage_micro_f1_known": safe_ratio(2*tp, 2*tp+fp+fn),
        "coverage_macro_table_f1_known": float(np.mean([r["coverage_f1"] for r in rows])),
        "known_scope_graph_exact": float(np.mean([r["known_scope_graph_exact"] for r in rows])),
        "all_predictions_verified_scope_graph_exact": float(np.mean([r["all_predictions_verified_scope_graph_exact"] for r in rows])),
    }


def run():
    folder = DEST / "learning_v1"
    labels_file = DEST / "scoring_only/val_labels.json"
    labels = json.loads(labels_file.read_text())
    public = {r["table_id"]: r for r in json.loads((DEST / "public/tables.json").read_text()) if r["split"] == "val"}
    if set(labels) != set(public):
        raise ValueError("Frozen development reference inventory changed")
    results, per_table = {}, {}
    for method in METHODS:
        for seed in SEEDS:
            root = folder / method / str(seed)
            manifest = json.loads((root / "manifest.json").read_text())
            file = root / "predictions.json"
            if not manifest["complete"] or sha(file) != manifest["prediction_sha256"]:
                raise ValueError("Frozen prediction checksum/completeness failed")
            predictions = json.loads(file.read_text())
            if len(predictions) != len(public) or {p["table_id"] for p in predictions} != set(public):
                raise ValueError("Missing/duplicate development prediction cannot be dropped")
            for key in (["coverage_edges", "generic_rectangle_decoder_edges"] if method == "generic_joint" else ["coverage_edges"]):
                name = method if key == "coverage_edges" else "generic_joint_rectangle_decoder"
                rows = [dict(score(labels[p["table_id"]], p, key), table_id=p["table_id"],
                             issuer_id=public[p["table_id"]]["issuer_id"]) for p in predictions]
                ident = f"{name}/{seed}"
                results[ident], per_table[ident] = aggregate(rows), rows
    means = {}
    for method in (*METHODS, "generic_joint_rectangle_decoder"):
        values = [results[f"{method}/{seed}"] for seed in SEEDS]
        means[method] = {k: float(np.mean([v[k] for v in values])) for k in values[0]}
    # Paired issuer-cluster bootstrap on all seeds, not treating seeds as new sources.
    companies = sorted({r["issuer_id"] for r in public.values()})
    paired, rng = {}, np.random.default_rng(20261014)
    for baseline in ("generic_joint", "generic_joint_rectangle_decoder"):
        deltas = defaultdict(list)
        for seed in SEEDS:
            a = {r["table_id"]: r for r in per_table[f"learned_rectangle_scope/{seed}"]}
            b = {r["table_id"]: r for r in per_table[f"{baseline}/{seed}"]}
            for ident in a:
                deltas[a[ident]["issuer_id"]].append(a[ident]["coverage_f1"] - b[ident]["coverage_f1"])
        sampled = []
        for _ in range(4000):
            group = rng.choice(companies, len(companies), replace=True)
            sampled.append(float(np.mean([v for c in group for v in deltas[c]])))
        paired[baseline] = {"macro_table_f1_difference": float(np.mean([v for vs in deltas.values() for v in vs])),
                            "issuer_bootstrap_95_ci": np.quantile(sampled, [.025, .975]).tolist(),
                            "issuer_identifier_clusters": len(companies),
                            "not_an_audited_legal_entity_alias_or_template_independent_test": True}
    save(folder / "development_results.json", {
        "complete": True, "tables_per_seed": len(public), "seeds": SEEDS,
        "all_seed_results": results, "seed_means": means, "paired_comparisons": paired,
        "per_table": per_table, "reference_sha256": sha(labels_file),
        "main_ontology_advantage_or_full_binding_established": False,
        "unknown_words_and_edges_not_certified_by_numerical_agreement": True,
        "official_test_not_scored": True, "scorer_sha256": sha(__file__),
    })
    print(json.dumps({"means": means, "paired": paired}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()

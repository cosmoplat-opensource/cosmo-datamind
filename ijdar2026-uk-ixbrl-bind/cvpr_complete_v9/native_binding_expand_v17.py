"""Equal-input candidate expansion using public text and definition networks.

Keep every v16 candidate. New dimension paths and completed calendar dates are
uncertain hypotheses for every control. No teacher/instance/context/reference
file is read, and no administrator's source year supplies a missing date.
"""

from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path

import numpy as np
import torch

from native_binding_schema_v16 import DEST as ORIGINAL
from native_binding_prepare_v14 import DEST as BINDING
from native_dimension_network_v17 import DEST as NETWORK
from native_field_retrieval_v12 import DEST as FIELD, field_text, report_card
from native_schema_labels_v2 import DEST as LABELS
from native_visible_periods_v17 import visible_period_candidates
from pilot_features import encode_texts, TEXT_MODEL
from prepare import opaque, save, sha


DEST = BINDING.parent / "binding_candidates_v17"
GRAPH_SINGLES, GRAPH_PAIRS = 128, 64


def merge_hypotheses(original, additions, key):
    output = list(original)
    seen = {json.dumps(row[key], sort_keys=True) for row in output}
    for row in additions:
        ident = json.dumps(row[key], sort_keys=True)
        if ident not in seen:
            seen.add(ident)
            output.append(row)
    return output


def graph_dimension_tuples(scored):
    ordered = sorted(scored, key=lambda p: (-p["retrieval_score"], p["axis"], p["member"]))[:GRAPH_SINGLES]
    singles = [{"dimensions": [{"axis": p["axis"], "kind": "explicit", "member": p["member"]}],
                "retrieval_score": p["retrieval_score"], "source_path_arc_ids": p["source_path_arc_ids"]} for p in ordered]
    pair_map = {}
    for a, b in combinations(singles[:48], 2):
        if a["dimensions"][0]["axis"] == b["dimensions"][0]["axis"]:
            continue
        dimensions = sorted(a["dimensions"]+b["dimensions"], key=lambda d: d["axis"])
        ident = json.dumps(dimensions, sort_keys=True)
        pair_map[ident] = {"dimensions": dimensions, "retrieval_score": (a["retrieval_score"]+b["retrieval_score"])/2,
                           "source_path_arc_ids": a["source_path_arc_ids"]+b["source_path_arc_ids"]}
    pairs = sorted(pair_map.values(), key=lambda p: (-p["retrieval_score"], json.dumps(p["dimensions"], sort_keys=True)))[:GRAPH_PAIRS]
    return [{**row, "candidate_id": opaque(["dimension-graph-v17", row["dimensions"]]),
             "basis": "source_public_definition_path_hint", "axis_member_membership_certified": False,
             "visible_dimension_binding_certified": False, "full_dts_certified": False} for row in singles+pairs]


def dimension_query(task):
    obs = task.get("common_visual_observation") or {}
    meaning = " | ".join(v for k in ("dimension_text", "version_text") if (v := obs.get(k, "unknown")) != "unknown") or "unknown"
    column = " | ".join([task.get("public_top_text", "")] + [h["text"] for h in obs.get("headers", []) if h["kind"] in {"dimension", "version", "entity"}])
    return meaning[:1500], column[:3500]


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Expanded shared candidate inventory already frozen")
    torch.set_num_threads(2)
    original_file = ORIGINAL / "public/tasks.json"
    original_manifest = json.loads((ORIGINAL / "manifest.json").read_text())
    graph_file = NETWORK / "public/report_graphs.json"
    graph_manifest = json.loads((NETWORK / "manifest.json").read_text())
    public_file = FIELD / "public/tasks.json"
    field_manifest = json.loads((FIELD / "manifest.json").read_text())
    labels_file = LABELS / "field_catalog.json"
    label_manifest = json.loads((LABELS / "manifest.json").read_text())
    if not all(m["complete"] for m in (original_manifest, graph_manifest, field_manifest, label_manifest)):
        raise ValueError("Complete shared public inputs required")
    if any(sha(p) != digest for p, digest in ((original_file, original_manifest["candidate_inventory_sha256"]),
           (graph_file, graph_manifest["public_graphs_sha256"]), (public_file, field_manifest["public_target_sha256"]),
           (labels_file, label_manifest["catalog_sha256"]))):
        raise ValueError("Frozen shared candidate/network input changed")
    tasks = json.loads(public_file.read_text())
    original = json.loads(original_file.read_text())
    if len(tasks) != 840 or [t["task_id"] for t in tasks] != [t["task_id"] for t in original]:
        raise ValueError("Full ordered public inventory required")
    graphs = json.loads(graph_file.read_text())
    fields = json.loads(labels_file.read_text())
    namespaces_file = LABELS / "report_namespaces.json"
    if sha(namespaces_file) != label_manifest["report_namespaces_sha256"]:
        raise ValueError("Source namespace list changed")
    namespaces = json.loads(namespaces_file.read_text())
    cards = {}
    for report in graphs:
        cards[report] = {"{"+c["namespace"]+"}"+c["name"]: c
                         for f in fields if f["namespace"] in namespaces[report]
                         if (c := report_card(f, report)) is not None}
    embedding_file = FIELD / "shared_embeddings/catalog.npz"
    embedding_meta_file = FIELD / "shared_embeddings/catalog_metadata.json"
    embedding_meta = json.loads(embedding_meta_file.read_text())
    if sha(embedding_file) != embedding_meta["cache_sha256"] or sha(labels_file) != embedding_meta["catalog_sha256"]:
        raise ValueError("Shared source-local embeddings changed")
    if any(sha(TEXT_MODEL / name) != digest for name, digest in embedding_meta["text_model_files"].items()):
        raise ValueError("Shared semantic encoder changed")
    with np.load(embedding_file) as archive:
        vectors = dict(zip(embedding_meta["strings"], archive["features"], strict=True))
    queries = {t["task_id"]: dimension_query(t) for t in tasks}
    embeddings = encode_texts(sorted({s for q in queries.values() for s in q}))
    records, counts = [], Counter({"targets": len(tasks)})
    for task, old in zip(tasks, original, strict=True):
        graph = graphs[task["report"]]
        local = cards[task["report"]]
        meaning, column = (embeddings[s] for s in queries[task["task_id"]])
        hinted_axes = {}
        for rank, field in enumerate(task["field_candidates"]):
            name = "{"+field["namespace"]+"}"+field["name"]
            for axis in graph["field_axis_hints"].get(name, []):
                hinted_axes[axis] = max(hinted_axes.get(axis, 0), 1/(rank+1))
        scored = []
        for hint in graph["axis_member_hints"]:
            if hint["axis"] not in local or hint["member"] not in local:
                continue
            axis = vectors[field_text(local[hint["axis"]])]
            member = vectors[field_text(local[hint["member"]])]
            score = float(.45*(member@meaning) + .15*(axis@meaning) + .25*(member@column) + .15*(axis@column))
            scored.append({**hint, "retrieval_score": score + .1*hinted_axes.get(hint["axis"], 0)})
        dimensions = merge_hypotheses(old["dimension_tuple_candidates"], graph_dimension_tuples(scored), "dimensions")
        periods, omitted = visible_period_candidates(task)
        periods = merge_hypotheses(old["period_candidates"], periods, "period")
        records.append({**old, "dimension_tuple_candidates": dimensions, "period_candidates": periods,
                        "public_dimension_relationship_hints_available": True,
                        "dimension_membership_networks_not_loaded": False,
                        "full_dts_dimension_membership_validation_certified": False,
                        "new_period_hypotheses_omitted_by_predeclared_cap": omitted,
                        "old_candidate_inventory_is_preserved_as_subset": True,
                        "future_or_historical_teacher_used_for_candidate_generation": False})
        counts["additional_dimension_tuple_hypotheses"] += len(dimensions)-len(old["dimension_tuple_candidates"])
        counts["additional_period_hypotheses"] += len(periods)-len(old["period_candidates"])
        counts["new_calendar_convention_period_hypotheses"] += sum(p.get("calendar_convention_assumed", False) for p in periods)
        counts["targets_with_complete_period_hypothesis"] += any(p["period"].get("complete") for p in periods)
        counts["new_period_hypotheses_omitted_by_fixed_cap"] += omitted
        counts["failed_visual_observations_retained"] += task["common_observation_failed"]
    save(DEST / "public/tasks.json", records)
    snapshot = DEST / "candidate_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    period_snapshot = DEST / "visible_period_parser_snapshot.py"
    period_snapshot.write_bytes(Path(__file__).with_name("native_visible_periods_v17.py").read_bytes())
    save(DEST / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "stage": "shared_visible_period_and_public_definition_candidate_expansion_not_model_evaluation",
        "candidate_inventory_sha256": sha(DEST / "public/tasks.json"), "builder_snapshot_sha256": sha(snapshot),
        "period_parser_snapshot_sha256": sha(period_snapshot),
        "input_sha256": {str(p): sha(p) for p in (original_file, graph_file, public_file, labels_file, namespaces_file, embedding_file, embedding_meta_file)},
        "maximum_added_graph_single_tuples": GRAPH_SINGLES, "maximum_added_graph_two_dimension_tuples": GRAPH_PAIRS,
        "maximum_added_period_hypotheses": 48, "all_original_v16_candidates_preserved": True,
        "all_controls_receive_identical_expanded_candidates_and_public_network_hints": True,
        "calendar_annual_or_comparative_completion_is_marked_as_hypothesis_not_observed_boundary": True,
        "administrative_source_year_not_used_to_generate_periods": True,
        "publisher_instances_contexts_teachers_or_scoring_references_read": False,
        "certified_visible_header_ownership_or_full_dts_membership": False,
        "human_runtime_confirmations": 0,
        "learned_full_binding_model_or_independent_test_complete": False,
    })
    print("Expanded shared period/dimension candidates", dict(counts), flush=True)


if __name__ == "__main__":
    prepare()

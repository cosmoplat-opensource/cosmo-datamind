"""Shared schema-field retrieval from native visual labels, with no references.

This is a new post-observation retrieval stage. It preserves the original v10
observations; those observations had only the initial 32-field catalog and are
not relabeled as having seen the expanded schema. All model controls receive
the same new cards and score vectors. An explicit null option remains available.
"""

import argparse
import json
from pathlib import Path
import re

import numpy as np
import torch

from native_correspondence_prepare_v10 import DEST as NATIVE
from native_schema_catalog_v10 import DEST as SCHEMA
from pilot_features import TEXT_MODEL, encode_texts
from prepare import save, sha

DEST = NATIVE / "field_retrieval_v2"
TOP_K = 64


def report_card(field, report):
    variants = [v["declaration"] for v in field["declaration_variants"]
                if report in v["reports"] or "public_standard" in v["reports"]]
    if not variants:
        return None
    card = {k: field[k] for k in ("name", "namespace", "field_id", "labels")}
    for key in ("type", "balance", "period_type", "abstract"):
        values = {v[key] for v in variants}
        card[key] = next(iter(values)) if len(values) == 1 else None
    card["public_declaration_conflict"] = len(variants) != 1
    return card


def field_text(card):
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", card["name"]).replace("_", " ")
    return name + " | " + " | ".join(card["labels"][:8])[:1000]


def catalog_cache():
    manifest = json.loads((SCHEMA / "manifest.json").read_text())
    file = SCHEMA / "field_catalog.json"
    if not manifest["complete"] or sha(file) != manifest["catalog_sha256"]:
        raise ValueError("Complete source-matched public field catalog required")
    fields = json.loads(file.read_text())
    strings = sorted({field_text(c) for c in fields})
    folder = DEST / "shared_embeddings"
    folder.mkdir(parents=True, exist_ok=True)
    cache, meta_file = folder / "catalog.npz", folder / "catalog_metadata.json"
    if cache.exists() and meta_file.exists():
        meta = json.loads(meta_file.read_text())
        if meta["catalog_sha256"] != sha(file) or meta["cache_sha256"] != sha(cache) or meta["strings"] != strings:
            raise ValueError("Frozen public field embeddings changed")
        vectors = dict(zip(strings, np.load(cache)["features"]))
    else:
        torch.set_num_threads(2)
        vectors = encode_texts(strings)
        np.savez_compressed(cache, features=np.stack([vectors[s] for s in strings]))
        save(meta_file, {"catalog_sha256": sha(file), "cache_sha256": sha(cache), "strings": strings,
                         "text_model_files": {f.name: sha(f) for f in TEXT_MODEL.iterdir() if f.is_file()},
                         "reference_files_read": False})
    return fields, vectors


def query_text(task, observation):
    observed_row = observation.get("row_text", "unknown")
    fields = " | ".join(h["text"] for h in observation.get("headers", []) if h["kind"] == "field")
    return {"visual_row": observed_row + " | " + fields,
            "native_row": task["public_row_text"] or "unknown",
            "section": observation.get("section_text", "unknown")}


def prepare():
    if (DEST / "manifest.json").exists():
        raise RuntimeError("Expanded public field retrieval already frozen")
    fields, vectors = catalog_cache()
    public_file = NATIVE / "public/tasks.json"
    tasks = json.loads(public_file.read_text())
    observation_file = NATIVE / "common_visual/manifest.json"
    manifest = json.loads(observation_file.read_text())
    if not manifest["complete"] or manifest["public_tasks_sha256"] != sha(public_file):
        raise ValueError("Complete frozen no-oracle native observations required")
    observed = {}
    for record in manifest["records"]:
        if record["status"] == "completed":
            for target in record["targets"]:
                if target["task_id"] in observed:
                    raise ValueError("Duplicate native observation target")
                observed[target["task_id"]] = target
    namespaces_file = SCHEMA / "report_namespaces.json"
    report_namespaces = json.loads(namespaces_file.read_text())
    report_pools = {}
    for report in sorted({t["report"] for t in tasks}):
        namespaces = set(report_namespaces[report])
        cards = []
        for field in fields:
            if field["namespace"] not in namespaces:
                continue
            card = report_card(field, report)
            if card is not None:
                cards.append(card)
        report_pools[report] = cards
    queries = {t["task_id"]: query_text(t, observed.get(t["task_id"], {})) for t in tasks}
    query_strings = sorted({s for row in queries.values() for s in row.values()})
    query_vectors = encode_texts(query_strings)
    public = []
    for task in tasks:
        cards = report_pools[task["report"]]
        matrix = np.stack([vectors[field_text(c)] for c in cards])
        q = queries[task["task_id"]]
        visual = matrix @ query_vectors[q["visual_row"]]
        native = matrix @ query_vectors[q["native_row"]]
        section = matrix @ query_vectors[q["section"]]
        common_failed = task["task_id"] not in observed
        scores = (.8 * native + .2 * section) if common_failed else (.65 * visual + .25 * native + .1 * section)
        chosen = np.argsort(-scores, kind="stable")[:TOP_K]
        new = {k: v for k, v in task.items() if k != "field_candidates"}
        new.update(field_candidates=[cards[int(i)] | {"retrieval_scores": [float(visual[i]), float(native[i]), float(section[i])]} for i in chosen],
                   common_visual_observation=observed.get(task["task_id"]),
                   common_observation_failed=common_failed)
        public.append(new)
    save(DEST / "public/tasks.json", public)
    save(DEST / "public/report_field_counts.json", {k: len(v) for k, v in report_pools.items()})
    snapshot = DEST / "retriever_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(DEST / "manifest.json", {
        "complete": True, "tasks": len(public), "field_candidates_per_target": TOP_K,
        "public_target_sha256": sha(DEST / "public/tasks.json"),
        "original_public_sha256": sha(public_file), "common_visual_sha256": sha(observation_file),
        "schema_catalog_sha256": sha(SCHEMA / "field_catalog.json"),
        "report_namespaces_sha256": sha(namespaces_file),
        "retriever_snapshot_sha256": sha(snapshot),
        "all_840_targets_retained_including_missing_or_ambiguous_fields": True,
        "original_visual_responses_are_not_represented_as_expanded_catalog_conditioned": True,
        "target_field_type_namespace_quantity_or_teacher_files_read": False,
        "only_public_document_namespace_declarations_select_the_catalog": True,
        "local_report_declaration_variants_not_merged_by_namespace_alone": True,
        "field_identifier_and_types_supplied_equally_to_all_controls": True,
        "human_runtime_confirmations": 0,
    })
    print("Frozen no-reference native field retrieval", len(public), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-only", action="store_true")
    args = parser.parse_args()
    catalog_cache() if args.catalog_only else prepare()

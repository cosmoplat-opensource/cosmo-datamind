"""Load the frozen v26 common candidate bank into v23-shaped training packs."""

import json

import numpy as np
import torch
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import historical_positive
from native_binding_prepare_v14 import DEST as TEACHERS
from native_dimension_network_v17 import DEST as NETWORK
from native_header_alignment_v21 import DEST as ALIGN
from native_v26_bank import DEST
from native_witness_data_v23 import component_prior, relation_features
from prepare import sha


def load_data():
    manifest_file = DEST / "features/manifest.json"
    manifest = json.loads(manifest_file.read_text())
    public_file, public_meta_file = DEST / "public/witnesses.json", DEST / "public/manifest.json"
    public = json.loads(public_meta_file.read_text())
    if not manifest["complete"] or sha(public_file) != manifest["witness_inventory_sha256"] or sha(public_file) != public["witness_inventory_sha256"]:
        raise ValueError("Complete frozen v26 bank required")
    for name, digest in manifest["files"].items():
        if sha(DEST / "features" / name) != digest:
            raise ValueError("v26 features changed: " + name)
    task_file, record_file = DEST / "public/tasks.json", DEST / "public/records.json"
    if sha(task_file) != public["public_task_sha256"] or sha(record_file) != public["public_record_sha256"]:
        raise ValueError("v26 public inventory changed")
    graph_file = NETWORK / "public/report_graphs.json"
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    if sha(teacher_file) != json.loads((TEACHERS / "manifest.json").read_text())["teacher_sha256"]:
        raise ValueError("Historical teacher provenance changed")
    tasks, records, inventory, graphs, teachers = [json.loads(p.read_text()) for p in (task_file, record_file, public_file, graph_file, teacher_file)]
    if not (len(tasks) == len(records) == len(inventory) == 840):
        raise ValueError("All 840 public targets required")
    contexts = torch.from_numpy(np.load(DEST / "features/context.npy"))
    packs = {}
    for i, (task, original, item) in enumerate(zip(tasks, records, inventory, strict=True)):
        if item["task_id"] != task["task_id"]:
            raise ValueError("Public target ordering differs")
        groups = item["components"]
        with np.load(DEST / "features" / (task["task_id"] + ".npz")) as arrays:
            nodes = [torch.from_numpy(arrays[c]) for c in CHANNELS]
            indices = [torch.from_numpy(arrays[c + "_groups"]) for c in CHANNELS[1:]]
            priors = [component_prior(task, c, arrays[c], groups[c], None if j == 0 else arrays[c + "_groups"]) for j, c in enumerate(CHANNELS)]
            pool = torch.from_numpy(arrays["common_header_pool"])
        masks = [torch.zeros(len(groups[c]), dtype=torch.bool) for c in CHANNELS]
        if task["source_year"] == 2023:
            teacher = teachers[task["task_id"]]
            masks = [historical_positive(task, groups[c], teacher, c)[:-1] for c in CHANNELS]
        relations, pair_priors = relation_features(groups, original, graphs[task["report"]])
        counts = [torch.bincount(index, minlength=len(groups[c])) for c, index in zip(CHANNELS[1:], indices, strict=True)]
        if any((count == 0).any() for count in counts):
            raise ValueError("An empty semantic witness group was introduced")
        packs[i] = {"task_id": task["task_id"], "company": task["company"], "year": task["source_year"],
                    "context": contexts[i], "nodes": nodes, "groups": indices, "group_counts": counts,
                    "prior": priors, "pool": pool, "relations": relations, "pair_prior": pair_priors,
                    "masks": masks, "factor_pairs": len(nodes[0]) * sum(len(n) for n in nodes[1:])}
    historical = {t["task_id"] for t in tasks if t["source_year"] == 2023}
    if historical != set(teachers) or len(historical) != 280:
        raise ValueError("Historical source boundary differs")
    prefixes_file = ALIGN / "learning_full_transfer/feedback_prefixes.json"
    prefixes = json.loads(prefixes_file.read_text())
    design = json.loads((ALIGN / "learning_full_transfer/design.json").read_text())
    if sha(prefixes_file) != design["feedback_prefix_sha256"]:
        raise ValueError("Frozen automatic-feedback prefixes changed")
    return tasks, inventory, packs, prefixes, {
        "feature_manifest_sha256": sha(manifest_file), "public_inventory_sha256": sha(public_file),
        "teacher_sha256": sha(teacher_file), "public_graph_sha256": sha(graph_file),
        "feedback_prefix_sha256": sha(prefixes_file), "bank": "common_candidate_v26"}

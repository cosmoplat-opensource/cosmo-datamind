"""Load frozen common evidence; only 2023 semantic teachers may supervise."""

import json

import numpy as np
import torch
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import historical_positive
from native_binding_prepare_v14 import DEST as TEACHERS
from native_dimension_network_v17 import DEST as NETWORK
from native_joint_features_v18 import unit_compatibility
from native_witness_inventory_v23 import DEST, INPUT
from prepare import sha


def component_prior(task, channel, nodes, groups, group_ids=None):
    """The same public soft prior for every learner, including explicit unknown."""
    vectors = nodes[:, :112]
    values = [g["value"] for g in groups]
    ids = np.arange(len(values)) if group_ids is None else group_ids
    unknown = np.array([values[j] is None for j in ids])
    if channel == "field":
        score = .65 * vectors[:, 61] + .25 * vectors[:, 62] + .1 * vectors[:, 63]
        if task["common_observation_failed"]:
            score = .8 * vectors[:, 62] + .2 * vectors[:, 63]
        result = 4 * (score - score[~unknown].max()) - 2 * vectors[:, 59]
    elif channel == "period":
        result = -.25 * vectors[:, 77] - 3 * (1 - vectors[:, 78])
    elif channel == "dimensions":
        result = 2 * (vectors[:, 99] - vectors[~unknown, 99].max())
        for i, j in enumerate(ids):
            if values[j] == []:
                result[i] = 0 if task["dimension_visible_text"] == "unknown" else -.5
    elif channel == "unit_quantity":
        result = -.25 * (vectors[:, 68] < 0).astype("float32")
    else:
        raise ValueError("Unknown component role")
    result[unknown] = -.75
    return torch.from_numpy(result.astype("float32"))


def relation_features(groups, records, graph):
    fields = [records["field"][g["source_record_indices"][0]] if g["value"] is not None else None for g in groups["field"]]
    periods, dimensions, amounts = [groups[c] for c in CHANNELS[1:]]
    result = [np.zeros((len(fields), len(rows), 2), dtype="float32") for rows in (periods, dimensions, amounts)]
    membership = {(r["axis"], r["member"]) for r in graph["axis_member_hints"]}
    for f, field in enumerate(fields):
        if field is None:
            continue
        declared = field["metadata"].get("period_type")
        for p, period in enumerate(periods):
            if declared in {"instant", "duration"} and period["value"] is not None:
                result[0][f, p] = 1, float(declared == period["value"]["kind"])
        axes = graph["field_axis_hints"].get(field["value"], [])
        for d, dimension in enumerate(dimensions):
            value = dimension["value"]
            if value:
                result[1][f, d] = (sum(v["axis"] in axes for v in value) / len(value),
                                    sum((v["axis"], v["member"]) in membership for v in value) / len(value))
        for q, amount in enumerate(amounts):
            result[2][f, q] = unit_compatibility(field, amount)
    # Definition-linkbase absence remains a zero retrieval hint, not evidence
    # that a field/dimension combination is invalid.
    priors = [-result[0][..., 0] * (1 - result[0][..., 1]), np.zeros(result[1].shape[:2]),
              -result[2][..., 0] * (1 - result[2][..., 1])]
    return [torch.from_numpy(r) for r in result], [torch.from_numpy(p.astype("float32")) for p in priors]


def load_data():
    manifest_file = DEST / "features/manifest.json"
    manifest = json.loads(manifest_file.read_text())
    public_file = DEST / "public/witnesses.json"
    public = json.loads((DEST / "public/manifest.json").read_text())
    if not manifest["complete"] or sha(public_file) != manifest["witness_inventory_sha256"] or sha(public_file) != public["witness_inventory_sha256"]:
        raise ValueError("Complete common witnessed feature freeze required")
    for name, digest in manifest["files"].items():
        if sha(DEST / "features" / name) != digest:
            raise ValueError("Common witnessed features changed: " + name)
    task_file, record_file = INPUT / "candidates/tasks.json", INPUT / "features/records.json"
    if sha(task_file) != public["public_task_sha256"] or sha(record_file) != public["public_record_sha256"]:
        raise ValueError("Public target/value inventory changed")
    graph_file = NETWORK / "public/report_graphs.json"
    original_file = INPUT.parent / "joint_binding_v19/features/manifest.json"
    original_manifest = json.loads(original_file.read_text())
    v21_file = INPUT / "features/manifest.json"
    v21 = json.loads(v21_file.read_text())
    if sha(v21_file) != manifest["original_node_feature_manifest_sha256"] or sha(original_file) != v21["previous_state_feature_manifest_sha256"] or sha(graph_file) != original_manifest["public_graph_sha256"]:
        raise ValueError("Public relation graph provenance changed")
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
            if teacher["source_year"] != 2023:
                raise ValueError("Historical teacher year differs")
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
    prefixes_file = INPUT / "learning_full_transfer/feedback_prefixes.json"
    prefixes = json.loads(prefixes_file.read_text())
    design = json.loads((INPUT / "learning_full_transfer/design.json").read_text())
    if sha(prefixes_file) != design["feedback_prefix_sha256"]:
        raise ValueError("Frozen automatic-feedback prefixes changed")
    return tasks, inventory, packs, prefixes, {
        "feature_manifest_sha256": sha(manifest_file), "public_inventory_sha256": sha(public_file),
        "teacher_sha256": sha(teacher_file), "public_graph_sha256": sha(graph_file),
        "feedback_prefix_sha256": sha(prefixes_file)}

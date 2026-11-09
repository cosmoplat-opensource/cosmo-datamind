"""Held-out packs: the v26 pack layout over the held-out bank with the development-fitted priors added."""

import json

import numpy as np
import torch
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import historical_positive
from native_witness_data_v23 import component_prior, relation_features
from native_v27_data import PRIOR_SCALE
from native_v28_data import PAIR_SCALE, ROLE
from native_v29_data import FIELD_SCALE
from heldout_priors_v1 import BANK, HELD


def load_heldout():
    tasks = json.loads((BANK / "public/tasks.json").read_text())
    records = json.loads((BANK / "public/records.json").read_text())
    inventory = json.loads((BANK / "public/witnesses.json").read_text())
    graphs = json.loads((HELD / "schema/public/report_graphs.json").read_text())
    teachers = json.loads((HELD / "training_only/past_binding_feedback.json").read_text())
    import os
    structural = json.loads((BANK / os.environ.get("HELDOUT_STRUCTURAL", "prior_v27") / "structural_prior.json").read_text())
    pair = json.loads((BANK / "prior_v28/unit_quantity_pair_prior.json").read_text())
    field = json.loads((BANK / os.environ.get("HELDOUT_FIELD", "prior_v31") / "field_prior.json").read_text())
    contexts = torch.from_numpy(np.load(BANK / "features/context.npy"))
    packs = {}
    for i, (task, original, item) in enumerate(zip(tasks, records, inventory, strict=True)):
        groups = item["components"]
        with np.load(BANK / "features" / (task["task_id"] + ".npz")) as arrays:
            nodes = [torch.from_numpy(arrays[c]) for c in CHANNELS]
            indices = [torch.from_numpy(arrays[c + "_groups"]) for c in CHANNELS[1:]]
            priors = [component_prior(task, c, arrays[c], groups[c], None if j == 0 else arrays[c + "_groups"]) for j, c in enumerate(CHANNELS)]
            pool = torch.from_numpy(arrays["common_header_pool"])
        masks = [torch.zeros(len(groups[c]), dtype=torch.bool) for c in CHANNELS]
        if task["source_year"] == 2023:
            masks = [historical_positive(task, groups[c], teachers[task["task_id"]], c)[:-1] for c in CHANNELS]
        relations, pair_priors = relation_features(groups, original, graphs[task["report"]])
        counts = [torch.bincount(index, minlength=len(groups[c])) for c, index in zip(CHANNELS[1:], indices, strict=True)]
        # development-fitted priors
        priors[0] = priors[0] + FIELD_SCALE * torch.tensor(field[task["task_id"]]["field"], dtype=torch.float32)
        for j, c in ((1, "period"), (2, "dimensions")):
            priors[j] = priors[j] + PRIOR_SCALE[c] * torch.tensor(structural[task["task_id"]][c], dtype=torch.float32)[indices[j - 1]]
        table = np.array(pair[task["task_id"]]["pair_logits"], dtype="float32"); codes = pair[task["task_id"]]["field_codes"]
        matrix = np.stack([table[b, t] for b, t in codes])
        known_q = np.array([g["value"] is not None for g in groups["unit_quantity"]]); known_f = np.array([g["value"] is not None for g in groups["field"]])
        if known_q.any():
            matrix[:, ~known_q] = matrix[:, known_q].mean(axis=1, keepdims=True)
        if known_f.any():
            matrix[~known_f] = matrix[known_f].mean(axis=0, keepdims=True)
        matrix = matrix - matrix.max(axis=1, keepdims=True)
        pair_priors[ROLE] = pair_priors[ROLE] + PAIR_SCALE * torch.from_numpy(matrix)
        packs[i] = {"task_id": task["task_id"], "company": task["company"], "year": task["source_year"], "context": contexts[i], "nodes": nodes,
                    "groups": indices, "group_counts": counts, "prior": priors, "pool": pool, "relations": relations, "pair_prior": pair_priors,
                    "masks": masks, "factor_pairs": len(nodes[0]) * sum(len(n) for n in nodes[1:])}
    return tasks, inventory, packs

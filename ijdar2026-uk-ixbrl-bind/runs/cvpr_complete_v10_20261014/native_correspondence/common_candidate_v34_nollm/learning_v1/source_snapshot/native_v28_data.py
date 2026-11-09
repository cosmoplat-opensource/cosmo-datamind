"""v27 packs plus the v28 field x unit-quantity pair prior, common to every learner."""

import json

import numpy as np
import torch
from native_structural_prior_v28 import DEST as PAIR
from native_v26_bank import DEST as BANK
from native_v27_data import load_data as load_v27
from prepare import sha

PAIR_SCALE = 1.0
ROLE = 2  # unit_quantity among CHANNELS[1:]


def load_data():
    tasks, inventory, packs, prefixes, provenance = load_v27()
    manifest = json.loads((PAIR / "manifest.json").read_text())
    prior_file = PAIR / "unit_quantity_pair_prior.json"
    if sha(prior_file) != manifest["unit_quantity_pair_prior_sha256"] or manifest["structural_prior_v27_sha256"] != provenance["structural_prior_sha256"]:
        raise ValueError("Frozen v28 pair prior does not match the v27 structural prior")
    prior = json.loads(prior_file.read_text())
    for i, (task, item) in enumerate(zip(tasks, inventory, strict=True)):
        entry = prior[task["task_id"]]
        table = np.array(entry["pair_logits"], dtype="float32")  # balance x type x groups
        codes = entry["field_codes"]
        fields, amounts = item["components"]["field"], item["components"]["unit_quantity"]
        if len(codes) != len(fields) or table.shape[-1] != len(amounts):
            raise ValueError("Pair prior shape differs for " + task["task_id"])
        matrix = np.stack([table[b, t] for b, t in codes])  # fields x groups
        known_q = np.array([g["value"] is not None for g in amounts])
        known_f = np.array([g["value"] is not None for g in fields])
        if known_q.any():
            matrix[:, ~known_q] = matrix[:, known_q].mean(axis=1, keepdims=True)
        if known_f.any():
            matrix[~known_f] = matrix[known_f].mean(axis=0, keepdims=True)
        matrix = matrix - matrix.max(axis=1, keepdims=True)
        if packs[i]["pair_prior"][ROLE].shape != matrix.shape:
            raise ValueError("Pair prior does not align with the relation prior for " + task["task_id"])
        packs[i]["pair_prior"][ROLE] = packs[i]["pair_prior"][ROLE] + PAIR_SCALE * torch.from_numpy(matrix)
    provenance = dict(provenance, bank="common_candidate_v26+structural_prior_v27+pair_prior_v28",
                      unit_quantity_pair_prior_sha256=manifest["unit_quantity_pair_prior_sha256"], pair_prior_scale=PAIR_SCALE)
    return tasks, inventory, packs, prefixes, provenance

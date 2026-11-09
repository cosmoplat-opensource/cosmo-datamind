"""Load the frozen v26 bank plus the v27 structural column/row prior into v23-shaped packs.

Every learner receives the same additive structural prior on its period and
dimension unaries; nothing else differs from native_v26_data."""

import json

import torch
from native_binding_features_v17 import CHANNELS
from native_structural_prior_v27 import DEST as STRUCTURAL
from native_v26_bank import DEST as BANK
from native_v26_data import load_data as load_v26
from prepare import sha

PRIOR_SCALE = {"period": 1.0, "dimensions": 1.0}


def load_data():
    tasks, inventory, packs, prefixes, provenance = load_v26()
    manifest = json.loads((STRUCTURAL / "manifest.json").read_text())
    prior_file = STRUCTURAL / "structural_prior.json"
    if sha(prior_file) != manifest["structural_prior_sha256"] or manifest["bank_witness_inventory_sha256"] != sha(BANK / "public/witnesses.json"):
        raise ValueError("Frozen v27 structural prior does not match the v26 bank")
    prior = json.loads(prior_file.read_text())
    for i, (task, item) in enumerate(zip(tasks, inventory, strict=True)):
        groups = item["components"]
        for j, channel in enumerate(CHANNELS):
            if channel not in PRIOR_SCALE:
                continue
            values = torch.tensor(prior[task["task_id"]][channel], dtype=torch.float32)
            if len(values) != len(groups[channel]):
                raise ValueError("Structural prior group count differs for " + task["task_id"])
            packs[i]["prior"][j] = packs[i]["prior"][j] + PRIOR_SCALE[channel] * values[packs[i]["groups"][j - 1]]
    provenance = dict(provenance, bank="common_candidate_v26+structural_prior_v27",
                      structural_prior_sha256=manifest["structural_prior_sha256"], structural_prior_scale=PRIOR_SCALE)
    return tasks, inventory, packs, prefixes, provenance

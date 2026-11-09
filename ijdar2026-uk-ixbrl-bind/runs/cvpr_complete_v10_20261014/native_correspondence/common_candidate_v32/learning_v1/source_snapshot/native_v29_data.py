"""v28 packs plus the v29 common field prior."""

import json

import torch
from native_structural_prior_v29 import DEST as FIELD
from native_v28_data import load_data as load_v28
from prepare import sha

FIELD_SCALE = 1.0


def load_data():
    tasks, inventory, packs, prefixes, provenance = load_v28()
    manifest = json.loads((FIELD / "manifest.json").read_text())
    prior_file = FIELD / "field_prior.json"
    if sha(prior_file) != manifest["field_prior_sha256"] or manifest["structural_prior_v27_sha256"] != provenance["structural_prior_sha256"]:
        raise ValueError("Frozen v29 field prior does not match the v27 structural prior")
    prior = json.loads(prior_file.read_text())
    for i, (task, item) in enumerate(zip(tasks, inventory, strict=True)):
        values = torch.tensor(prior[task["task_id"]]["field"], dtype=torch.float32)
        if len(values) != len(item["components"]["field"]) or len(values) != len(packs[i]["prior"][0]):
            raise ValueError("Field prior group count differs for " + task["task_id"])
        packs[i]["prior"][0] = packs[i]["prior"][0] + FIELD_SCALE * values
    provenance = dict(provenance, bank=provenance["bank"] + "+field_prior_v29", field_prior_sha256=manifest["field_prior_sha256"], field_prior_scale=FIELD_SCALE)
    return tasks, inventory, packs, prefixes, provenance

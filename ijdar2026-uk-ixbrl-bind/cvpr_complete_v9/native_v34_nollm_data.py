"""v34 no-LLM packs: v30 bank with the v27 structural prior (no LLM), the v28 pair prior and the no-LLM linkbase field prior (prior_v34_nollm_field).  No language-model output enters this system."""

import json

import numpy as np
import torch
import native_v26_data
import native_v27_data
from native_structural_prior_v30 import PRIOR27, PRIOR28
from native_structural_prior_v34_nollm import DEST as FIELD34N
from native_structural_prior_v34 import NO_LLM
from native_v28_data import PAIR_SCALE, ROLE
from native_v29_data import FIELD_SCALE
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from prepare import sha


def load_data():
    native_v26_data.DEST, native_v26_data.ALIGN = BANK, PERCEPTION
    native_v27_data.STRUCTURAL, native_v27_data.BANK = PRIOR27, BANK
    tasks, inventory, packs, prefixes, provenance = native_v27_data.load_data()
    pair_manifest = json.loads((PRIOR28 / "manifest.json").read_text())
    pair = json.loads((PRIOR28 / "unit_quantity_pair_prior.json").read_text())
    if sha(PRIOR28 / "unit_quantity_pair_prior.json") != pair_manifest["unit_quantity_pair_prior_sha256"]:
        raise ValueError("Frozen v28 pair prior changed")
    field_manifest = json.loads((FIELD34N / "manifest.json").read_text())
    field = json.loads((FIELD34N / "field_prior.json").read_text())
    if sha(FIELD34N / "field_prior.json") != field_manifest["field_prior_sha256"]:
        raise ValueError("Frozen v34 no-LLM field prior changed")
    for i, (task, item) in enumerate(zip(tasks, inventory, strict=True)):
        entry = pair[task["task_id"]]
        table = np.array(entry["pair_logits"], dtype="float32")
        fields, amounts = item["components"]["field"], item["components"]["unit_quantity"]
        matrix = np.stack([table[b, t] for b, t in entry["field_codes"]])
        known_q = np.array([g["value"] is not None for g in amounts]); known_f = np.array([g["value"] is not None for g in fields])
        if known_q.any():
            matrix[:, ~known_q] = matrix[:, known_q].mean(axis=1, keepdims=True)
        if known_f.any():
            matrix[~known_f] = matrix[known_f].mean(axis=0, keepdims=True)
        matrix = matrix - matrix.max(axis=1, keepdims=True)
        packs[i]["pair_prior"][ROLE] = packs[i]["pair_prior"][ROLE] + PAIR_SCALE * torch.from_numpy(matrix)
        values = torch.tensor(field[task["task_id"]]["field"], dtype=torch.float32)
        if len(values) != len(packs[i]["prior"][0]):
            raise ValueError("Field prior group count differs for " + task["task_id"])
        packs[i]["prior"][0] = packs[i]["prior"][0] + FIELD_SCALE * values
    provenance = dict(provenance, bank="common_candidate_v30+structural_prior_v27+pair_prior_v28+field_prior_v34_nollm", perception="perception_v30",
                      structural_prior="prior_v27", unit_quantity_pair_prior_sha256=pair_manifest["unit_quantity_pair_prior_sha256"], field_prior_sha256=field_manifest["field_prior_sha256"],
                      llm_readings="none")
    return tasks, inventory, packs, prefixes, provenance

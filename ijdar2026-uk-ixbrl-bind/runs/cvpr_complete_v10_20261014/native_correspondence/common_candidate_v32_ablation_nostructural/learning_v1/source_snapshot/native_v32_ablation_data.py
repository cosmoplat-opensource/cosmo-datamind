"""v32 drop-one ablation loaders on the v30 bank (review item K5 / drop-one-prior column for Table 4).

VARIANT is fixed per generated script:
  "nopair"      v32 structural prior + v31 LLM field prior, WITHOUT the concept x unit-value pair prior (v28);
  "nollmfield"  v32 structural prior + v28 pair prior + v29 field prior (ontology disambiguation WITHOUT LLM ranks);
  "nofield"     v32 structural prior + v28 pair prior, no concept prior at all;
  "nostructural" v28 pair prior + v31 LLM field prior, no structural prior.
The full set (v32 structural + v28 pair + v31 field) is the v32 main run."""

import json

import numpy as np
import torch
import native_v26_data
import native_v27_data
from native_structural_prior_v30 import PRIOR28, PRIOR29
from native_structural_prior_v31 import DEST as FIELD31
from native_structural_prior_v32 import DEST as PRIOR32
from native_v28_data import PAIR_SCALE, ROLE
from native_v29_data import FIELD_SCALE
from native_v30_bank import DEST as BANK
from native_v30_bank import PERCEPTION
from prepare import sha

VARIANTS = {"nopair": (True, False, FIELD31), "nollmfield": (True, True, PRIOR29), "nofield": (True, True, None), "nostructural": (False, True, FIELD31)}


def add_pair(tasks, inventory, packs):
    manifest = json.loads((PRIOR28 / "manifest.json").read_text())
    pair = json.loads((PRIOR28 / "unit_quantity_pair_prior.json").read_text())
    if sha(PRIOR28 / "unit_quantity_pair_prior.json") != manifest["unit_quantity_pair_prior_sha256"]:
        raise ValueError("Frozen v28 pair prior changed")
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
    return manifest["unit_quantity_pair_prior_sha256"]


def add_field(tasks, packs, folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    field = json.loads((folder / "field_prior.json").read_text())
    if sha(folder / "field_prior.json") != manifest["field_prior_sha256"]:
        raise ValueError("Frozen field prior changed: " + str(folder))
    for i, task in enumerate(tasks):
        values = torch.tensor(field[task["task_id"]]["field"], dtype=torch.float32)
        if len(values) != len(packs[i]["prior"][0]):
            raise ValueError("Field prior group count differs for " + task["task_id"])
        packs[i]["prior"][0] = packs[i]["prior"][0] + FIELD_SCALE * values
    return manifest["field_prior_sha256"]


def load_variant(variant):
    structural, pair, field_folder = VARIANTS[variant]
    native_v26_data.DEST, native_v26_data.ALIGN = BANK, PERCEPTION
    if structural:
        native_v27_data.STRUCTURAL, native_v27_data.BANK = PRIOR32, BANK
        tasks, inventory, packs, prefixes, provenance = native_v27_data.load_data()
    else:
        tasks, inventory, packs, prefixes, provenance = native_v26_data.load_data()
    extra = {}
    if pair:
        extra["unit_quantity_pair_prior_sha256"] = add_pair(tasks, inventory, packs)
    if field_folder is not None:
        extra["field_prior_sha256"] = add_field(tasks, packs, field_folder); extra["field_prior"] = field_folder.name
    provenance = dict(provenance, bank="common_candidate_v30+v32_ablation_" + variant, perception="perception_v30", structural_prior="prior_v32" if structural else "none", **extra)
    return tasks, inventory, packs, prefixes, provenance

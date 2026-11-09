"""Historical-only complete semantic-tuple development with equal feedback.

Four trained/update controls share a candidate space, visible evidence and
public relationship hints. Unknown scope remains unknown; prediction of a
publisher semantic tuple never certifies the ownership of a visual header.
"""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path

import numpy as np
import torch
from torch import nn

from native_binding_expand_v17 import DEST
from native_binding_features_v17 import CHANNELS
from native_binding_prepare_v14 import DEST as TEACHERS
from native_field_learning_v12 import feedback_policies
from native_field_retrieval_v12 import DEST as FIELD
from prepare import save, sha


FOLDER = DEST / "learning_v1"
METHODS = ("public_binding_prior", "generic_joint", "factor_binding", "learned_semantic_memory", "frozen_semantic_memory")
SEEDS = (19, 41, 73)
POLICIES = ("hash_prefix", "public_information_prefix")
PRETRAIN_STEPS, LOCAL_STEPS, HIDDEN, K = 256, 64, 64, 8


class BindingMatcher(nn.Module):
    def __init__(self, method, df, dc):
        super().__init__()
        if method not in METHODS[1:]:
            raise ValueError("Unknown complete-binding model")
        self.method = method
        if method == "factor_binding":
            self.field = nn.Linear(df, HIDDEN)
            self.context = nn.Linear(dc, HIDDEN)
        else:
            self.first = nn.Linear(df+dc, HIDDEN)
        self.out = nn.Linear(HIDDEN, 1)
        nn.init.zeros_(self.out.weight)
        nn.init.zeros_(self.out.bias)

    def forward(self, fields, context, supports=None):
        if self.method == "factor_binding":
            return self.out(torch.tanh(self.field(fields))*torch.tanh(self.context(context))).flatten()
        if self.method == "generic_joint":
            # Exact affine decomposition of concatenation; avoid repeatedly
            # multiplying a shared context, without changing the baseline.
            first = nn.functional.linear(fields, self.first.weight[:, :fields.shape[1]], self.first.bias)
            first = first + nn.functional.linear(context, self.first.weight[:, fields.shape[1]:])
            return self.out(torch.tanh(first)).flatten()
        if not supports:
            return fields.new_zeros(len(fields)) + sum(p.sum()*0 for p in self.parameters())
        sf, sc = (torch.stack(v) for v in zip(*supports))
        nearest = torch.argsort((sc-context).square().mean(1), stable=True)[:K]
        # Equivalent linear decomposition avoids copying large repeated contexts.
        delta = (fields[:, None, :]-sf[nearest]).abs()
        wf, wc = self.first.weight[:, :fields.shape[1]], self.first.weight[:, fields.shape[1]:]
        first = nn.functional.linear(delta, wf) + nn.functional.linear((context-sc[nearest]).abs(), wc, self.first.bias)[None, :, :]
        values = self.out(torch.tanh(first)).squeeze(-1)
        return torch.logsumexp(values, 1)-math.log(len(nearest))


def historical_positive(task, records, teacher, channel):
    if task["source_year"] != 2023 or teacher["source_year"] != 2023 or task["task_id"] != teacher["task_id"]:
        raise ValueError("Only matching historical teachers may supervise bindings")
    binding = teacher.get("teacher_binding")
    expected = ({"unit": binding["unit"], "quantity": binding["quantity"]} if binding and channel == "unit_quantity"
                else binding.get(channel) if binding else None)
    if expected is None:
        return torch.zeros(len(records)+1, dtype=torch.bool)
    def equal(value):
        if value is None:
            return False
        if channel == "unit_quantity":
            return value["unit"] == expected["unit"] and Decimal(value["quantity"]) == Decimal(expected["quantity"])
        if channel == "dimensions":
            return sorted(value, key=lambda d: d["axis"]) == sorted(expected, key=lambda d: d["axis"])
        return value == expected
    return torch.tensor([equal(r["value"]) for r in records]+[False])


def prior(task, channel, vectors, rows):
    if channel == "field":
        old = vectors[:, :401]
        scores = .65*old[:, 397]+.25*old[:, 398]+.1*old[:, 399]
        if task["common_observation_failed"]:
            scores = .8*old[:, 398]+.2*old[:, 399]
        result = 4*(scores-scores[:-1].max())
        result[:-1] -= 2*old[:-1, 395]
    elif channel == "dimensions":
        result = 2*(vectors[:, 435]-vectors[:-1, 435].max())
        for i, row in enumerate(rows):
            if row["value"] == []:
                result[i] = 0 if task["dimension_visible_text"] == "unknown" else -.5
    elif channel == "period":
        result = -.25*vectors[:, 413]-3*(1-vectors[:, 414])
    else:
        result = -.25*(vectors[:, 404] < 0).float()
    result[-1] = -.75
    return result


def support_bank(pairs, packs):
    banks = {c: [] for c in CHANNELS}
    for i, channel in pairs:
        item = packs[i][channel]
        for j in torch.nonzero(item["positive"], as_tuple=True)[0].tolist():
            banks[channel].append((i, (item["candidates"][j], packs[i]["context"])))
    return banks


def active_pairs(indices, packs):
    return [(i, c) for i in indices for c in CHANNELS if packs[i][c]["positive"].any()]


def fit(model, local, replay, packs, banks, steps, seed):
    if not local:
        return {"optimizer_steps": 0, "sampled_local_component_cases": 0, "sampled_replay_component_cases": 0}
    rng = np.random.default_rng(seed+20261015)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    sampled_local = sampled_replay = 0
    model.train()
    for _ in range(steps):
        if replay:
            batch = [local[j] for j in rng.choice(len(local), min(4, len(local)), replace=False)]
            old = [replay[j] for j in rng.choice(len(replay), min(4, len(replay)), replace=False)]
        else:
            batch = [local[j] for j in rng.choice(len(local), min(8, len(local)), replace=False)]
            old = []
        sampled_local += len(batch)
        sampled_replay += len(old)
        optimizer.zero_grad()
        losses = []
        for i, c in batch+old:
            item = packs[i][c]
            memory = [v for j, v in banks[c] if j != i]
            logits = item["prior"] + model(item["candidates"], packs[i]["context"], memory)
            losses.append(torch.logsumexp(logits, 0)-torch.logsumexp(logits[item["positive"]], 0))
        torch.stack(losses).mean().backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5)
        optimizer.step()
    return {"optimizer_steps": steps, "sampled_local_component_cases": sampled_local, "sampled_replay_component_cases": sampled_replay}


def prediction(task, choices):
    unit = choices["unit_quantity"]
    binding = {"field": choices["field"], "period": choices["period"], "dimensions": choices["dimensions"],
               "entity": task["entity_candidates"][0]["entity"] if task["entity_candidates"] else None,
               "unit": unit["unit"] if unit else None, "quantity": unit["quantity"] if unit else None}
    return {"task_id": task["task_id"], "predicted_binding": binding,
            "semantic_tuple_complete": all(value is not None for value in binding.values()),
            "visual_header_ownership_certified": False, "scope_assignment": "unknown",
            "entity_from_common_report_metadata_not_learned_visual_identity": True}


def run():
    torch.set_num_threads(2)
    public_file = DEST / "public/tasks.json"
    feature_folder = DEST / "features"
    feature_file = feature_folder / "manifest.json"
    meta = json.loads(feature_file.read_text())
    if not meta["complete"] or sha(public_file) != meta["public_candidate_sha256"]:
        raise ValueError("Complete public candidate feature freeze required")
    if any(sha(feature_folder/name) != digest for name, digest in meta["files"].items()):
        raise ValueError("Shared candidate features changed")
    tasks = json.loads(public_file.read_text())
    source_file = FIELD / "public/tasks.json"
    sources = json.loads(source_file.read_text())
    if sha(source_file) != meta["original_native_public_sha256"] or [t["task_id"] for t in tasks] != json.loads((feature_folder / "ids.json").read_text()):
        raise ValueError("Original native target inventory changed")
    policies = feedback_policies(sources)
    save(FOLDER / "feedback_prefixes.json", policies)
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    teacher_manifest = json.loads((TEACHERS / "manifest.json").read_text())
    if sha(teacher_file) != teacher_manifest["teacher_sha256"]:
        raise ValueError("Historical full-tuple teachers changed")
    feedback = json.loads(teacher_file.read_text())
    records = json.loads((feature_folder / "records.json").read_text())
    contexts = torch.from_numpy(np.load(feature_folder / "context.npy"))
    packs = []
    for i, task in enumerate(tasks):
        item = {"context": contexts[i]}
        with np.load(feature_folder / (task["task_id"]+".npz")) as values:
            for channel in CHANNELS:
                array = torch.from_numpy(values[channel])
                positive = (historical_positive(task, records[i][channel], feedback[task["task_id"]], channel) if task["source_year"] == 2023
                            else torch.zeros(len(array), dtype=torch.bool))
                item[channel] = {"candidates": array, "prior": prior(task, channel, array, records[i][channel]), "positive": positive}
        packs.append(item)
    parameter_counts = {method: sum(p.numel() for p in BindingMatcher(method, meta["candidate_dimension"], meta["context_dimension"]).parameters())
                        for method in METHODS[1:]}
    design = {"methods": list(METHODS), "seeds": list(SEEDS), "policies": list(POLICIES), "automatic_feedback_budgets": [0, 5, 10],
              "channels": list(CHANNELS), "pretrain_steps": PRETRAIN_STEPS, "maximum_local_steps": LOCAL_STEPS,
              "optimizer": "AdamW", "learning_rate": .001, "weight_decay": .01, "gradient_clip": 5,
              "maximum_pretrain_component_cases_per_step": 8, "maximum_local_cases_per_step": 4,
              "maximum_nonlocal_replay_cases_per_local_step": 4, "memory_neighbors": K, "parameter_counts": parameter_counts,
              "own_company_excluded_from_pretraining": True, "local_prefixes_nested_and_same_for_every_control": True,
              "missing_correct_candidate_consumes_feedback_without_false_unknown_label": True,
              "one_feedback_case_contains_the_full_historical_semantic_tuple_for_all_controls": True,
              "human_runtime_confirmations": 0, "public_candidate_sha256": sha(public_file), "feature_manifest_sha256": sha(feature_file),
              "historical_teacher_sha256": sha(teacher_file), "feedback_prefix_sha256": sha(FOLDER / "feedback_prefixes.json"),
              "runner_sha256": sha(__file__), "future_reference_files_read": False,
              "entity_channel_common_metadata_assistance_not_visual_learning": True,
              "header_ownership_and_full_dts_not_certified": True,
              "development_after_v12_v15_and_v16_not_independent_native_test": True}
    design_file = FOLDER / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Frozen complete-binding learning design changed")
    save(design_file, design)
    (FOLDER / "runner_snapshot.py").write_bytes(Path(__file__).read_bytes())
    index = {t["task_id"]: i for i, t in enumerate(tasks)}
    for company in sorted(policies[POLICIES[0]]):
        global_indices = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
        future = [i for i, t in enumerate(tasks) if t["source_year"] != 2023 and t["company"] == company]
        global_pairs = active_pairs(global_indices, packs)
        banks = support_bank(global_pairs, packs)
        for seed in SEEDS:
            parents = {}
            for base in ("generic_joint", "factor_binding", "learned_semantic_memory"):
                path = FOLDER / "parents" / company / str(seed) / base
                path.mkdir(parents=True, exist_ok=True)
                weight, metadata = path / "weights.pt", path / "metadata.json"
                torch.manual_seed(seed)
                model = BindingMatcher(base, meta["candidate_dimension"], meta["context_dimension"])
                if metadata.exists():
                    record = json.loads(metadata.read_text())
                    if record["weights_sha256"] != sha(weight) or record["design_sha256"] != sha(design_file):
                        raise ValueError("Frozen complete-binding parent changed")
                    model.load_state_dict(torch.load(weight, weights_only=True))
                else:
                    exposure = fit(model, global_pairs, [], packs, banks, PRETRAIN_STEPS, seed)
                    torch.save(model.state_dict(), weight)
                    save(metadata, {"weights_sha256": sha(weight), "design_sha256": sha(design_file), "exposure": exposure,
                                    "historical_component_cases": len(global_pairs), "excluded_company": company})
                parents[base] = model
                print("Complete-binding parent", company, seed, base, flush=True)
            for policy in POLICIES:
                for budget in (0, 5, 10):
                    local_indices = [index[ident] for ident in policies[policy][company][str(budget)]]
                    if any(tasks[i]["source_year"] != 2023 or tasks[i]["company"] != company for i in local_indices):
                        raise ValueError("Local feedback crossed company/year boundary")
                    local_pairs = active_pairs(local_indices, packs)
                    supports = support_bank(global_pairs+local_pairs, packs)
                    for method in METHODS:
                        output = FOLDER / "predictions" / policy / company / method / str(seed) / (str(budget)+".json")
                        if output.exists():
                            old = json.loads(output.read_text())
                            if old["design_sha256"] != sha(design_file) or [r["task_id"] for r in old["predictions"]] != [tasks[i]["task_id"] for i in future]:
                                raise ValueError("Frozen complete-binding prediction changed")
                            continue
                        base = "learned_semantic_memory" if method == "frozen_semantic_memory" else method
                        model = deepcopy(parents[base]) if method != "public_binding_prior" else None
                        exposure = {"optimizer_steps": 0, "sampled_local_component_cases": 0, "sampled_replay_component_cases": 0}
                        if model is not None and budget and method != "frozen_semantic_memory":
                            exposure = fit(model, local_pairs, global_pairs, packs, supports, LOCAL_STEPS, seed)
                        predictions = []
                        if model is not None:
                            model.eval()
                        with torch.no_grad():
                            for i in future:
                                choices = {}
                                for channel in CHANNELS:
                                    item = packs[i][channel]
                                    scores = item["prior"]
                                    if model is not None:
                                        scores = scores + model(item["candidates"], packs[i]["context"], [v for _, v in supports[channel]])
                                    selected = int(torch.argmax(scores))
                                    choices[channel] = records[i][channel][selected]["value"] if selected < len(records[i][channel]) else None
                                predictions.append(prediction(tasks[i], choices))
                        save(output, {"design_sha256": sha(design_file), "company": company, "seed": seed, "method": method,
                                      "policy": policy, "automatic_feedback_cases": budget, "usable_local_component_cases": len(local_pairs),
                                      "exposure": exposure, "predictions": predictions, "future_reference_files_read": False})
            save(FOLDER / "progress.json", {"last_company": company, "last_seed": seed, "updated_utc": datetime.now(timezone.utc).isoformat(),
                                           "completed_prediction_files": len(list((FOLDER / "predictions").rglob("*.json")))})
    files = sorted((FOLDER / "predictions").rglob("*.json"))
    if len(files) != 1260:
        raise ValueError("Complete configuration inventory required before scoring")
    save(FOLDER / "manifest.json", {"complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
         "design_sha256": sha(design_file), "prediction_files": len(files),
         "predictions_sha256": {str(p.relative_to(FOLDER)): sha(p) for p in files},
         "future_reference_files_read": False, "independent_native_test_completed": False})
    print("Complete semantic-tuple development predictions frozen", len(files), flush=True)


if __name__ == "__main__":
    run()

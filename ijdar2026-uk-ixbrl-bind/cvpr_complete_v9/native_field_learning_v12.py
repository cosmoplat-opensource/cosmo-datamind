"""Historical-only native field correspondence with equal automatic feedback.

This development classification does not establish full visual binding,
header ownership, or independent-test superiority. Every control has the same
source-local public field cards, typed properties, native pixels and context.
Frozen memory is an efficient update control: its actual zero local gradient
steps are reported, rather than padded with ineffective optimization steps.
"""

from copy import deepcopy
import json
import math
from pathlib import Path

import numpy as np
import torch
from torch import nn

from native_correspondence_prepare_v10 import DEST as NATIVE
from native_field_retrieval_v12 import DEST
from prepare import save, sha

SEEDS = (19, 41, 73)
METHODS = ("generic_joint", "factor_field", "learned_semantic_memory", "frozen_semantic_memory")
POLICIES = ("hash_prefix", "public_information_prefix")
PRETRAIN_STEPS, LOCAL_STEPS, HIDDEN, K = 128, 64, 64, 8


class FieldMatcher(nn.Module):
    def __init__(self, method, df, dc):
        super().__init__()
        if method not in METHODS:
            raise ValueError("Unregistered native field control")
        self.method = method
        if method == "factor_field":
            self.field = nn.Sequential(nn.Linear(df, HIDDEN), nn.Tanh())
            self.context = nn.Sequential(nn.Linear(dc, HIDDEN), nn.Tanh())
            self.out = nn.Linear(HIDDEN, 1)
            nn.init.zeros_(self.out.weight)
            nn.init.zeros_(self.out.bias)
        else:
            self.net = nn.Sequential(nn.Linear(df+dc, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, 1))
            nn.init.zeros_(self.net[-1].weight)
            nn.init.zeros_(self.net[-1].bias)

    def forward(self, fields, context, supports=None):
        if self.method == "factor_field":
            return self.out(self.field(fields) * self.context(context)).flatten()
        if self.method == "generic_joint":
            return self.net(torch.cat((fields, context.expand(len(fields), -1)), 1)).flatten()
        if not supports:
            return fields.new_zeros(len(fields)) + sum(p.sum()*0 for p in self.parameters())
        sf, sc = (torch.stack(v) for v in zip(*supports))
        distance = (sc-context).square().mean(1)
        nearest = torch.argsort(distance, stable=True)[:K]
        pairs = torch.cat(((fields[:, None, :] - sf[nearest]).abs(),
                           (context-sc[nearest]).abs()[None, :, :].expand(len(fields), -1, -1)), 2)
        values = self.net(pairs.flatten(0, 1)).reshape(len(fields), -1)
        return torch.logsumexp(values, 1) - math.log(len(nearest))


def historical_positive(task, feedback):
    if task["source_year"] != 2023 or feedback["source_year"] != 2023 or task["task_id"] != feedback["task_id"]:
        raise ValueError("Only matching historical feedback can train correspondence")
    return torch.tensor([c["name"] == feedback["field_name"] and c["namespace"] == feedback["field_namespace"]
                         for c in task["field_candidates"]] + [False])


def supports_for(indices, packs):
    result = []
    for i in indices:
        positives = torch.nonzero(packs[i]["positive"], as_tuple=True)[0].tolist()
        result.extend((i, (packs[i]["fields"][j], packs[i]["context"])) for j in positives)
    return result


def fit(model, indices, packs, supports, steps, seed):
    active = [i for i in indices if packs[i]["positive"].any()]
    if not active:
        return 0
    rng = np.random.default_rng(seed + 20261014)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    model.train()
    for _ in range(steps):
        optimizer.zero_grad()
        losses = []
        for i in rng.choice(active, min(8, len(active)), replace=False).tolist():
            item = packs[i]
            memory = [s for j, s in supports if j != i]
            logits = item["prior"] + model(item["fields"], item["context"], memory)
            losses.append(torch.logsumexp(logits, 0) - torch.logsumexp(logits[item["positive"]], 0))
        torch.stack(losses).mean().backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5)
        optimizer.step()
    return steps


def feedback_policies(tasks):
    original = json.loads((NATIVE / "feedback_prefixes.json").read_text())
    result = {"hash_prefix": original, "public_information_prefix": {}}
    for company in sorted(original):
        past = [t for t in tasks if t["company"] == company and t["source_year"] == 2023]
        def information(t):
            obs = t.get("common_visual_observation") or {}
            uncertainty = sum(h["uncertain"] for h in obs.get("headers", []))
            return (-uncertainty, t["page_id"], t["task_id"])
        ordered, pages = [], set()
        available = sorted(past, key=information)
        while available:
            new = next((t for t in available if t["page_id"] not in pages), available[0])
            ordered.append(new["task_id"])
            pages.add(new["page_id"])
            available.remove(new)
        result["public_information_prefix"][company] = {str(k): ordered[:k] for k in (0, 5, 10)}
    for policy in result.values():
        for company, budgets in policy.items():
            if budgets["0"] or budgets["5"] != budgets["10"][:5] or len(set(budgets["10"])) != 10:
                raise ValueError("Feedback prefixes are not nested and unique")
    return result


def run():
    torch.set_num_threads(2)
    tasks_file = DEST / "public/tasks.json"
    tasks = json.loads(tasks_file.read_text())
    feature_meta_file = DEST / "features/manifest.json"
    feature_meta = json.loads(feature_meta_file.read_text())
    if not feature_meta["complete"] or feature_meta["public_tasks_sha256"] != sha(tasks_file):
        raise ValueError("Complete shared native feature freeze required")
    for name, digest in feature_meta["files"].items():
        if sha(DEST / "features" / name) != digest:
            raise ValueError("Frozen common field feature changed")
    if json.loads((DEST / "features/ids.json").read_text()) != [t["task_id"] for t in tasks]:
        raise ValueError("Common field feature order differs")
    # Freeze both public selection policies before reading any teacher labels.
    policies = feedback_policies(tasks)
    folder = DEST / "learning_v1"
    save(folder / "feedback_prefixes.json", policies)
    feedback_file = NATIVE / "training_only/past_feedback.json"
    feedback = json.loads(feedback_file.read_text())
    contexts = torch.from_numpy(np.load(DEST / "features/context.npy"))
    fields = np.load(DEST / "features/candidates.npz")
    packs = []
    for i, task in enumerate(tasks):
        candidates = torch.from_numpy(fields[task["task_id"]])
        scores = (.8*candidates[:, -3] + .2*candidates[:, -2] if task["common_observation_failed"] else
                  .65*candidates[:, -4] + .25*candidates[:, -3] + .1*candidates[:, -2])
        prior = 4*(scores-scores[:-1].max())
        prior[:-1] -= 2*candidates[:-1, 395]  # Public abstract declaration; same soft prior for every control.
        prior[-1] = -.75
        positive = (historical_positive(task, feedback[task["task_id"]]) if task["source_year"] == 2023
                    else torch.zeros(len(candidates), dtype=torch.bool))
        packs.append({"fields": candidates, "context": contexts[i], "prior": prior, "positive": positive})
    design = {"methods": list(METHODS), "seeds": list(SEEDS), "feedback_policies": list(POLICIES),
              "automatic_feedback_budgets": [0, 5, 10], "human_confirmations": 0,
              "pretrain_optimizer_steps": PRETRAIN_STEPS, "maximum_local_optimizer_steps": LOCAL_STEPS,
              "frozen_memory_actual_local_gradient_steps": 0, "optimizer": "AdamW", "learning_rate": .001,
              "weight_decay": .01, "minibatch_cases": 8, "memory_neighbors": K,
              "own_company_excluded_from_2023_pretraining": True,
              "missing_correct_candidate_consumes_feedback_budget_without_false_null_label": True,
              "all_models_receive_same_pixels_text_schema_types_and_feedback": True,
              "future_reference_files_read": False, "public_tasks_sha256": sha(tasks_file),
              "feature_manifest_sha256": sha(feature_meta_file), "past_feedback_sha256": sha(feedback_file),
              "feedback_prefix_sha256": sha(folder / "feedback_prefixes.json"), "runner_sha256": sha(__file__),
              "source_cohort_is_development_not_independent_test": True,
              "does_not_certify_quantity_unit_period_dimension_or_header_scope_binding": True}
    design_file = folder / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Frozen field-learning design changed")
    save(design_file, design)
    (folder / "runner_snapshot.py").write_bytes(Path(__file__).read_bytes())
    index = {t["task_id"]: i for i, t in enumerate(tasks)}
    for company in sorted(policies[POLICIES[0]]):
        global_indices = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
        future = [i for i, t in enumerate(tasks) if t["source_year"] != 2023 and t["company"] == company]
        global_supports = supports_for(global_indices, packs)
        for seed in SEEDS:
            for base_method in ("generic_joint", "factor_field", "learned_semantic_memory"):
                root = folder / "parents" / company / str(seed) / base_method
                root.mkdir(parents=True, exist_ok=True)
                weight, metadata = root / "weights.pt", root / "metadata.json"
                torch.manual_seed(seed)
                parent = FieldMatcher(base_method, feature_meta["field_dimension"], feature_meta["context_dimension"])
                if metadata.exists():
                    m = json.loads(metadata.read_text())
                    if m["weights_sha256"] != sha(weight) or m["design_sha256"] != sha(design_file):
                        raise ValueError("Frozen field parent checkpoint changed")
                    parent.load_state_dict(torch.load(weight, weights_only=True))
                else:
                    fit(parent, global_indices, packs, global_supports, PRETRAIN_STEPS, seed)
                    torch.save(parent.state_dict(), weight)
                    save(metadata, {"weights_sha256": sha(weight), "design_sha256": sha(design_file),
                                    "parameter_count": sum(p.numel() for p in parent.parameters()),
                                    "past_companies_only": True})
                for method in ([base_method, "frozen_semantic_memory"] if base_method == "learned_semantic_memory" else [base_method]):
                    for policy in POLICIES:
                        for budget in (0, 5, 10):
                            output = folder / "predictions" / policy / company / method / str(seed) / f"{budget}.json"
                            if output.exists():
                                continue
                            local = [index[k] for k in policies[policy][company][str(budget)]]
                            for i in local:
                                if tasks[i]["source_year"] != 2023 or tasks[i]["company"] != company:
                                    raise ValueError("Feedback prefix crossed company/time boundary")
                            memory = global_supports + supports_for(local, packs)
                            model = deepcopy(parent)
                            actual_steps = (fit(model, local, packs, memory, LOCAL_STEPS, seed)
                                            if budget and method != "frozen_semantic_memory" else 0)
                            model.eval()
                            predictions = []
                            with torch.no_grad():
                                for i in future:
                                    p = packs[i]
                                    logits = p["prior"] + model(p["fields"], p["context"], [s for _, s in memory])
                                    selected = int(logits.argmax())
                                    card = tasks[i]["field_candidates"][selected] if selected < len(tasks[i]["field_candidates"]) else None
                                    predictions.append({"task_id": tasks[i]["task_id"], "field": None if card is None else
                                                        {k: card[k] for k in ("field_id", "name", "namespace")}})
                            save(output, {"complete": True, "policy": policy, "company": company, "method": method,
                                          "seed": seed, "budget": budget, "predictions": predictions,
                                          "actual_local_gradient_steps": actual_steps,
                                          "usable_local_feedback": sum(bool(packs[i]["positive"].any()) for i in local),
                                          "parent_weight_sha256": sha(weight), "design_sha256": sha(design_file),
                                          "future_labels_read": False})
            print("Native no-oracle field correspondence", company, seed, "complete", flush=True)
    save(folder / "manifest.json", {"complete": True, "companies": len(policies[POLICIES[0]]),
                                    "design_sha256": sha(design_file), "all_budgets_policies_seeds_methods_preserved": True,
                                    "future_reference_labels_not_opened": True,
                                    "ontology_unique_or_full_visible_binding_advantage_established": False})


if __name__ == "__main__":
    run()

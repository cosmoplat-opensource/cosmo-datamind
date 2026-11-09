"""Equal-information program-correction learning with compositional controls.

All models have the same field, local context, pixels and candidate features.
All have the same other-company 2023 pretraining and local 0/5/10 feedback.
Future labels are unopened. This assisted diagnostic does not certify complete
visual bindings, calibrated coverage, or an independent main-method advantage.
"""

import json
import math
from decimal import Decimal
from pathlib import Path

import numpy as np
import torch
from crop_candidates_v3 import DEST
from prepare import save, sha
from torch import nn

SEEDS = (19, 41, 73)
METHODS = ("generic_joint", "factor_composition", "learned_semantic_memory")
PRETRAIN_STEPS = 256
LOCAL_STEPS = 128
HIDDEN = 64
MEMORY_NEIGHBORS = 8
TRAIN_BATCH_SIZE = 64


def positive_programs(task, event):
    if task["source_year"] != 2023 or event["source_year"] != 2023:
        raise ValueError("Future labels cannot train an applicability model")
    if task.get("task_id") != event.get("task_id"):
        raise ValueError("Feedback target identity differs")
    if not Decimal(event["answer"]).is_finite():
        raise ValueError("Nonfinite feedback quantity")
    return torch.tensor(
        [
            Decimal(c["computed_answer"]) == Decimal(event["answer"])
            for c in task["candidates"]
        ]
        + [False],
        dtype=torch.bool,
    )


def bag_loss(logits, positive):
    if not positive.any():
        return None
    return torch.logsumexp(logits, 0) - torch.logsumexp(logits[positive], 0)


class JointCorrection(nn.Module):
    def __init__(self, df, dc, dn):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(df + dc + dn, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, 1)
        )
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)

    def forward(self, f, c, n, supports=None):
        size = len(n)
        return self.net(
            torch.cat((f.expand(size, -1), c.expand(size, -1), n), 1)
        ).flatten()


class FactorCorrection(nn.Module):
    def __init__(self, df, dc, dn):
        super().__init__()
        self.field = nn.Sequential(nn.Linear(df, HIDDEN), nn.Tanh())
        self.context = nn.Sequential(nn.Linear(dc, HIDDEN), nn.Tanh())
        self.polarity = nn.Linear(HIDDEN, 1)
        self.parse = nn.Sequential(
            nn.Linear(HIDDEN + dn, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, 1)
        )
        nn.init.zeros_(self.polarity.weight)
        nn.init.zeros_(self.polarity.bias)
        nn.init.zeros_(self.parse[-1].weight)
        nn.init.zeros_(self.parse[-1].bias)

    def forward(self, f, c, n, supports=None):
        field = self.field(f)
        context = self.context(c)
        # A shared field/context interaction contributes only to the sign
        # component. Parsing has its own local-context dependency.
        sign = self.polarity(field * context).squeeze() * (n[:, 0] * 2 - 1)
        parse = self.parse(torch.cat((context.expand(len(n), -1), n), 1)).flatten()
        return sign + parse


class LearnedMemoryCorrection(nn.Module):
    def __init__(self, df, dc, dn):
        super().__init__()
        self.match = nn.Sequential(
            nn.Linear(df + dc + dn, HIDDEN), nn.Tanh(), nn.Linear(HIDDEN, 1)
        )
        nn.init.zeros_(self.match[-1].weight)
        nn.init.zeros_(self.match[-1].bias)

    def pairs(self, f, c, n, supports):
        sf, sc, sn = (torch.stack(block) for block in zip(*supports))
        distance = (sf - f).square().mean(1) + (sc - c).square().mean(1)
        nearest = torch.argsort(distance, stable=True)[:MEMORY_NEIGHBORS]
        sf, sc, sn = sf[nearest], sc[nearest], sn[nearest]
        return torch.cat(
            (
                (f - sf).abs().unsqueeze(0).expand(len(n), -1, -1),
                (c - sc).abs().unsqueeze(0).expand(len(n), -1, -1),
                (n[:, None, :] - sn).abs(),
            ),
            2,
        )

    def forward(self, f, c, n, supports=None):
        if not supports:
            return sum(p.sum() * 0 for p in self.parameters()) + n.new_zeros(len(n))
        # Confirmed program edits, not old numerical answers, are transferred.
        # Matching learns from the same field/type/context/candidate blocks.
        pairs = self.pairs(f, c, n, supports)
        scores = self.match(pairs.flatten(0, 1)).reshape(pairs.shape[:2])
        return torch.logsumexp(scores, 1) - math.log(pairs.shape[1])


def make(method, df, dc, dn, seed):
    torch.manual_seed(seed)
    return {
        "generic_joint": JointCorrection,
        "factor_composition": FactorCorrection,
        "learned_semantic_memory": LearnedMemoryCorrection,
    }[method](df, dc, dn)


def prior_and_candidates(task, observation, features):
    # A null option is present in both training and inference. Unsupported
    # numbers do not generate a false null teacher label.
    n = torch.tensor(features, dtype=torch.float32).reshape(-1, 12)
    if len(n) != len(task["candidates"]) or not torch.isfinite(n).all():
        raise ValueError("Candidate features differ from the frozen public pool")
    null = torch.zeros(1, 12)
    null[0, 10] = 1
    n = torch.cat((n, null), 0)
    selected = observation.get("candidate_index")
    if selected is not None and (
        type(selected) is not int or not 0 <= selected < len(task["candidates"])
    ):
        raise ValueError("Common selected program is outside the candidate pool")
    index = selected if selected is not None else len(task["candidates"])
    prior = torch.zeros(len(n))
    prior[index] = 2.2
    return prior, n


def train(model, indices, packs, supports, steps):
    active = [i for i in indices if packs[i]["positive"].any()]
    if not active:
        return 0
    counts = [len(packs[i]["candidate"]) for i in active]
    offsets = np.cumsum([0] + counts).tolist()
    fields = torch.cat(
        [packs[i]["field"].expand(n, -1) for i, n in zip(active, counts)]
    )
    contexts = torch.cat(
        [packs[i]["context"].expand(n, -1) for i, n in zip(active, counts)]
    )
    candidates = torch.cat([packs[i]["candidate"] for i in active])
    priors = torch.cat([packs[i]["prior"] for i in active])
    memory_pairs, memory_offsets = [], [0]
    if isinstance(model, LearnedMemoryCorrection):
        for i in active:
            pack = packs[i]
            # Memory queries exclude every candidate of their own confirmation.
            memory = [s for j, s in supports if j != i]
            pair = (
                model.pairs(pack["field"], pack["context"], pack["candidate"], memory)
                if memory
                else None
            )
            memory_pairs.append(pair)
            memory_offsets.append(
                memory_offsets[-1]
                + (pair.numel() // pair.shape[-1] if pair is not None else 0)
            )
        flat_pairs = (
            torch.cat([p.flatten(0, 1) for p in memory_pairs if p is not None])
            if any(p is not None for p in memory_pairs)
            else None
        )
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.01)
    schedule = np.random.default_rng(20261014)
    for _ in range(steps):
        optimizer.zero_grad()
        losses = []
        chosen = sorted(
            schedule.choice(
                len(active), min(TRAIN_BATCH_SIZE, len(active)), replace=False
            ).tolist()
        )
        selected = torch.cat([torch.arange(offsets[k], offsets[k + 1]) for k in chosen])
        if isinstance(model, LearnedMemoryCorrection):
            pair_chunks = [
                flat_pairs[memory_offsets[k] : memory_offsets[k + 1]]
                for k in chosen
                if memory_pairs[k] is not None
            ]
            scores = (
                model.match(torch.cat(pair_chunks)).flatten() if pair_chunks else None
            )
            deltas = []
            cursor = 0
            for k in chosen:
                pair = memory_pairs[k]
                if pair is None:
                    deltas.append(
                        sum(p.sum() * 0 for p in model.parameters())
                        + priors.new_zeros(counts[k])
                    )
                else:
                    size = memory_offsets[k + 1] - memory_offsets[k]
                    score = scores[cursor : cursor + size].reshape(pair.shape[:2])
                    cursor += size
                    deltas.append(torch.logsumexp(score, 1) - math.log(pair.shape[1]))
            delta = torch.cat(deltas)
        else:
            delta = model(fields[selected], contexts[selected], candidates[selected])
        logits = priors[selected] + delta
        cursor = 0
        for k in chosen:
            i = active[k]
            losses.append(
                bag_loss(logits[cursor : cursor + counts[k]], packs[i]["positive"])
            )
            cursor += counts[k]
        value = torch.stack(losses).mean()
        value.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
        optimizer.step()
    return len(active)


def validate_feedback_protocol(tasks, feedback, prefixes):
    indexed = {t["task_id"]: t for t in tasks}
    past = {t["task_id"] for t in tasks if t["source_year"] == 2023}
    if len(indexed) != len(tasks) or set(feedback) != past:
        raise ValueError("Feedback identities do not cover exactly the past inventory")
    if set(prefixes) != {t["company"] for t in tasks}:
        raise ValueError("Incomplete legal-entity feedback protocol")
    for ident, event in feedback.items():
        if event["task_id"] != ident or event["source_year"] != 2023:
            raise ValueError("Future or misidentified feedback event")
    for company, budgets in prefixes.items():
        if set(budgets) != {"0", "5", "10"}:
            raise ValueError("Registered local feedback budgets differ")
        for budget in (0, 5, 10):
            ids = budgets[str(budget)]
            if len(ids) != budget or len(ids) != len(set(ids)):
                raise ValueError("Feedback budget size or uniqueness differs")
            if any(i not in past or indexed[i]["company"] != company for i in ids):
                raise ValueError("Future or other-company local feedback")
        if budgets["5"] != budgets["10"][:5] or budgets["0"]:
            raise ValueError("Feedback prefixes are not nested")


def checkpoint_load(path, model, design_sha256):
    metadata = path.with_suffix(".json")
    if not path.exists() and not metadata.exists():
        return False
    if not path.exists() or not metadata.exists():
        raise RuntimeError("Incomplete frozen checkpoint pair")
    record = json.loads(metadata.read_text())
    if record["design_sha256"] != design_sha256 or record["checkpoint_sha256"] != sha(
        path
    ):
        raise RuntimeError("Frozen checkpoint provenance changed")
    model.load_state_dict(torch.load(path, weights_only=True))
    return True


def checkpoint_save(path, model, design_sha256):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pt.tmp")
    torch.save(model.state_dict(), temporary)
    temporary.replace(path)
    save(
        path.with_suffix(".json"),
        {"design_sha256": design_sha256, "checkpoint_sha256": sha(path)},
    )


def run():
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    task_file = DEST / "public/tasks.json"
    tasks = json.loads(task_file.read_text())
    fm = DEST / "features/manifest.json"
    meta = json.loads(fm.read_text())
    if meta["public_task_sha256"] != sha(task_file):
        raise RuntimeError("Frozen target features changed")
    for name, digest in meta["files"].items():
        if sha(DEST / "features" / name) != digest:
            raise RuntimeError("Frozen shared feature block changed")
    ids = json.loads((DEST / "features/ids.json").read_text())
    if ids != [t["task_id"] for t in tasks]:
        raise RuntimeError("Feature target order changed")
    fields = torch.tensor(np.load(DEST / "features/field.npy"))
    contexts = torch.tensor(np.load(DEST / "features/context.npy"))
    if (
        len(fields) != len(tasks)
        or len(contexts) != len(tasks)
        or not torch.isfinite(fields).all()
        or not torch.isfinite(contexts).all()
    ):
        raise RuntimeError("Malformed common public feature matrices")
    candidates = json.loads((DEST / "features/candidates.json").read_text())
    observations = json.loads((DEST / "features/observations.json").read_text())
    feedback_file = DEST / "training_only/past_feedback.json"
    feedback = json.loads(feedback_file.read_text())
    if any(e["source_year"] != 2023 for e in feedback.values()):
        raise RuntimeError("Past-only feedback contains future labels")
    prefixes_file = DEST / "feedback_prefixes.json"
    prefixes = json.loads(prefixes_file.read_text())
    validate_feedback_protocol(tasks, feedback, prefixes)
    index = {t["task_id"]: j for j, t in enumerate(tasks)}
    packs = []
    for j, task in enumerate(tasks):
        prior, numeric = prior_and_candidates(
            task, observations[task["task_id"]], candidates[task["task_id"]]
        )
        positive = (
            positive_programs(task, feedback[task["task_id"]])
            if task["source_year"] == 2023
            else torch.zeros_like(prior, dtype=torch.bool)
        )
        packs.append(
            {
                "field": fields[j],
                "context": contexts[j],
                "candidate": numeric,
                "prior": prior,
                "positive": positive,
            }
        )

    def support(indices):
        result = []
        for j in indices:
            pack = packs[j]
            for k in torch.nonzero(pack["positive"]).flatten().tolist():
                result.append(
                    (j, (pack["field"], pack["context"], pack["candidate"][k]))
                )
        return result

    df, dc = fields.shape[1], contexts.shape[1]
    dest = DEST / "program_correction_learning"
    counts = {
        m: sum(p.numel() for p in make(m, df, dc, 12, 19).parameters()) for m in METHODS
    }
    design = {
        "methods": METHODS,
        "seeds": SEEDS,
        "pretraining_steps": PRETRAIN_STEPS,
        "local_steps": LOCAL_STEPS,
        "maximum_bags_per_step": TRAIN_BATCH_SIZE,
        "common_bag_schedule_seed": 20261014,
        "learning_rate": 0.001,
        "weight_decay": 0.01,
        "hidden": HIDDEN,
        "learned_memory_neighbors": MEMORY_NEIGHBORS,
        "memory_support_selection": "common public field/context feature distance; excludes own confirmation in training",
        "pretraining": "only all other legal-entity 2023 records; leave current legal entity out",
        "local_automatic_feedback_budgets": [0, 5, 10],
        "actual_parameters": counts,
        "same_training_labels_features_candidates_and_base_prior_for_all_controls": True,
        "null_in_training_and_inference_denominators": True,
        "missing_gold_candidate_consumes_feedback_slot_but_has_no_false_label": True,
        "same_cached_visual_API_calls_no_method_specific_generation": True,
        "scope_boxes_and_roles_are_common_uncertified_model_proposals": True,
        "development_only_not_a_native_or_independent_main_advantage_test": True,
        "human_confirmations": 0,
        "feature_manifest_sha256": sha(fm),
        "public_tasks_sha256": sha(task_file),
        "past_feedback_sha256": sha(feedback_file),
        "feedback_prefix_sha256": sha(prefixes_file),
        "runner_sha256": sha(__file__),
    }
    design_path = dest / "design.json"
    if design_path.exists() and json.loads(design_path.read_text()) != json.loads(
        json.dumps(design)
    ):
        raise RuntimeError("Frozen learning design changed")
    save(design_path, design)
    design_hash = sha(design_path)
    snapshot = dest / "runner_snapshot.py"
    if snapshot.exists() and sha(snapshot) != sha(__file__):
        raise RuntimeError("Frozen runner snapshot changed")
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    rows = []
    ledger = []
    for company in sorted(prefixes):
        historical = [
            j
            for j, t in enumerate(tasks)
            if t["source_year"] == 2023 and t["company"] != company
        ]
        evaluation = [
            j
            for j, t in enumerate(tasks)
            if t["source_year"] in (2024, 2025) and t["company"] == company
        ]
        for j in evaluation:
            task = tasks[j]
            chosen = int(packs[j]["prior"].argmax())
            rows.append(
                {
                    "task_id": task["task_id"],
                    "company": company,
                    "method": "common_visual",
                    "budget": 0,
                    "seed": None,
                    "prediction": {
                        "candidate_index": chosen
                        if chosen < len(task["candidates"])
                        else None,
                        "forced_answer": task["candidates"][chosen]["computed_answer"]
                        if chosen < len(task["candidates"])
                        else None,
                    },
                }
            )
        for method in METHODS:
            for seed in SEEDS:
                model = make(method, df, dc, 12, seed)
                pretrained = dest / "pretrained" / f"{company}_{method}_{seed}.pt"
                if not checkpoint_load(pretrained, model, design_hash):
                    train(model, historical, packs, support(historical), PRETRAIN_STEPS)
                    checkpoint_save(pretrained, model, design_hash)
                state = {k: v.clone() for k, v in model.state_dict().items()}
                for budget in (0, 5, 10):
                    model.load_state_dict(state)
                    local = [index[i] for i in prefixes[company][str(budget)]]
                    if any(tasks[j]["source_year"] != 2023 for j in local):
                        raise RuntimeError("Future local feedback forbidden")
                    all_support = support(historical + local)
                    active = (
                        train(model, local, packs, all_support, LOCAL_STEPS)
                        if budget
                        else 0
                    )
                    with torch.no_grad():
                        memory = [s for _, s in all_support]
                        for j in evaluation:
                            task = tasks[j]
                            pack = packs[j]
                            delta = model(
                                pack["field"],
                                pack["context"],
                                pack["candidate"],
                                memory,
                            )
                            probabilities = (pack["prior"] + delta).softmax(0)
                            chosen = int(probabilities.argmax())
                            rows.append(
                                {
                                    "task_id": task["task_id"],
                                    "company": company,
                                    "method": method,
                                    "budget": budget,
                                    "seed": seed,
                                    "prediction": {
                                        "candidate_index": chosen
                                        if chosen < len(task["candidates"])
                                        else None,
                                        "forced_answer": task["candidates"][chosen][
                                            "computed_answer"
                                        ]
                                        if chosen < len(task["candidates"])
                                        else None,
                                        "probabilities": probabilities.tolist(),
                                        "probabilities_not_calibrated": True,
                                        "full_visual_binding_not_certified": True,
                                    },
                                }
                            )
                    cp = dest / "checkpoints" / f"{company}_{budget}_{method}_{seed}.pt"
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    checkpoint_save(cp, model, design_hash)
                    ledger.append(
                        {
                            "company": company,
                            "method": method,
                            "seed": seed,
                            "budget": budget,
                            "other_company_pretraining_slots": len(historical),
                            "usable_pretraining_program_bags": sum(
                                packs[j]["positive"].any().item() for j in historical
                            ),
                            "local_feedback_slots": len(local),
                            "usable_local_program_bags": active,
                            "checkpoint_sha256": sha(cp),
                            "pretrained_sha256": sha(pretrained),
                        }
                    )
                    save(
                        dest / "progress.json",
                        {"models": len(ledger), "expected_models": len(prefixes) * 27},
                    )
                    print(
                        "Learned program applicability",
                        company,
                        method,
                        seed,
                        budget,
                        "local usable",
                        active,
                        flush=True,
                    )
    expected = sum(t["source_year"] in (2024, 2025) for t in tasks) * 28
    if len(rows) != expected:
        raise RuntimeError("Incomplete equal-input program prediction matrix")
    save(dest / "predictions.json", rows)
    save(dest / "training_ledger.json", ledger)
    save(
        dest / "inference_manifest.json",
        {
            "complete": True,
            "expected_predictions": expected,
            "predictions_sha256": sha(dest / "predictions.json"),
            "design_sha256": sha(design_path),
            "public_tasks_sha256": sha(task_file),
            "future_reference_or_scoring_files_read": False,
            "human_confirmations": 0,
        },
    )


if __name__ == "__main__":
    run()

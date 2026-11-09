"""Resumable joint binding/year-link optimization with actual work accounting.

Case and optimizer budgets can be matched exactly. Registered dense matrix
FLOPs and observed runtime are measured separately; this is not a certificate
of equal total FLOPs, scalar operations or wall time across architectures.
"""

import hashlib
import json
import time

import numpy as np
import torch
from native_scope_learning_v23 import bank_for
from torch import nn
from torch.utils.flop_counter import FlopCounterMode

from semantic_star_v23 import partial_semantic_nll


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def configure_auxiliary(model, auxiliary):
    active, index = auxiliary["active"], auxiliary["index"]
    if not active or len(set(active)) != len(active):
        raise ValueError("Unique observed auxiliary source cases are required")
    if any(i < 0 or i >= len(index) or index[i]["source_year"] != 2023
           or index[i]["company"] == auxiliary["excluded_company"] for i in active):
        raise ValueError("An own-company or future auxiliary source entered training")
    model.configure_auxiliary(auxiliary["inputs"][active], auxiliary["labels"][active], active)


def validate_binding_sources(local, replay, packs, auxiliary, stage):
    if stage not in {"parent", "local"}:
        raise ValueError("Parent or local optimization stage required")
    bank = bank_for(local + replay, packs)
    company = auxiliary["excluded_company"]
    if stage == "parent" and (replay or any(packs[i]["company"] == company for i in local)):
        raise ValueError("Parent binding labels must exclude the held company")
    if stage == "local" and (any(packs[i]["company"] != company for i in local)
                             or any(packs[i]["company"] == company for i in replay)):
        raise ValueError("Local feedback and other-company replay crossed source boundaries")
    return bank


def fit_year(model, local, replay, packs, auxiliary, steps, seed, *, stage,
             auxiliary_weight=1., resume=None, design_sha=None, progress=None):
    bank = validate_binding_sources(local, replay, packs, auxiliary, stage)
    configure_auxiliary(model, auxiliary)
    active = [i for i in local if any(mask.any() for mask in packs[i]["masks"])]
    older = [i for i in replay if any(mask.any() for mask in packs[i]["masks"])]
    exposure = {"optimizer_steps": 0, "sampled_local_cases": 0, "sampled_replay_cases": 0,
                "sampled_witness_factor_pairs": 0, "sampled_auxiliary_year_cases": 0,
                "counted_registered_dense_flops": 0, "wall_seconds": 0.,
                "case_sequence_sha256": None, "auxiliary_case_sequence_sha256": None,
                "auxiliary_source_cases_available": len(auxiliary["active"]),
                "extra_own_company_auxiliary_cases": 0}
    if not active:
        model.clear_auxiliary_cache()
        return exposure
    if auxiliary_weight < 0:
        raise ValueError("Auxiliary loss weight cannot be negative")
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    rng = np.random.default_rng(seed + 20261016)
    aux_rng = np.random.default_rng(seed + 20261017)
    source_key = digest({"company": auxiliary["excluded_company"], "active": auxiliary["active"],
                         "feature_manifest_sha256": auxiliary["feature_manifest_sha256"],
                         "binding_local": local, "binding_replay": replay})
    protocol = {"method": model.method, "steps": steps, "stage": stage, "auxiliary_weight": auxiliary_weight,
                "seed": seed, "source_key": source_key, "design_sha256": design_sha}
    sequence, aux_sequence, start = [], [], 0
    if resume is not None and resume.exists():
        saved = torch.load(resume, weights_only=True)
        if saved["protocol"] != protocol:
            raise ValueError("Partial joint optimizer belongs to another source/budget/design")
        model.load_state_dict(saved["model"])
        optimizer.load_state_dict(saved["optimizer"])
        rng.bit_generator.state = saved["rng_state"]
        aux_rng.bit_generator.state = saved["aux_rng_state"]
        exposure, sequence, aux_sequence, start = saved["exposure"], saved["sequence"], saved["aux_sequence"], saved["step"]
        if start > steps:
            raise ValueError("Partial joint optimizer exceeds the planned step budget")
    begin, elapsed = time.perf_counter(), exposure["wall_seconds"]
    model.train()
    try:
        for step in range(start, steps):
            batch = rng.choice(active, min(4 if older else 8, len(active)), replace=False).tolist()
            old = rng.choice(older, min(4, len(older)), replace=False).tolist() if older else []
            aux_ids = aux_rng.choice(auxiliary["active"], min(32, len(auxiliary["active"])), replace=False).tolist()
            with FlopCounterMode(display=False) as counter:
                optimizer.zero_grad()
                cache = model.memory_cache(bank)
                losses = []
                for i in batch + old:
                    field, pairs, _ = model(packs[i], cache, exclude=i)
                    loss = partial_semantic_nll(field, pairs, packs[i]["masks"])
                    if loss is None or not torch.isfinite(loss):
                        raise ValueError("Unobserved or nonfinite binding loss")
                    losses.append(loss)
                binding_loss = torch.stack(losses).mean()
                auxiliary_loss = model.auxiliary_loss(auxiliary["inputs"][aux_ids], auxiliary["labels"][aux_ids],
                                                      torch.tensor(aux_ids, dtype=torch.long))
                loss = binding_loss + auxiliary_weight * auxiliary_loss
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite joint binding/year loss")
                loss.backward()
                if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                    raise ValueError("Nonfinite joint binding/year gradient")
                nn.utils.clip_grad_norm_(model.parameters(), 5)
                optimizer.step()
                model.clear_auxiliary_cache()
            counted_flops = counter.get_total_flops()
            if counted_flops <= 0:
                raise ValueError("Actual registered dense computation was not counted")
            exposure["optimizer_steps"] = step + 1
            exposure["sampled_local_cases"] += len(batch)
            exposure["sampled_replay_cases"] += len(old)
            exposure["sampled_witness_factor_pairs"] += sum(packs[i]["factor_pairs"] for i in batch + old)
            exposure["sampled_auxiliary_year_cases"] += len(aux_ids)
            exposure["counted_registered_dense_flops"] += int(counted_flops)
            sequence.append(batch + old)
            aux_sequence.append(aux_ids)
            if (step + 1) % 32 == 0 or step + 1 == steps:
                exposure["wall_seconds"] = elapsed + time.perf_counter() - begin
                exposure["case_sequence_sha256"] = digest(sequence)
                exposure["auxiliary_case_sequence_sha256"] = digest(aux_sequence)
                if resume is not None:
                    resume.parent.mkdir(parents=True, exist_ok=True)
                    temporary = resume.with_suffix(".tmp")
                    torch.save({"protocol": protocol, "step": step + 1, "model": model.state_dict(),
                                "optimizer": optimizer.state_dict(), "rng_state": rng.bit_generator.state,
                                "aux_rng_state": aux_rng.bit_generator.state, "exposure": exposure,
                                "sequence": sequence, "aux_sequence": aux_sequence}, temporary)
                    temporary.replace(resume)
                if progress:
                    progress(step + 1, exposure)
    finally:
        model.clear_auxiliary_cache()
    return exposure

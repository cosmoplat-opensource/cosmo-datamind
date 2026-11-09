"""Same-input development controls for damaging small-feedback updates.

Reuse every v12 public feature, prefix and historical-only parent checkpoint.
Replay changes the local minibatch: at most four local and four previously
available nonlocal 2023 examples. No new visual calls or semantic references
enter inference. This is a field-channel study, not full binding evaluation.
"""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from native_correspondence_prepare_v10 import DEST as NATIVE
from native_field_learning_v12 import FieldMatcher, historical_positive, SEEDS, POLICIES
from native_field_retrieval_v12 import DEST as SOURCE
from prepare import save, sha


DEST = NATIVE / "field_replay_v15"
METHODS = ("public_retrieval", "public_soft_prior", "legacy_joint", "legacy_factor",
           "frozen_semantic_memory", "replay_joint", "replay_factor")
STEPS = 64


def replay_fit(model, local, global_indices, packs, company, seed, steps=STEPS):
    for i in local + global_indices:
        if not packs[i]["past"]:
            raise ValueError("Later-year examples cannot enter replay")
    if any(packs[i]["company"] != company for i in local) or any(packs[i]["company"] == company for i in global_indices):
        raise ValueError("Company boundary crossed by replay samples")
    local = [i for i in local if packs[i]["positive"].any()]
    history = [i for i in global_indices if packs[i]["positive"].any()]
    if not local:
        return {"steps": 0, "local_example_exposures": 0, "historical_example_exposures": 0}
    if not history:
        raise ValueError("Nonlocal replay examples are required")
    rng = np.random.default_rng(seed + 20261014)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    local_exposures = historical_exposures = 0
    model.train()
    for _ in range(steps):
        a = rng.choice(local, min(4, len(local)), replace=False).tolist()
        b = rng.choice(history, min(4, len(history)), replace=False).tolist()
        optimizer.zero_grad()
        losses = []
        for i in a + b:
            item = packs[i]
            logits = item["prior"] + model(item["fields"], item["context"])
            losses.append(torch.logsumexp(logits, 0) - torch.logsumexp(logits[item["positive"]], 0))
        torch.stack(losses).mean().backward()
        nn.utils.clip_grad_norm_(model.parameters(), 5)
        optimizer.step()
        local_exposures += len(a)
        historical_exposures += len(b)
    return {"steps": steps, "local_example_exposures": local_exposures,
            "historical_example_exposures": historical_exposures}


def run():
    torch.set_num_threads(2)
    old = SOURCE / "learning_v1"
    feature_file = SOURCE / "features/manifest.json"
    feature = json.loads(feature_file.read_text())
    source_design_file = old / "design.json"
    source_design = json.loads(source_design_file.read_text())
    if not json.loads((old / "manifest.json").read_text())["complete"] or not feature["complete"]:
        raise ValueError("Complete historical-only parent experiment required")
    tasks_file = SOURCE / "public/tasks.json"
    if sha(tasks_file) != source_design["public_tasks_sha256"]:
        raise ValueError("Frozen public targets changed")
    for name, digest in feature["files"].items():
        if sha(SOURCE / "features" / name) != digest:
            raise ValueError("Shared v12 feature file changed")
    prefix_file = old / "feedback_prefixes.json"
    if sha(prefix_file) != source_design["feedback_prefix_sha256"]:
        raise ValueError("Frozen feedback prefixes changed")
    feedback_file = NATIVE / "training_only/past_feedback.json"
    if sha(feedback_file) != source_design["past_feedback_sha256"]:
        raise ValueError("Historical-only feedback changed")
    tasks = json.loads(tasks_file.read_text())
    policies = json.loads(prefix_file.read_text())
    feedback = json.loads(feedback_file.read_text())
    contexts = torch.from_numpy(np.load(SOURCE / "features/context.npy"))
    fields = np.load(SOURCE / "features/candidates.npz")
    packs = []
    for i, task in enumerate(tasks):
        candidates = torch.from_numpy(fields[task["task_id"]])
        scores = (.8*candidates[:, -3] + .2*candidates[:, -2] if task["common_observation_failed"] else
                  .65*candidates[:, -4] + .25*candidates[:, -3] + .1*candidates[:, -2])
        prior = 4*(scores-scores[:-1].max())
        prior[:-1] -= 2*candidates[:-1, 395]
        prior[-1] = -.75
        positive = (historical_positive(task, feedback[task["task_id"]]) if task["source_year"] == 2023
                    else torch.zeros(len(candidates), dtype=torch.bool))
        packs.append({"fields": candidates, "context": contexts[i], "prior": prior,
                      "positive": positive, "past": task["source_year"] == 2023, "company": task["company"]})
    design = {"methods": list(METHODS), "seeds": list(SEEDS), "feedback_policies": list(POLICIES),
              "automatic_feedback_budgets": [0, 5, 10], "local_optimizer_steps": STEPS,
              "maximum_local_examples_per_step": 4, "maximum_nonlocal_history_examples_per_step": 4,
              "source_design_sha256": sha(source_design_file), "public_tasks_sha256": sha(tasks_file),
              "feature_manifest_sha256": sha(feature_file), "feedback_prefix_sha256": sha(prefix_file),
              "past_feedback_sha256": sha(feedback_file), "runner_sha256": sha(__file__),
              "no_new_visual_observations_or_feature_encoder_training": True,
              "all_controls_have_the_same_historical_feedback_available": True,
              "future_reference_labels_used_for_training": False, "human_confirmations": 0,
              "developed_after_v12_development_results": True,
              "full_binding_or_independent_test_experiment": False}
    design_file = DEST / "design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Frozen replay design changed")
    save(design_file, design)
    save(DEST / "feedback_prefixes.json", policies)
    (DEST / "runner_snapshot.py").write_bytes(Path(__file__).read_bytes())
    index = {t["task_id"]: i for i, t in enumerate(tasks)}
    companies = sorted(policies[POLICIES[0]])
    completed = 0
    for company in companies:
        global_indices = [i for i, t in enumerate(tasks) if t["source_year"] == 2023 and t["company"] != company]
        future = [i for i, t in enumerate(tasks) if t["source_year"] != 2023 and t["company"] == company]
        for seed in SEEDS:
            parents = {}
            for base in ("generic_joint", "factor_field"):
                root = old / "parents" / company / str(seed) / base
                metadata = json.loads((root / "metadata.json").read_text())
                weight = root / "weights.pt"
                if metadata["weights_sha256"] != sha(weight) or metadata["design_sha256"] != sha(source_design_file):
                    raise ValueError("Historical-only parent checkpoint changed")
                model = FieldMatcher(base, feature["field_dimension"], feature["context_dimension"])
                model.load_state_dict(torch.load(weight, weights_only=True))
                parents[base] = model, metadata["weights_sha256"]
            for policy in POLICIES:
                for budget in (0, 5, 10):
                    local = [index[i] for i in policies[policy][company][str(budget)]]
                    for method in METHODS:
                        output = DEST / "predictions" / policy / company / method / str(seed) / f"{budget}.json"
                        if output.exists():
                            body = json.loads(output.read_text())
                            if not body["complete"] or body["design_sha256"] != sha(design_file):
                                raise ValueError("Existing replay prediction incomplete or changed")
                            continue
                        provenance = {}
                        exposures = {"steps": 0, "local_example_exposures": 0, "historical_example_exposures": 0}
                        parent_hash = None
                        if method in ("legacy_joint", "legacy_factor", "frozen_semantic_memory"):
                            base = {"legacy_joint": "generic_joint", "legacy_factor": "factor_field"}.get(method, method)
                            file = old / "predictions" / policy / company / base / str(seed) / f"{budget}.json"
                            body = json.loads(file.read_text())
                            if not body["complete"] or body["design_sha256"] != sha(source_design_file):
                                raise ValueError("Legacy prediction changed")
                            predictions = body["predictions"]
                            actual_steps = body["actual_local_gradient_steps"]
                            usable = body["usable_local_feedback"]
                            parent_hash = body["parent_weight_sha256"]
                            provenance = {"reused_v12_prediction_file": str(file), "reused_v12_prediction_sha256": sha(file)}
                        else:
                            model = None
                            if method.startswith("replay_"):
                                base = "generic_joint" if method == "replay_joint" else "factor_field"
                                parent, parent_hash = parents[base]
                                model = deepcopy(parent)
                                if budget:
                                    exposures = replay_fit(model, local, global_indices, packs, company, seed)
                                model.eval()
                                if exposures["steps"]:
                                    file = DEST / "checkpoints" / policy / company / method / str(seed) / f"{budget}.pt"
                                    file.parent.mkdir(parents=True, exist_ok=True)
                                    torch.save(model.state_dict(), file)
                                    provenance = {"local_checkpoint": str(file), "local_checkpoint_sha256": sha(file)}
                            actual_steps = exposures["steps"]
                            usable = sum(bool(packs[i]["positive"].any()) for i in local) if model is not None else 0
                            predictions = []
                            with torch.no_grad():
                                for i in future:
                                    item = packs[i]
                                    selected = (0 if method == "public_retrieval" else int((item["prior"] +
                                                (model(item["fields"], item["context"]) if model is not None else 0)).argmax()))
                                    card = tasks[i]["field_candidates"][selected] if selected < len(tasks[i]["field_candidates"]) else None
                                    predictions.append({"task_id": tasks[i]["task_id"], "field": None if card is None else
                                                        {k: card[k] for k in ("field_id", "name", "namespace")}})
                        if len(predictions) != len(future) or {p["task_id"] for p in predictions} != {tasks[i]["task_id"] for i in future}:
                            raise ValueError("Future replay denominator changed")
                        save(output, {"complete": True, "policy": policy, "company": company, "method": method,
                                      "seed": seed, "budget": budget, "predictions": predictions,
                                      "actual_local_gradient_steps": actual_steps, "usable_local_feedback": usable,
                                      "parent_weight_sha256": parent_hash, "design_sha256": sha(design_file),
                                      "future_labels_read": False, "replay_exposures": exposures, **provenance})
                        completed += 1
            save(DEST / "progress.json", {"updated_utc": datetime.now(timezone.utc).isoformat(),
                                          "last_completed_company": company, "last_completed_seed": seed,
                                          "new_prediction_files_this_session": completed,
                                          "expected_prediction_files": len(companies)*len(SEEDS)*len(POLICIES)*3*len(METHODS)})
            print("Replay controls", company, seed, "complete", flush=True)
    save(DEST / "manifest.json", {"complete": True, "companies": len(companies), "design_sha256": sha(design_file),
                                  "all_budgets_policies_seeds_methods_preserved": True,
                                  "future_reference_labels_not_opened_by_runner": True,
                                  "ontology_unique_or_full_binding_advantage_established": False})


if __name__ == "__main__":
    run()

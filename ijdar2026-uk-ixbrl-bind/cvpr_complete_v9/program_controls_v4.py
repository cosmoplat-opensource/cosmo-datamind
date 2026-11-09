"""Stronger generic controls and an interaction ablation for the v3 diagnostic.

Frozen v3 composition/memory predictions are reused unchanged. New controls
receive identical input blocks, past-only labels, bag schedules and steps.
Future development labels are not read by this runner.
"""

import json
from pathlib import Path

import numpy as np
import program_scope_learning_v3 as core
import torch
from crop_candidates_v3 import DEST as COMMON
from prepare import save, sha
from torch import nn

DEST = COMMON / "mechanism_controls_v4"
METHODS = (
    "factor_composition",
    "learned_semantic_memory",
    "capacity_matched_joint",
    "generic_bilinear_joint",
    "additive_composition",
)
NEW_METHODS = METHODS[2:]
SEEDS = core.SEEDS


class CapacityMatchedJoint(nn.Module):
    def __init__(self, df, dc, dn):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(df + dc + dn, 67), nn.Tanh(), nn.Linear(67, 1)
        )
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)

    def forward(self, f, c, n, supports=None):
        return self.net(
            torch.cat((f.expand(len(n), -1), c.expand(len(n), -1), n), 1)
        ).flatten()


class GenericBilinearJoint(nn.Module):
    def __init__(self, df, dc, dn):
        super().__init__()
        self.field = nn.Sequential(nn.Linear(df, 64), nn.Tanh())
        self.context = nn.Sequential(nn.Linear(dc, 64), nn.Tanh())
        self.joint = nn.Sequential(
            nn.Linear(3 * 64 + dn, 64), nn.Tanh(), nn.Linear(64, 1)
        )
        nn.init.zeros_(self.joint[-1].weight)
        nn.init.zeros_(self.joint[-1].bias)

    def forward(self, f, c, n, supports=None):
        field = self.field(f).expand(len(n), -1)
        context = self.context(c).expand(len(n), -1)
        return self.joint(torch.cat((field, context, field * context, n), 1)).flatten()


class AdditiveComposition(core.FactorCorrection):
    def forward(self, f, c, n, supports=None):
        field = self.field(f)
        context = self.context(c)
        sign = self.polarity(field + context).squeeze() * (n[:, 0] * 2 - 1)
        parse = self.parse(torch.cat((context.expand(len(n), -1), n), 1)).flatten()
        return sign + parse


def make(method, df, dc, dn, seed):
    torch.manual_seed(seed)
    return {
        "capacity_matched_joint": CapacityMatchedJoint,
        "generic_bilinear_joint": GenericBilinearJoint,
        "additive_composition": AdditiveComposition,
    }[method](df, dc, dn)


def run():
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    task_file = COMMON / "public/tasks.json"
    features_file = COMMON / "features/manifest.json"
    tasks = json.loads(task_file.read_text())
    features = json.loads(features_file.read_text())
    if features["public_task_sha256"] != sha(task_file):
        raise RuntimeError("Frozen target features changed")
    for name, digest in features["files"].items():
        if sha(COMMON / "features" / name) != digest:
            raise RuntimeError("Common feature block changed")
    if json.loads((COMMON / "features/ids.json").read_text()) != [
        t["task_id"] for t in tasks
    ]:
        raise RuntimeError("Common feature target order changed")
    fields = torch.tensor(np.load(COMMON / "features/field.npy"))
    contexts = torch.tensor(np.load(COMMON / "features/context.npy"))
    if not torch.isfinite(fields).all() or not torch.isfinite(contexts).all():
        raise RuntimeError("Nonfinite common features")
    candidates = json.loads((COMMON / "features/candidates.json").read_text())
    observations = json.loads((COMMON / "features/observations.json").read_text())
    feedback_file = COMMON / "training_only/past_feedback.json"
    feedback = json.loads(feedback_file.read_text())
    prefixes_file = COMMON / "feedback_prefixes.json"
    prefixes = json.loads(prefixes_file.read_text())
    core.validate_feedback_protocol(tasks, feedback, prefixes)
    index = {t["task_id"]: j for j, t in enumerate(tasks)}
    packs = []
    for j, task in enumerate(tasks):
        prior, numeric = core.prior_and_candidates(
            task, observations[task["task_id"]], candidates[task["task_id"]]
        )
        positive = (
            core.positive_programs(task, feedback[task["task_id"]])
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

    original = COMMON / "program_correction_learning"
    source_manifest = json.loads((original / "inference_manifest.json").read_text())
    original_predictions_file = original / "predictions.json"
    original_design_file = original / "design.json"
    if (
        not source_manifest["complete"]
        or sha(original_predictions_file) != source_manifest["predictions_sha256"]
        or sha(original_design_file) != source_manifest["design_sha256"]
        or sha(task_file) != source_manifest["public_tasks_sha256"]
    ):
        raise RuntimeError("Complete frozen v3 controls required")
    original_design = json.loads(original_design_file.read_text())
    if (
        original_design["feature_manifest_sha256"] != sha(features_file)
        or original_design["past_feedback_sha256"] != sha(feedback_file)
        or original_design["runner_sha256"] != sha(Path(core.__file__))
    ):
        raise RuntimeError("Shared training implementation or data differs")
    df, dc = fields.shape[1], contexts.shape[1]
    counts = {
        m: sum(p.numel() for p in make(m, df, dc, 12, 19).parameters())
        for m in NEW_METHODS
    }
    factor_count = original_design["actual_parameters"]["factor_composition"]
    if any(
        counts[m] < factor_count
        for m in ("capacity_matched_joint", "generic_bilinear_joint")
    ):
        raise RuntimeError(
            "Generic controls must have at least the compositional parameter count"
        )
    design = dict(
        original_design,
        methods=list(METHODS),
        capacity_matched_joint_hidden=67,
        bilinear_projection_and_output_hidden=64,
        actual_parameters={
            "factor_composition": factor_count,
            "learned_semantic_memory": original_design["actual_parameters"][
                "learned_semantic_memory"
            ],
            **counts,
        },
        runner_sha256=sha(__file__),
        shared_training_runner_sha256=sha(core.__file__),
        frozen_v3_predictions_sha256=sha(original_predictions_file),
        frozen_v3_design_sha256=sha(original_design_file),
        reused_v3_conditions=[
            "factor_composition",
            "learned_semantic_memory",
            "common_visual",
        ],
        generic_bilinear_control_has_same_learnable_field_context_product_but_no_fixed_program_sign_separation=True,
        additive_ablation_has_identical_factor_parameter_count=True,
        interpretation="assisted development architecture comparison; no exclusive ontology capability claim",
    )
    frozen = DEST / "design.json"
    if frozen.exists() and json.loads(frozen.read_text()) != design:
        raise RuntimeError("Frozen stronger-control design changed")
    save(frozen, design)
    snapshot = DEST / "runner_snapshot.py"
    if snapshot.exists() and sha(snapshot) != sha(__file__):
        raise RuntimeError("Frozen stronger-control implementation changed")
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    design_hash = sha(frozen)
    rows = [
        r
        for r in json.loads(original_predictions_file.read_text())
        if r["method"]
        in ("common_visual", "factor_composition", "learned_semantic_memory")
    ]
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
        for method in NEW_METHODS:
            for seed in SEEDS:
                model = make(method, df, dc, 12, seed)
                pretrained = DEST / "pretrained" / f"{company}_{method}_{seed}.pt"
                if not core.checkpoint_load(pretrained, model, design_hash):
                    core.train(model, historical, packs, [], core.PRETRAIN_STEPS)
                    core.checkpoint_save(pretrained, model, design_hash)
                state = {k: v.clone() for k, v in model.state_dict().items()}
                for budget in (0, 5, 10):
                    model.load_state_dict(state)
                    local = [index[i] for i in prefixes[company][str(budget)]]
                    active = (
                        core.train(model, local, packs, [], core.LOCAL_STEPS)
                        if budget
                        else 0
                    )
                    with torch.no_grad():
                        for j in evaluation:
                            pack, task = packs[j], tasks[j]
                            probability = (
                                pack["prior"]
                                + model(
                                    pack["field"], pack["context"], pack["candidate"]
                                )
                            ).softmax(0)
                            chosen = int(probability.argmax())
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
                                        "probabilities": probability.tolist(),
                                        "probabilities_not_calibrated": True,
                                        "full_visual_binding_not_certified": True,
                                    },
                                }
                            )
                    checkpoint = (
                        DEST / "checkpoints" / f"{company}_{budget}_{method}_{seed}.pt"
                    )
                    core.checkpoint_save(checkpoint, model, design_hash)
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
                            "checkpoint_sha256": sha(checkpoint),
                            "pretrained_sha256": sha(pretrained),
                        }
                    )
                    save(
                        DEST / "progress.json",
                        {
                            "new_models": len(ledger),
                            "expected_new_models": len(prefixes) * 27,
                        },
                    )
        print(
            "Stronger joint/bilinear controls",
            company,
            "new models",
            len(ledger),
            flush=True,
        )
    expected = sum(t["source_year"] in (2024, 2025) for t in tasks) * (
        1 + len(METHODS) * 9
    )
    if len(rows) != expected:
        raise RuntimeError("Incomplete stronger-control matrix")
    save(DEST / "predictions.json", rows)
    save(DEST / "training_ledger.json", ledger)
    save(
        DEST / "inference_manifest.json",
        {
            "complete": True,
            "expected_predictions": expected,
            "predictions_sha256": sha(DEST / "predictions.json"),
            "design_sha256": sha(frozen),
            "public_tasks_sha256": sha(task_file),
            "future_reference_or_scoring_files_read": False,
            "human_confirmations": 0,
        },
    )


if __name__ == "__main__":
    run()

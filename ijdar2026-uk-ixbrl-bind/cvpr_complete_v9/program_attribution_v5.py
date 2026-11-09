"""Separate program-decoding, schema, local context and crop-feature effects.

These are exploratory DEVELOPMENT attribution tests. Previously viewed future
development scores are not independent test evidence. No future labels train
models. Every omission is an ablation, not a weakened main baseline.
"""

import json
from pathlib import Path

import program_scope_learning_v3 as core
import program_training_inputs
import torch
from crop_candidates_v3 import DEST as COMMON
from prepare import save, sha
from torch import nn

DEST = COMMON / "program_attribution_v5"
METHODS = (
    "factor_composition",
    "learned_semantic_memory",
    "candidate_only",
    "without_field",
    "without_context",
    "without_crop_encoder_and_geometry",
    "neutral_null_factor",
    "generic_program_aware",
)
NEW_METHODS = METHODS[2:]
SEEDS = core.SEEDS


def program_properties(n):
    # Derivable from the shared public candidate's arithmetic program. No
    # canonical teacher quantity is consulted, and null has no numeric sign.
    numeric = (n[:, :10].abs().sum(1) > 0).float()
    sign = (2 * n[:, 0] - 1) * (1 - 2 * n[:, 1]) * (1 - n[:, 2]) * numeric
    return torch.stack((sign, numeric), 1)


class MaskedFactor(core.FactorCorrection):
    def __init__(self, df, dc, dn, mask):
        super().__init__(df, dc, dn)
        self.mask = mask

    def forward(self, f, c, n, supports=None):
        if self.mask in ("candidate_only", "without_field"):
            f = torch.zeros_like(f)
        if self.mask in ("candidate_only", "without_context"):
            c = torch.zeros_like(c)
        if self.mask == "without_crop_encoder_and_geometry":
            c = c.clone()
            c[..., 384:1152] = 0
            c[..., -8:] = 0
        field, context = self.field(f), self.context(c)
        direction = n[:, 0] * 2 - 1
        if self.mask == "neutral_null_factor":
            direction = direction * program_properties(n)[:, 1]
        sign = self.polarity(field * context).squeeze() * direction
        parse = self.parse(torch.cat((context.expand(len(n), -1), n), 1)).flatten()
        return sign + parse


class GenericProgramAware(nn.Module):
    def __init__(self, df, dc, dn):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(df + dc + dn + 2, 65),
            nn.Tanh(),
            nn.Linear(65, 65),
            nn.Tanh(),
            nn.Linear(65, 1),
        )
        nn.init.zeros_(self.net[-1].weight)
        nn.init.zeros_(self.net[-1].bias)

    def forward(self, f, c, n, supports=None):
        return self.net(
            torch.cat(
                (f.expand(len(n), -1), c.expand(len(n), -1), n, program_properties(n)),
                1,
            )
        ).flatten()


def make(method, df, dc, dn, seed):
    torch.manual_seed(seed)
    return (
        GenericProgramAware(df, dc, dn)
        if method == "generic_program_aware"
        else MaskedFactor(df, dc, dn, method)
    )


def run():
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    data = program_training_inputs.load()
    tasks, packs, prefixes = data["tasks"], data["packs"], data["prefixes"]
    df, dc = data["field_dimension"], data["context_dimension"]
    original = COMMON / "program_correction_learning"
    original_file = original / "predictions.json"
    original_manifest = json.loads((original / "inference_manifest.json").read_text())
    original_design_file = original / "design.json"
    original_design = json.loads(original_design_file.read_text())
    if (
        not original_manifest["complete"]
        or sha(original_file) != original_manifest["predictions_sha256"]
        or sha(original_design_file) != original_manifest["design_sha256"]
        or original_design["runner_sha256"] != sha(core.__file__)
        or original_design["public_tasks_sha256"] != data["public_tasks_sha256"]
    ):
        raise RuntimeError("Frozen equal-training v3 control provenance differs")
    counts = {
        m: sum(p.numel() for p in make(m, df, dc, 12, 19).parameters())
        for m in NEW_METHODS
    }
    if (
        counts["generic_program_aware"]
        < original_design["actual_parameters"]["factor_composition"]
    ):
        raise RuntimeError("Program-aware generic control capacity too small")
    design = dict(
        original_design,
        methods=list(METHODS),
        actual_parameters={
            m: original_design["actual_parameters"][m] for m in METHODS[:2]
        }
        | counts,
        public_tasks_sha256=data["public_tasks_sha256"],
        feature_manifest_sha256=data["feature_manifest_sha256"],
        past_feedback_sha256=data["past_feedback_sha256"],
        feedback_prefix_sha256=data["feedback_prefix_sha256"],
        runner_sha256=sha(__file__),
        shared_training_runner_sha256=sha(core.__file__),
        shared_input_loader_sha256=sha(program_training_inputs.__file__),
        frozen_v3_predictions_sha256=sha(original_file),
        program_aware_generic_hidden=[65, 65],
        candidate_only_is_attribution_ablation_not_main_baseline=True,
        crop_geometry_ablation_retains_common_visual_perception_text_roles_and_OCR=True,
        neutral_null_only_removes_polarity_term_from_non_numeric_null=True,
        full_visual_scope_ownership_certified=False,
        exploratory_development_attribution=True,
    )
    frozen = DEST / "design.json"
    if frozen.exists() and json.loads(frozen.read_text()) != design:
        raise RuntimeError("Frozen program attribution design changed")
    save(frozen, design)
    snapshot = DEST / "runner_snapshot.py"
    if snapshot.exists() and sha(snapshot) != sha(__file__):
        raise RuntimeError("Frozen attribution implementation differs")
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    digest = sha(frozen)
    rows = [
        r
        for r in json.loads(original_file.read_text())
        if r["method"] in ("common_visual", *METHODS[:2])
    ]
    index = {t["task_id"]: j for j, t in enumerate(tasks)}
    ledger = []
    for company in sorted(prefixes):
        past = [
            j
            for j, t in enumerate(tasks)
            if t["source_year"] == 2023 and t["company"] != company
        ]
        future = [
            j
            for j, t in enumerate(tasks)
            if t["source_year"] in (2024, 2025) and t["company"] == company
        ]
        for method in NEW_METHODS:
            for seed in SEEDS:
                model = make(method, df, dc, 12, seed)
                pretrained = DEST / "pretrained" / f"{company}_{method}_{seed}.pt"
                if not core.checkpoint_load(pretrained, model, digest):
                    core.train(model, past, packs, [], core.PRETRAIN_STEPS)
                    core.checkpoint_save(pretrained, model, digest)
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
                        for j in future:
                            task, pack = tasks[j], packs[j]
                            probabilities = (
                                pack["prior"]
                                + model(
                                    pack["field"], pack["context"], pack["candidate"]
                                )
                            ).softmax(0)
                            selected = int(probabilities.argmax())
                            rows.append(
                                {
                                    "task_id": task["task_id"],
                                    "company": company,
                                    "method": method,
                                    "budget": budget,
                                    "seed": seed,
                                    "prediction": {
                                        "candidate_index": selected
                                        if selected < len(task["candidates"])
                                        else None,
                                        "forced_answer": task["candidates"][selected][
                                            "computed_answer"
                                        ]
                                        if selected < len(task["candidates"])
                                        else None,
                                        "probabilities": probabilities.tolist(),
                                        "probabilities_not_calibrated": True,
                                        "full_visual_binding_not_certified": True,
                                    },
                                }
                            )
                    checkpoint = (
                        DEST / "checkpoints" / f"{company}_{budget}_{method}_{seed}.pt"
                    )
                    core.checkpoint_save(checkpoint, model, digest)
                    ledger.append(
                        {
                            "company": company,
                            "method": method,
                            "seed": seed,
                            "budget": budget,
                            "other_company_pretraining_slots": len(past),
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
                            "expected_new_models": len(prefixes) * len(NEW_METHODS) * 9,
                        },
                    )
        print("Program attribution", company, "models", len(ledger), flush=True)
    expected = sum(t["source_year"] in (2024, 2025) for t in tasks) * (
        1 + len(METHODS) * 9
    )
    if len(rows) != expected:
        raise RuntimeError("Incomplete program-attribution matrix")
    save(DEST / "predictions.json", rows)
    save(DEST / "training_ledger.json", ledger)
    save(
        DEST / "inference_manifest.json",
        {
            "complete": True,
            "expected_predictions": expected,
            "predictions_sha256": sha(DEST / "predictions.json"),
            "design_sha256": sha(frozen),
            "public_tasks_sha256": data["public_tasks_sha256"],
            "future_reference_or_scoring_files_read": False,
            "human_confirmations": 0,
        },
    )


if __name__ == "__main__":
    run()

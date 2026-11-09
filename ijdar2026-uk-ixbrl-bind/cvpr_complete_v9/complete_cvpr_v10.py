"""Machine-readable completion ledger; no speculative result is marked complete."""

import json
from datetime import datetime, timezone
from pathlib import Path

from prepare import EXP, save, sha

DEST = EXP / "runs/cvpr_complete_v10_20261014"


def plan():
    source_plan = EXP / "CVPR2027_SOTA相关工作调查与具体实验方案_20261011.md"
    body = {
        "objective": "Finish the original CVPR experimental matrix and test an attributable ontology/scope benefit under equal evidence and budgets",
        "primary_generation_and_visual_model": "deepseek-flash",
        "other_generative_backbones": "deferred by explicit user instruction; no Qwen/InternVL experiment claimed",
        "common_frozen_support_encoders": ["LayoutLMv3", "MiniLM"],
        "human_runtime_confirmations": 0,
        "feedback_is_automatic_not_human": True,
        "historical_negative_results_retained": True,
        "positive_result_not_presumed": True,
        "do_not_weaken_controls_or_open_test_answers_to_tune_methods": True,
        "source_plan_sha256": sha(source_plan),
        "phases": [
            {
                "id": "E0",
                "name": "source/reference/visible-scope qualification",
                "required": [
                    "common native OCR and crop qualification",
                    "independent header coverage supervision",
                    "identity, source/template overlap and label ambiguity audit",
                ],
                "existing_completed": [
                    "strict native OCR repair",
                    "publisher semantic ID and declared numeric transform validation",
                    "uniform ChartQAPro API image qualification",
                ],
                "status": "in_progress",
            },
            {
                "id": "E1",
                "name": "direct visual scope and semantic correspondence",
                "required": [
                    "field and multiplier oracle removed from annual-report correspondence inputs",
                    "train explicit header coverage and component correspondence",
                    "strong semantic memory and generic joint/structure controls",
                    "three seeds and complete component attribution",
                ],
                "existing_completed": [
                    "assisted sign/program development and attribution only"
                ],
                "status": "in_progress",
            },
            {
                "id": "E2",
                "name": "complete official TAT-DQA comparison",
                "required": [
                    "frozen method and strong controls on official train/dev/test",
                    "native images with the same OCR/tools/call budget",
                    "official full-denominator EM/F1",
                    "historical checkpoint results clearly distinguished from adapted DeepSeek controls",
                ],
                "existing_completed": [
                    "1644-question native development baseline and two equal-first-stage program/memory agents"
                ],
                "status": "pending_method_freeze",
            },
            {
                "id": "E3",
                "name": "complete ChartQAPro transfer and visual necessity",
                "required": [
                    "1948-record official evaluation",
                    "native direct, common-tool program agent, table representation, learned scope and generic controls",
                    "same image adapter, tools and transport policy",
                    "visual and OCR-only attribution",
                ],
                "existing_completed": [
                    "1948-record direct native inference after API qualification; terminal scoring references unopened"
                ],
                "status": "pending_method_freeze",
            },
            {
                "id": "E4",
                "name": "0/5/10 automatic-feedback reuse",
                "required": [
                    "two fixed public prefix policies",
                    "known and unknown feedback consumes equal slots",
                    "correct full-binding coverage and error propagation",
                    "frozen parent with local changes compared to whole-model updates",
                    "new-source evaluation after source qualification",
                ],
                "existing_completed": [
                    "assisted program-correction learning showed negative local transfer"
                ],
                "status": "pending_direct_correspondence",
            },
            {
                "id": "E5",
                "name": "natural change and selective invalidation",
                "required": [
                    "period, unit, dimension, restatement, scope and presentation changes recorded separately",
                    "affected-link withdrawal precision/recall",
                    "valid-link preservation",
                    "full reread, semantic memory, temporal/contradiction memory, generic dependency withdrawal and visual scope controls",
                    "common visible evidence and dependency inputs",
                ],
                "status": "pending_direct_correspondence",
            },
            {
                "id": "E6",
                "name": "mechanism attribution",
                "required": [
                    "predicted/removed/heuristic/reference scope",
                    "same rules with retrieval and execution variants",
                    "global versus independent decoding",
                    "matched reread budgets",
                    "visual representation, OCR/layout and version-dependency ablation",
                ],
                "existing_completed": [
                    "program-correction component ablations do not establish extra field-schema or multiplicative-interaction benefit"
                ],
                "status": "pending_full_method",
            },
            {
                "id": "E7",
                "name": "ChartMuseum extension",
                "required": [
                    "official full 1162-question protocol after freeze",
                    "current leaderboard and input track qualification",
                    "same-model mechanism controls",
                ],
                "original_plan_conditional_on_resources": True,
                "status": "pending_method_freeze_and_resource_measurement",
            },
        ],
        "completion_requires_actual_artifact_for_every_required_item": True,
        "no_score_or_scope_certification_in_this_plan": True,
        "runner_sha256": sha(__file__),
    }
    path = DEST / "completion_plan.json"
    if path.exists() and json.loads(path.read_text()) != body:
        raise RuntimeError("Completion plan already frozen differently")
    save(path, body)
    snapshot = DEST / "plan_snapshot.py"
    if not snapshot.exists():
        snapshot.write_bytes(Path(__file__).read_bytes())
    save(
        DEST / "execution_state.json",
        {
            "updated_utc": datetime.now(timezone.utc).isoformat(),
            "plan_sha256": sha(path),
            "all_original_experiments_completed": False,
            "ontology_mechanism_advantage_established": False,
            "current_work": [
                "FinTabNet.c annotation/image preparation for independently annotated header coverage",
                "annual report direct correspondence without target field or multiplier oracle",
            ],
            "human_runtime_confirmations": 0,
        },
    )
    print(
        "Frozen completion matrix E0–E7; all results require checked artifacts",
        flush=True,
    )


if __name__ == "__main__":
    plan()

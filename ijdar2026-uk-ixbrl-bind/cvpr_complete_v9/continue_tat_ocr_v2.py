"""Uniform input-repair experiment; never selects questions by correctness.

Reuse both controls only where their actual model inputs are byte-identical.
Rerun every changed question in both conditions under the original prompts and
budgets. The two conditions again share the exact first model response.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

import httpx

from prepare import OUT, save, sha
from public_benchmarks import ROOT as ORIGINAL_ROOT
from refresh_tat_ocr import ROOT, link_unchanged
from tat_agent import FIRST, SECOND, ocr_context

DEST = OUT / "tat_ocr_corrected_v2"


def digest_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def copy_exact(source, target):
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and sha(target) != sha(source):
        raise RuntimeError("An unchanged-input cached result differs")
    if not target.exists():
        shutil.copyfile(source, target)


def prepare_reuse():
    old_root = ORIGINAL_ROOT / "tatdqa/dev"
    root = ROOT / "tatdqa/dev"
    protocol = json.loads((root / "ocr_refresh_protocol.json").read_text())
    if protocol["documents"] != 274 or protocol["pages"] != 307:
        raise RuntimeError("Complete official development source refresh required")
    if sha(root / "public/tasks.json") != sha(old_root / "public/tasks.json"):
        raise RuntimeError("Public question inventory changed")
    old_design = json.loads((OUT / "tat_dev_program_agent/design.json").read_text())
    if old_design["first_prompt"] != FIRST or old_design["second_prompt"] != SECOND:
        raise RuntimeError("Original prompts changed")
    if old_design["executor_sha256"] != sha(
        Path(__file__).with_name("evidence_program.py")
    ):
        raise RuntimeError("Original execution tool changed")
    tasks = json.loads((root / "public/tasks.json").read_text())
    reused, changed = [], []
    for task in tasks:
        before, _ = ocr_context(task, old_root)
        after, _ = ocr_context(task, root)
        first_before = FIRST + "\nQuestion: " + task["question"] + "\n" + before
        first_after = FIRST + "\nQuestion: " + task["question"] + "\n" + after
        before_manifest = json.loads(
            (
                old_root / "common_perception" / task["document_id"] / "manifest.json"
            ).read_text()
        )
        after_manifest = json.loads(
            (
                root / "common_perception" / task["document_id"] / "manifest.json"
            ).read_text()
        )
        images_before = [
            (p["image"], p["image_sha256"]) for p in before_manifest["pages"]
        ]
        images_after = [
            (p["image"], p["image_sha256"]) for p in after_manifest["pages"]
        ]
        if images_before != images_after:
            raise RuntimeError("Original model image inputs changed")
        record = {
            "task_id": task["task_id"],
            "document_id": task["document_id"],
            "old_first_prompt_sha256": digest_text(first_before),
            "corrected_first_prompt_sha256": digest_text(first_after),
            "image_inputs_identical": True,
        }
        if first_before != first_after:
            changed.append(record)
            continue
        reused.append(record)
        ident = task["task_id"]
        for name in ("tat_dev_program_agent", "tat_dev_memory_agent"):
            former = OUT / name
            target = DEST / name
            copy_exact(
                former / "responses" / (ident + ".json"),
                target / "responses" / (ident + ".json"),
            )
            for source in (former / "raw").glob(ident + "*.json"):
                copy_exact(source, target / "raw" / source.name)
            for source in (former / "attempts").glob(ident + "_*.json"):
                copy_exact(source, target / "attempts" / source.name)
    link_unchanged(DEST / "tat_training_memory", OUT / "tat_training_memory")
    link_unchanged(DEST / "tat_dev_native_baseline", OUT / "tat_dev_native_baseline")
    save(
        DEST / "repair_protocol.json",
        {
            "questions": len(tasks),
            "byte_identical_input_questions_reused": reused,
            "every_changed_input_question_rerun_in_both_controls": changed,
            "selection_based_only_on_public_input_bytes_not_answers_or_success": True,
            "unchanged_success_and_failure_outputs_reused_equally": True,
            "original_prompts_executor_and_call_budgets_unchanged": True,
            "changed_question_first_call_shared_exactly_between_controls": True,
            "unaffected_native_image_baseline_reused_without_resampling": True,
            "training_memory_uses_questions_and_official_train_supervision_not_OCR": True,
            "training_retrieval_artifact_sha256": sha(
                OUT / "tat_training_memory/development_retrieval.json"
            ),
            "old_inference_results_preserved": True,
            "not_an_ontology_advantage_experiment": True,
            "public_tasks_sha256": sha(root / "public/tasks.json"),
            "corrected_native_manifest_sha256": sha(
                root / "common_perception/manifest.json"
            ),
            "source_OCR_refresh_protocol_sha256": sha(
                root / "ocr_refresh_protocol.json"
            ),
            "runner_sha256": sha(__file__),
            "human_confirmations": 0,
        },
    )
    print(
        "Uniform OCR repair:",
        len(reused),
        "identical-input reused;",
        len(changed),
        "changed-input paired reruns",
        flush=True,
    )


def resilient_account_probe(function):
    """Retry network-only balance probes; model inference retries stay unchanged."""

    def probe(client, key):
        for attempt in range(3):
            try:
                return function(client, key)
            except httpx.TransportError as exc:
                log = DEST / "account_probe_transport_failures.jsonl"
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a") as file:
                    file.write(
                        json.dumps(
                            {
                                "error_type": type(exc).__name__,
                                "probe_attempt": attempt + 1,
                                "not_a_model_inference_retry": True,
                            }
                        )
                        + "\n"
                    )
                if attempt == 2:
                    raise
                time.sleep(2)

    return probe


def main(stage):
    if stage in ("prepare", "all"):
        prepare_reuse()
    if stage in ("run", "all"):
        import tat_agent
        import tat_memory_agent

        tat_agent.ROOT = ROOT
        tat_agent.OUT = DEST
        tat_memory_agent.ROOT = ROOT
        tat_memory_agent.OUT = DEST
        tat_agent.balance = resilient_account_probe(tat_agent.balance)
        tat_memory_agent.balance = resilient_account_probe(tat_memory_agent.balance)
        tat_agent.run()
        manifest = json.loads(
            (DEST / "tat_dev_program_agent/inference_manifest.json").read_text()
        )
        if not manifest["complete"]:
            raise RuntimeError(
                "Corrected program inference incomplete; preserve progress and resume"
            )
        tat_memory_agent.run()
        manifest = json.loads(
            (DEST / "tat_dev_memory_agent/inference_manifest.json").read_text()
        )
        if not manifest["complete"]:
            raise RuntimeError(
                "Corrected memory inference incomplete; preserve progress and resume"
            )
    if stage in ("score", "all"):
        import score_native
        import compare_agents

        # The unchanged official references become available only for the
        # post-inference scoring stage, after each complete matrix is checked.
        link_unchanged(
            ROOT / "tatdqa/dev/scoring_only", ORIGINAL_ROOT / "tatdqa/dev/scoring_only"
        )
        score_native.OUT = DEST
        score_native.ROOT = ROOT
        for name in ("tat_dev_program_agent", "tat_dev_memory_agent"):
            score_native.main(name)
        rows = json.loads((DEST / "tat_dev_memory_agent/outcomes.json").read_text())
        parent = DEST / "tat_dev_program_agent/raw"
        if any(
            r.get("common_first_response_sha256")
            != sha(parent / (r["task_id"] + "_0.json"))
            for r in rows
        ):
            raise RuntimeError("Matched first-stage response differs")
        compare_agents.OUT = DEST
        compare_agents.main()
    save(
        DEST / "continuation_status.json",
        {
            "stage_completed": stage,
            "full_original_CVPR_experiments_complete": False,
            "human_confirmations": 0,
            "runner_sha256": sha(__file__),
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("prepare", "run", "score", "all"))
    main(parser.parse_args().stage)

"""Complete the same fixed cohort after private no-ID annotation recovery.

The former 800 targets, predictions and negative results remain unchanged.
Restore all 42 reserved reports with identical pixels, public hash selection
and feedback rules. Reuse shared model responses only for byte-identical tasks.
"""

import argparse
import json
import shutil

import canonicalization_prepare as prep
from prepare import OUT, save, sha

ORIGINAL = prep.ROOT
PREVIOUS = prep.DEST
VIEW = ORIGINAL / "full_publisher_reference_view_v2"
DEST = OUT / "canonicalization_isolation_v2_full_cohort"


def prepare_view():
    recovery = ORIGINAL / "publisher_identity_v2"
    manifest_file = recovery / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    original_render = ORIGINAL / "native_renders_v3/render_manifest_dpr2.json"
    if sha(original_render) != manifest["original_render_manifest_sha256"]:
        raise RuntimeError("Original frozen native geometry changed")
    VIEW.mkdir(parents=True, exist_ok=True)
    for name in (
        "public_schema",
        "public_feature_pages.json",
        "common_perception",
        "common_headers",
        "visual_features",
    ):
        target = VIEW / name
        if not target.exists():
            target.symlink_to(
                ORIGINAL / name, target_is_directory=(ORIGINAL / name).is_dir()
            )
    for source in (ORIGINAL / "scoring_only").glob("*.json"):
        patched = recovery / "scoring_only" / source.name
        selected = patched if patched.exists() else source
        target = VIEW / "scoring_only" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.symlink_to(selected)
        if sha(target) != sha(selected):
            raise RuntimeError("Private recovered reference view changed")
    renders = json.loads((recovery / "render_scoring_only.json").read_text())
    for report in renders:
        for page in report["selected_pages"]:
            if sha(page["image_path"]) != page["image_sha256"]:
                raise RuntimeError("Original source image changed")
            for region in page["scoring_only_region_boxes"]:
                if "private_reference_key" in region:
                    region["id"] = region["private_reference_key"]
    render_view = VIEW / "native_renders_v3/render_manifest_dpr2.json"
    if render_view.exists() and json.loads(render_view.read_text()) != renders:
        raise RuntimeError("Recovered private geometry view changed")
    save(render_view, renders)
    save(
        VIEW / "provenance.json",
        {
            "private_reference_ID_recovery_only": True,
            "original_render_manifest_sha256": sha(original_render),
            "recovery_manifest_sha256": sha(manifest_file),
            "runner_sha256": sha(__file__),
            "no_original_pixel_OCR_or_header_replacement": True,
            "old_inference_outputs_and_labels_not_rewritten": True,
        },
    )


def reuse_observations():
    old_tasks = {
        t["task_id"]: t
        for t in json.loads((PREVIOUS / "public/tasks.json").read_text())
    }
    new_tasks = {
        t["task_id"]: t for t in json.loads((DEST / "public/tasks.json").read_text())
    }
    for ident, task in old_tasks.items():
        if new_tasks.get(ident) != task:
            raise RuntimeError(
                "An original common model task changed; exact response reuse forbidden"
            )
    former = PREVIOUS / "common_visual"
    for subdir in ("raw", "attempts", "batches", "failures"):
        for source in (former / subdir).glob("*.json"):
            target = DEST / "common_visual" / subdir / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and sha(target) != sha(source):
                raise RuntimeError("Existing exact common response differs")
            if not target.exists():
                shutil.copyfile(source, target)
    save(
        DEST / "recovery_protocol_addendum.json",
        {
            "previous_tasks_preserved": len(old_tasks),
            "new_targets_from_former_unscorable_reports": len(new_tasks)
            - len(old_tasks),
            "prior_negative_result_unchanged": str(
                PREVIOUS / "development_results.json"
            ),
            "byte_identical_previous_public_tasks_checked": True,
            "previous_shared_raw_observations_reused_without_new_model_sampling": True,
            "original_source_selection_and_public_hash_prefix_not_changed": True,
            "only_private_publisher_annotation_correspondence_repaired": True,
            "private_display_number_or_model_outcomes_not_used_to_match_references": True,
            "not_native_QA_main_result": True,
            "human_confirmations": 0,
            "derived_reference_view_sha256": sha(VIEW / "provenance.json"),
            "runner_sha256": sha(__file__),
        },
    )


def main(stage):
    # Make the phase's derived reference view and output destination explicit.
    # The frozen v1 preparation implementation and selection rules are retained.
    prep.ROOT = VIEW
    prep.DEST = DEST
    if stage in ("prepare", "all"):
        prepare_view()
        if not (DEST / "protocol.json").exists():
            prep.prepare()
        reuse_observations()
    if stage in ("observe", "all"):
        import canonicalization_observe

        canonicalization_observe.run()
    if stage in ("train", "all"):
        import canonicalization_train

        if not (DEST / "training/inference_manifest.json").exists():
            canonicalization_train.run()
    if stage in ("score", "all"):
        import canonicalization_score

        canonicalization_score.main()
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
    parser.add_argument(
        "stage", choices=("prepare", "observe", "train", "score", "all")
    )
    main(parser.parse_args().stage)

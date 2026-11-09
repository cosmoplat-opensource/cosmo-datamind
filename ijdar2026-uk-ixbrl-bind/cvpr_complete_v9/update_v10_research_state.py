"""Record completed evidence and actual pending jobs without completion claims."""

import json
from collections import Counter
from datetime import datetime, timezone

from prepare import EXP, save, sha

ROOT = EXP / "runs/cvpr_complete_v10_20261014"


def run():
    native = ROOT / "native_correspondence"
    fin = ROOT / "fintab_header_coverage"
    a = json.loads((fin / "learning_v1/development_results.json").read_text())
    b = json.loads((fin / "strong_controls_v2/development_results.json").read_text())
    fields = native / "field_retrieval_report_labels_v3"
    f = json.loads((fields / "learning_v1/development_results.json").read_text())
    visual = json.loads((native / "common_visual/manifest.json").read_text())
    visual_status = Counter(r["status"] for r in visual["records"])
    audit = json.loads((fields / "past_retrieval_audit.json").read_text())
    scope = a["seed_means"]["learned_rectangle_scope"]["coverage_macro_table_f1_known"]
    strong = b["seed_means"]["optimal_span_edges"]["coverage_macro_table_f1_known"]
    summary = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False,
        "fintab_component": {
            "train_tables": 1000, "development_tables": 200, "training_issuer_identifiers": 219,
            "development_issuer_identifiers": 10, "seeds": [19, 41, 73], "trained_models": 15,
            "scope_macro_table_f1_known": scope, "strongest_generic_macro_table_f1_known": strong,
            "difference": scope-strong,
            "scope_without_pixels_macro_table_f1_known": a["seed_means"]["scope_without_pixels"]["coverage_macro_table_f1_known"],
            "scope_without_text_macro_table_f1_known": a["seed_means"]["scope_without_text"]["coverage_macro_table_f1_known"],
            "scope_micro_f1_known": a["seed_means"]["learned_rectangle_scope"]["coverage_micro_f1_known"],
            "all_predictions_verified_scope_graph_exact": a["seed_means"]["learned_rectangle_scope"]["all_predictions_verified_scope_graph_exact"],
            "unknown_scope_predictions_are_not_certified_by_numeric_agreement": True,
            "table_crop_and_native_word_localization_assisted_by_publisher": True,
            "grid_coverage_supervision_is_not_financial_field_unit_period_ownership_gold": True,
            "strong_controls_were_added_after_development_results": True,
            "independent_financial_binding_or_cvpr_sota_result": False,
        },
        "native_shared_observation": {
            "targets": sum(len(r["task_ids"]) for r in visual["records"]), "batches": len(visual["records"]),
            "valid_batches": visual_status["completed"], "invalid_format_batches": visual_status["invalid_model_format"],
            "valid_target_observations": sum(len(r.get("targets", [])) for r in visual["records"]),
            "failed_targets_retained": sum(len(r["task_ids"]) for r in visual["records"] if r["status"] != "completed"),
            "target_field_and_multiplier_oracles_provided": False,
            "target_roi_localization_is_a_common_annotation_assisted_input": True,
        },
        "public_schema_and_retrieval": {
            "source_report_packages": 42, "public_schema_fields": 16564,
            "past_exact_retrieval_coverage": audit["counts"]["retrieval_exact_field_coverage"],
            "past_targets": 280, "past_top1_exact_field_correct": audit["counts"]["top1_exact_field_correct"],
            "future_report_label_aliases_not_used_in_past_field_cards": True,
            "cross_year_pooled_alias_stage_preserved_but_ineligible_for_chronological_learning": True,
            "report_year_split_is_not_yet_certified_actual_publication_chronology": True,
        },
        "native_field_development": {
            "complete": True, "companies": 14, "unique_future_targets": 560,
            "configured_prediction_files": 1008, "configured_outputs_including_identical_zero_feedback_controls": 40320,
            "parent_checkpoints": 126, "seeds": [19, 41, 73], "automatic_feedback_budgets": [0, 5, 10],
            "selection_policies": ["hash_prefix", "public_information_prefix"],
            "four_controls": ["generic_joint", "factor_field", "learned_semantic_memory", "frozen_semantic_memory"],
            "human_confirmations": 0, "seed_means": f["seed_means"],
            "feedback_error_propagation": f["feedback_error_propagation"],
            "factor_zero_feedback_advantage_over_generic": f["paired_comparisons"]["hash_prefix/0/generic_joint"],
            "field_factor_advantage_not_established": True,
            "small_full_parameter_updates_cause_negative_transfer": True,
            "not_full_semantic_binding_or_reserved_independent_source_test": True,
        },
        "active_next_experiment": {
            "name": "native_context_crops_v12", "targets": 840, "expected_batches": 468,
            "common_original_pixel_row_and_column_contexts": True,
            "targets_per_request": 2, "maximum_images_per_request": 7,
            "maximum_output_tokens": 5000, "all_controls_use_same_context_responses": True,
            "model": "deepseek-flash", "maximum_transport_attempts": 4, "format_errors_not_retried": True,
            "queued_followup": ["source_local_field_reretrieval", "common_pixel_text_features",
                                "four_field_controls_with_two_automatic_feedback_policies", "complete_development_scoring"],
            "future_canonical_quantity_header_ownership_or_field_reference_not_used_by_observer": True,
            "changes_to_visual_tools_will_not_be_reported_as_ontology_gain": True,
        },
        "required_remaining": [
            "Learn and evaluate full field/unit/period/dimension/quantity bindings, retaining unverifiable scopes as unknown",
            "Compare scoped local updates with strong semantic/version/dependency memory under equal feedback and visible evidence",
            "Natural change withdrawal/preservation tests and budget accounting",
            "Legal-entity/template/chronology qualification and independent native sources",
            "Frozen complete TAT-DQA and ChartQAPro method comparisons and planned broader public benchmarks",
        ],
        "result_files_sha256": {str(p.relative_to(ROOT)): sha(p) for p in (
            fin / "learning_v1/development_results.json", fin / "strong_controls_v2/development_results.json",
            fields / "learning_v1/development_results.json", fields / "past_retrieval_audit.json",
            native / "common_visual/manifest.json")},
        "runner_sha256": sha(__file__),
    }
    context_root = native / "native_context_crops_v12"
    context_scores = context_root / "field_retrieval_v1/learning_v1/development_results.json"
    if context_scores.exists():
        context_result = json.loads(context_scores.read_text())
        context_visual_file = context_root / "common_visual/manifest.json"
        context_visual = json.loads(context_visual_file.read_text())
        pipeline = json.loads((context_root / "field_retrieval_v1/pipeline_progress.json").read_text())
        if not context_result["complete"] or not context_visual["complete"] or pipeline["phase"] != "complete":
            raise ValueError("Complete v12 observation, predictions and scoring are required")
        context_status = Counter(r["status"] for r in context_visual["records"])
        summary["native_context_field_development_v12"] = {
            "complete": True,
            "completed_utc": context_result["completed_utc"],
            "companies": context_result["companies"],
            "future_targets": context_result["future_targets"],
            "shared_observation_batches": context_visual["processed_batches"],
            "observation_status_counts": dict(context_status),
            "valid_target_observations": sum(len(r.get("targets", [])) for r in context_visual["records"]),
            "failed_targets_retained_in_full_inventory": sum(len(r["task_ids"]) for r in context_visual["records"] if r["status"] != "completed"),
            "seed_means": context_result["seed_means"],
            "paired_comparisons": context_result["paired_comparisons"],
            "feedback_error_propagation": context_result["feedback_error_propagation"],
            "no_clear_field_factor_advantage_over_generic_or_memory": True,
            "field_channel_only_not_full_visible_binding": True,
            "independent_native_test_completed": context_result["independent_native_test_completed"],
            "human_runtime_confirmations": context_result["human_runtime_confirmations"],
            "extra_context_does_not_establish_ontology_or_visual_tool_gain": True,
        }
        summary["active_next_experiment"].update(
            status="completed_development_only", queued_followup=[],
            all_previously_queued_followup_stages_completed=True,
        )
        for artifact in (context_scores, context_visual_file):
            summary["result_files_sha256"][str(artifact.relative_to(ROOT))] = sha(artifact)
    failure_audit_file = native / "failure_audit_v13/development_retrieval_ranking_audit.json"
    if failure_audit_file.exists():
        failure_audit = json.loads(failure_audit_file.read_text())
        if not failure_audit["complete"]:
            raise ValueError("Complete post-score failure diagnosis required")
        summary["native_field_failure_diagnosis_v13"] = {
            "stage": "post_score_development_diagnosis_not_new_model_experiment",
            "counts": {k: v["counts"] for k, v in failure_audit["experiments"].items()},
            "exact_field_candidate_ceiling": {k: v["exact_field_candidate_ceiling"] for k, v in failure_audit["experiments"].items()},
            "new_model_or_prediction_trained_or_modified": False,
            "independent_native_test_references_read": False,
            "field_annotation_does_not_certify_visual_reading_errors": True,
        }
        summary["result_files_sha256"][str(failure_audit_file.relative_to(ROOT))] = sha(failure_audit_file)
    binding_manifest_file = native / "full_binding_preparation_v14/manifest.json"
    if binding_manifest_file.exists():
        binding = json.loads(binding_manifest_file.read_text())
        if not binding["complete"]:
            raise ValueError("Complete historical binding preparation required")
        summary["historical_full_binding_preparation_v14"] = {
            "complete": True,
            "stage": binding["stage"],
            "prepared_utc": binding["prepared_utc"],
            "historical_targets": binding["historical_targets"],
            "historical_source_reports": binding["historical_source_reports"],
            "counts": binding["counts"],
            "new_model_inference_or_accuracy_result": False,
            "visible_header_ownership_or_visual_identifiability_certified": False,
            "future_or_reserved_test_report_content_or_labels_read_by_preparer": False,
            "historical_reference_view": binding["historical_reference_view"],
        }
        summary["result_files_sha256"][str(binding_manifest_file.relative_to(ROOT))] = sha(binding_manifest_file)
    binding_candidates_file = native / "full_binding_preparation_v14/public_component_candidates/manifest.json"
    if binding_candidates_file.exists():
        candidates = json.loads(binding_candidates_file.read_text())
        if not candidates["complete"]:
            raise ValueError("Complete shared component candidate preparation required")
        summary["public_binding_component_candidates_v14"] = {
            "complete": True,
            "prepared_utc": candidates["prepared_utc"],
            "stage": candidates["stage"],
            "counts": candidates["counts"],
            "any_teacher_or_scoring_reference_file_read": candidates["any_teacher_or_scoring_reference_file_read"],
            "canonical_entity_and_dimension_resolution_complete": False,
            "candidate_counts_are_not_accuracy_or_certified_coverage": True,
            "new_model_inference_or_accuracy_result": False,
        }
        summary["result_files_sha256"][str(binding_candidates_file.relative_to(ROOT))] = sha(binding_candidates_file)
    replay_root = native / "field_replay_v15"
    replay_progress_file = replay_root / "pipeline_progress.json"
    replay_result_file = replay_root / "development_results.json"
    if replay_progress_file.exists():
        replay_progress = json.loads(replay_progress_file.read_text())
        summary["field_replay_development_v15"] = {
            "pipeline_progress": replay_progress,
            "field_channel_only_not_full_binding": True,
            "no_new_visual_model_calls": True,
            "controls": ["public_retrieval", "public_soft_prior", "legacy_joint", "legacy_factor",
                         "frozen_semantic_memory", "replay_joint", "replay_factor"],
            "seeds": [19, 41, 73], "automatic_feedback_budgets": [0, 5, 10],
            "local_minibatch_includes_at_most_four_local_and_four_nonlocal_historical_examples": True,
            "developed_after_v12_development_scores": True,
            "independent_test_or_ontology_unique_advantage_established": False,
        }
        if replay_result_file.exists():
            replay_results = json.loads(replay_result_file.read_text())
            if not replay_results["complete"]:
                raise ValueError("Complete replay results required")
            summary["field_replay_development_v15"]["seed_means"] = replay_results["seed_means"]
            summary["field_replay_development_v15"]["paired_comparisons"] = replay_results["paired_comparisons"]
            summary["field_replay_development_v15"]["feedback_error_propagation"] = replay_results["feedback_error_propagation"]
            summary["result_files_sha256"][str(replay_result_file.relative_to(ROOT))] = sha(replay_result_file)
    binding_v16_root = native / "binding_candidates_v16_corrected_members_v2"
    binding_v16_progress_file = binding_v16_root / "pipeline_progress.json"
    binding_v16_manifest_file = binding_v16_root / "manifest.json"
    binding_v16_audit_file = binding_v16_root / "training_only/historical_candidate_audit.json"
    if binding_v16_progress_file.exists():
        binding_v16_progress = json.loads(binding_v16_progress_file.read_text())
        summary["public_entity_dimension_candidates_v16"] = {
            "pipeline_progress": binding_v16_progress,
            "stage": "candidate_preparation_not_model_accuracy",
            "supersedes_ineligible_initial_preparation": "binding_candidates_v16",
            "report_lei_is_common_metadata_assistance_not_learned_visual_identity": True,
            "public_declaration_roles_do_not_certify_axis_member_membership_or_header_ownership": True,
            "all_controls_receive_same_candidate_inventory": True,
            "complete_learned_binding_or_independent_test_result": False,
        }
        initial_review_file = native / "binding_candidates_v16/preparation_review.json"
        if initial_review_file.exists():
            summary["public_entity_dimension_candidates_v16"]["initial_preparation_review"] = json.loads(initial_review_file.read_text())
            summary["result_files_sha256"][str(initial_review_file.relative_to(ROOT))] = sha(initial_review_file)
        if binding_v16_manifest_file.exists():
            binding_v16_manifest = json.loads(binding_v16_manifest_file.read_text())
            if not binding_v16_manifest["complete"]:
                raise ValueError("Complete public v16 candidate manifest required")
            summary["public_entity_dimension_candidates_v16"]["counts"] = binding_v16_manifest["counts"]
            summary["result_files_sha256"][str(binding_v16_manifest_file.relative_to(ROOT))] = sha(binding_v16_manifest_file)
        if binding_v16_audit_file.exists():
            binding_v16_audit = json.loads(binding_v16_audit_file.read_text())
            if not binding_v16_audit["complete"]:
                raise ValueError("Complete historical-only candidate audit required")
            summary["public_entity_dimension_candidates_v16"]["historical_feasibility_counts"] = binding_v16_audit["counts"]
            summary["public_entity_dimension_candidates_v16"]["nonempty_historical_dimensions"] = binding_v16_audit["nonempty_historical_dimensions"]
            summary["public_entity_dimension_candidates_v16"]["required_next_step"] = "repair common period and dimension candidate omissions before full joint binding training"
            summary["result_files_sha256"][str(binding_v16_audit_file.relative_to(ROOT))] = sha(binding_v16_audit_file)
    binding_v17_root = native / "binding_candidates_v17"
    binding_v17_progress_file = binding_v17_root / "pipeline_progress.json"
    binding_v17_audit_file = binding_v17_root / "training_only/historical_candidate_audit.json"
    binding_v17_result_file = binding_v17_root / "learning_v1/development_results.json"
    if binding_v17_progress_file.exists():
        binding_v17_progress = json.loads(binding_v17_progress_file.read_text())
        summary["expanded_binding_candidates_and_learning_v17"] = {
            "pipeline_progress": binding_v17_progress,
            "all_controls_share_visible_evidence_and_public_definition_hints": True,
            "no_additional_generative_visual_model_calls": True,
            "human_runtime_confirmations": 0,
            "candidate_coverage_and_predicted_semantic_tuple_do_not_certify_visual_header_ownership": True,
            "independent_native_test_complete": False,
            "ontology_unique_advantage_established": False,
            "architecture": "shared candidate/context matcher with independent component selections, then tuple assembly; not cross-component joint inference",
        }
        if binding_v17_audit_file.exists():
            audit_v17 = json.loads(binding_v17_audit_file.read_text())
            summary["expanded_binding_candidates_and_learning_v17"].update(
                historical_candidate_counts=audit_v17["counts"],
                baseline_historical_candidate_counts=audit_v17["baseline_counts"],
                nonempty_historical_dimensions=audit_v17["nonempty_historical_dimensions"],
                calendar_completions_remain_uncertain_hypotheses=True,
            )
            summary["result_files_sha256"][str(binding_v17_audit_file.relative_to(ROOT))] = sha(binding_v17_audit_file)
        if binding_v17_result_file.exists():
            result_v17 = json.loads(binding_v17_result_file.read_text())
            if not result_v17["complete"]:
                raise ValueError("Complete semantic-tuple development scores required")
            summary["expanded_binding_candidates_and_learning_v17"]["seed_means"] = result_v17["seed_means"]
            summary["expanded_binding_candidates_and_learning_v17"]["paired_comparisons"] = result_v17["paired_comparisons"]
            summary["result_files_sha256"][str(binding_v17_result_file.relative_to(ROOT))] = sha(binding_v17_result_file)
            review_file = binding_v17_root / "stage_completion_review.json"
            if review_file.exists():
                summary["expanded_binding_candidates_and_learning_v17"]["stage_completion_review"] = json.loads(review_file.read_text())
                summary["result_files_sha256"][str(review_file.relative_to(ROOT))] = sha(review_file)
    joint_v18_root = native / "joint_binding_v18"
    joint_v18_progress_file = joint_v18_root / "pipeline_progress.json"
    joint_v18_audit_file = joint_v18_root / "training_only/historical_feasibility.json"
    joint_v18_result_file = joint_v18_root / "learning_v1/development_results.json"
    if joint_v18_progress_file.exists():
        joint_v18_progress = json.loads(joint_v18_progress_file.read_text())
        summary["whole_joint_state_development_v18"] = {
            "pipeline_progress": joint_v18_progress,
            "all_controls_score_whole_states_with_same_public_compatibility_features": True,
            "all_cases_in_a_fold_share_its_excluded_company_proposal_parent": True,
            "dash_zero_is_uncertain_semantic_option_with_unknown_preserved": True,
            "no_new_generative_visual_model_calls": True,
            "human_runtime_confirmations": 0,
            "independent_native_test_completed": False,
            "ontology_unique_advantage_established": False,
        }
        learning_progress_file = joint_v18_root / "learning_v1/progress.json"
        if learning_progress_file.exists():
            summary["whole_joint_state_development_v18"]["learning_progress"] = json.loads(learning_progress_file.read_text())
        if joint_v18_audit_file.exists():
            audit_v18 = json.loads(joint_v18_audit_file.read_text())
            summary["whole_joint_state_development_v18"]["historical_candidate_counts"] = audit_v18["counts"]
            summary["whole_joint_state_development_v18"]["historical_joint_beam_feasibility"] = audit_v18["own_company_excluded_proposal_beam_by_seed"]
            summary["result_files_sha256"][str(joint_v18_audit_file.relative_to(ROOT))] = sha(joint_v18_audit_file)
        beam_diagnosis_file = joint_v18_root / "training_only/beam_retention_diagnosis.json"
        if beam_diagnosis_file.exists():
            summary["whole_joint_state_development_v18"]["historical_beam_retention_diagnosis"] = json.loads(beam_diagnosis_file.read_text())
            summary["result_files_sha256"][str(beam_diagnosis_file.relative_to(ROOT))] = sha(beam_diagnosis_file)
        if joint_v18_result_file.exists():
            result_v18 = json.loads(joint_v18_result_file.read_text())
            if not result_v18["complete"]:
                raise ValueError("Complete v18 joint scores required")
            for key in ("seed_means", "paired_comparisons", "feedback_error_propagation"):
                summary["whole_joint_state_development_v18"][key] = result_v18[key]
            summary["result_files_sha256"][str(joint_v18_result_file.relative_to(ROOT))] = sha(joint_v18_result_file)
        joint_review_file = joint_v18_root / "control_integrity_review.json"
        if joint_review_file.exists():
            summary["whole_joint_state_development_v18"]["control_integrity_review"] = json.loads(joint_review_file.read_text())
            summary["result_files_sha256"][str(joint_review_file.relative_to(ROOT))] = sha(joint_review_file)
        joint_stage_review_file = joint_v18_root / "stage_completion_review.json"
        if joint_stage_review_file.exists():
            summary["whole_joint_state_development_v18"]["stage_completion_review"] = json.loads(joint_stage_review_file.read_text())
            summary["result_files_sha256"][str(joint_stage_review_file.relative_to(ROOT))] = sha(joint_stage_review_file)
    joint_v19_root = native / "joint_binding_v19"
    joint_v19_progress_file = joint_v19_root / "pipeline_progress.json"
    joint_v19_result_file = joint_v19_root / "learning_v1/development_results.json"
    if joint_v19_progress_file.exists():
        progress_v19 = json.loads(joint_v19_progress_file.read_text())
        study_v19 = {
            "pipeline_progress": progress_v19,
            "old_v18_beams_retained_with_four_additional_public_candidates_per_field_and_dimension": True,
            "all_controls_receive_the_same_expansion_and_training_budgets": True,
            "new_generative_visual_api_calls": 0,
            "human_runtime_confirmations": 0,
            "independent_native_test_completed": False,
            "ontology_unique_advantage_established": False,
        }
        for name, relative in (
            ("preparation_progress", "preparation_progress.json"),
            ("historical_candidate_comparison", "training_only/historical_comparison.json"),
            ("historical_beam_retention", "training_only/beam_retention_diagnosis.json"),
            ("learning_progress", "learning_v1/progress.json"),
            ("control_integrity_review", "control_integrity_review.json"),
            ("stage_completion_review", "stage_completion_review.json"),
        ):
            path = joint_v19_root / relative
            if path.exists():
                study_v19[name] = json.loads(path.read_text())
                summary["result_files_sha256"][str(path.relative_to(ROOT))] = sha(path)
        if joint_v19_result_file.exists():
            result_v19 = json.loads(joint_v19_result_file.read_text())
            if not result_v19["complete"]:
                raise ValueError("Complete v19 scores required")
            for key in ("seed_means", "paired_comparisons", "feedback_error_propagation"):
                study_v19[key] = result_v19[key]
            summary["result_files_sha256"][str(joint_v19_result_file.relative_to(ROOT))] = sha(joint_v19_result_file)
        summary["expanded_common_joint_beams_v19"] = study_v19
    header_audit_root = native / "header_supervision_audit_v20"
    header_audit_file = header_audit_root / "training_only/structure_audit.json"
    if header_audit_file.exists():
        header_audit = json.loads(header_audit_file.read_text())
        if not header_audit["complete"]:
            raise ValueError("Complete historical source declaration audit required")
        summary["historical_native_header_declaration_audit_v20"] = {
            "counts": header_audit["counts"],
            "source_declarations_are_not_render_verified_visual_ownership_labels": True,
            "missing_declarations_do_not_imply_no_visible_headers": True,
            "native_visual_scope_teacher_certified": False,
            "future_or_independent_reference_files_read": False,
            "human_runtime_confirmations": 0,
            "report_file_sha256": sha(header_audit_file),
        }
        summary["result_files_sha256"][str(header_audit_file.relative_to(ROOT))] = sha(header_audit_file)
    transfer_v21_root = native / "header_transfer_v21"
    transfer_v21_localization = transfer_v21_root / "localization/manifest.json"
    transfer_v21_progress = transfer_v21_root / "pipeline_progress.json"
    transfer_v21_result = transfer_v21_root / "comparison_summary.json"
    transfer_v21_conditions = ("full_transfer", "donor_pixels_zeroed", "localization_only")
    if transfer_v21_localization.exists():
        localization_v21 = json.loads(transfer_v21_localization.read_text())
        progress_v21 = (json.loads(transfer_v21_progress.read_text()) if transfer_v21_progress.exists()
                        else {"phase": "public_transfer_preparation"})
        study_v21 = {
            "pipeline_progress": progress_v21,
            "public_header_lexical_localization_counts": localization_v21["counts"],
            "conditions": list(transfer_v21_conditions),
            "same_localized_geometry_availability_candidates_labels_and_budgets": True,
            "image_removal_affects_donor_stream_only_original_crop_context_remains": True,
            "transfer_probabilities_are_not_native_visual_ownership_certificates": True,
            "human_runtime_confirmations": 0,
            "new_generative_visual_calls": 0,
            "all_original_experiments_completed": False,
            "independent_native_test_completed": False,
        }
        for name, relative in (
            ("native_word_features", "native_word_features/manifest.json"),
            ("native_feature_progress", "native_word_features/progress.json"),
            ("donor_inference", "donor_predictions/manifest.json"),
            ("donor_inference_progress", "donor_predictions/progress.json"),
            ("stage_completion_review", "stage_completion_review.json"),
        ):
            path = transfer_v21_root / relative
            if path.exists():
                study_v21[name] = json.loads(path.read_text())
                summary["result_files_sha256"][str(path.relative_to(ROOT))] = sha(path)
        study_v21["learning_progress"] = {}
        for condition in transfer_v21_conditions:
            path = transfer_v21_root / ("learning_" + condition) / "progress.json"
            if path.exists():
                study_v21["learning_progress"][condition] = json.loads(path.read_text())
        if transfer_v21_result.exists():
            study_v21["development_comparison"] = json.loads(transfer_v21_result.read_text())
            summary["result_files_sha256"][str(transfer_v21_result.relative_to(ROOT))] = sha(transfer_v21_result)
        summary["native_header_scope_transfer_v21"] = study_v21
        summary["result_files_sha256"][str(transfer_v21_localization.relative_to(ROOT))] = sha(transfer_v21_localization)
    diagnosis_v22 = native / "transfer_diagnosis_v22/training_only/failure_attribution.json"
    if diagnosis_v22.exists():
        diagnostic = json.loads(diagnosis_v22.read_text())
        summary["historical_transfer_failure_attribution_v22"] = {
            key: diagnostic[key] for key in (
                "complete", "completed_utc", "historical_targets", "company_identifiers", "seeds",
                "per_configuration", "candidate_value_header_link_counts", "semantic_value_transfer_discrimination",
                "post_hoc_historical_diagnostic_not_new_method_or_independent_test",
                "future_or_independent_reference_files_read", "native_visual_scope_accuracy_measured",
            )
        }
        summary["result_files_sha256"][str(diagnosis_v22.relative_to(ROOT))] = sha(diagnosis_v22)
    witness_v23_root = native / "witness_composition_v23"
    witness_v23_public = witness_v23_root / "public/manifest.json"
    study_v23 = {}
    if witness_v23_public.exists():
        for name, relative in (
            ("common_public_witness_inventory", "public/manifest.json"),
            ("common_witness_features", "features/manifest.json"),
            ("historical_candidate_and_witness_audit", "training_only/candidate_and_witness_feasibility.json"),
            ("implementation_review", "implementation_review.json"),
            ("engineering_preflight", "engineering_preflight.json"),
            ("learning_design", "learning_v1/design.json"),
            ("learning_progress", "learning_v1/progress.json"),
            ("prediction_manifest", "learning_v1/manifest.json"),
            ("development_results", "learning_v1/development_results.json"),
            ("control_integrity_review", "learning_v1/control_integrity_review.json"),
            ("finalization_progress", "learning_v1/finalization_progress.json"),
            ("stage_completion_review", "learning_v1/stage_completion_review.json"),
            ("posthoc_selection_diagnosis", "posthoc_development_v24/selection_diagnosis.json"),
            ("candidate_omission_audit", "posthoc_candidate_omissions_v25/v2_original_public_text/candidate_omission_audit.json"),
        ):
            path = witness_v23_root / relative
            if path.exists():
                value = json.loads(path.read_text())
                if name == "historical_candidate_and_witness_audit":
                    value = {key: value[key] for key in (
                        "complete", "audited_utc", "counts", "candidate_upper_bounds_and_text_location_counts_are_not_model_accuracies",
                        "located_text_is_not_certified_header_ownership", "future_or_reserved_independent_reference_files_read")}
                elif name == "development_results":
                    value = {key: value[key] for key in (
                        "complete", "completed_utc", "unique_future_targets", "seed_means", "paired_comparisons", "feedback_error_propagation")}
                elif name == "prediction_manifest":
                    value = {key: value[key] for key in ("complete", "completed_utc", "design_sha256", "prediction_files")}
                elif name == "posthoc_selection_diagnosis":
                    value = {key: value[key] for key in (
                        "complete", "completed_utc", "unique_development_targets",
                        "public_candidate_availability_counts_not_accuracies",
                        "geometry_effects_conditioned_on_other_components",
                        "primary_policy_10_feedback_selection_and_period_errors",
                        "post_hoc_reused_development_diagnosis_not_independent_advantage_evidence")}
                elif name == "candidate_omission_audit":
                    value = {key: value[key] for key in (
                        "complete", "completed_utc", "development_only", "unique_development_targets",
                        "field_omission_counts", "missing_period_overlapping_clue_counts",
                        "missing_period_disjoint_clue_counts_by_kind",
                        "candidate_generators_predictions_or_original_scores_modified")}
                study_v23[name] = value
                summary["result_files_sha256"][str(path.relative_to(ROOT))] = sha(path)
        summary["full_value_visual_witness_composition_v23"] = study_v23
        summary["active_next_experiment"] = {
            "name": "full_value_visual_witness_composition_v23",
            "phase": study_v23.get("learning_progress", {}).get("phase", "full_learning_preparation"),
            "training_started": study_v23.get("learning_progress", {}).get("training_started", False),
            "development_scores_complete": "development_results" in study_v23,
            "independent_native_test_completed": False,
        }
    source_metadata_audit_file = native / "source_qualification/metadata_only_v1/audit.json"
    if source_metadata_audit_file.exists():
        source_metadata_audit = json.loads(source_metadata_audit_file.read_text())
        summary["native_source_metadata_prequalification"] = {
            "counts": source_metadata_audit["counts"],
            "counts_by_tentative_role": source_metadata_audit["counts_by_tentative_role"],
            "reserved_or_calibration_report_images_instance_facts_or_labels_opened": False,
            "independent_source_eligibility_certified": False,
            "portal_order_does_not_certify_original_publication_chronology": True,
        }
        summary["result_files_sha256"][str(source_metadata_audit_file.relative_to(ROOT))] = sha(source_metadata_audit_file)
    historical_grid_audit_file = native / "historical_grid_supervision_v24/training_only/grid_audit.json"
    if historical_grid_audit_file.exists():
        historical_grid_audit = json.loads(historical_grid_audit_file.read_text())
        summary["historical_source_grid_audit_v24"] = {
            key: historical_grid_audit[key] for key in (
                "complete", "audited_utc", "counts",
                "source_grid_and_semantic_consistency_are_not_visible_ownership_certificates",
                "year_cues_are_not_complete_periods", "browser_geometry_and_visibility_verified",
                "additional_training_witnesses_qualified", "not_supplied_to_frozen_v23_training",
                "original_280_targets_all_retained",
                "future_or_reserved_independent_source_assets_or_references_read",
            )
        }
        summary["result_files_sha256"][str(historical_grid_audit_file.relative_to(ROOT))] = sha(historical_grid_audit_file)
    historical_year_v24 = {}
    for name, relative in (
        ("render_geometry", "training_only/render_geometry_v1/audit.json"),
        ("common_ocr_year_links", "training_only/ocr_weak_year_v1/manifest.json"),
        ("common_year_link_features", "training_only/ocr_weak_year_v1/geometry_features/manifest.json"),
        ("auxiliary_engineering_preflight", "training_only/ocr_weak_year_v1/geometry_features/engineering_preflight.json"),
        ("joint_binding_engineering_preflight", "joint_binding_v1/engineering_preflight.json"),
        ("historical_inference_cost_probe", "joint_binding_v1/historical_inference_cost_probe.json"),
    ):
        path = native / "historical_grid_supervision_v24" / relative
        if path.exists():
            historical_year_v24[name] = json.loads(path.read_text())
            summary["result_files_sha256"][str(path.relative_to(ROOT))] = sha(path)
    if historical_year_v24:
        summary["historical_weak_year_correspondences_v24"] = historical_year_v24
    registry_root = native / "source_qualification/registry_metadata_v1"
    registry_collection_file = registry_root / "collection.json"
    registry_progress_file = registry_root / "progress.json"
    registry_metadata = {}
    if registry_progress_file.exists():
        registry_metadata["last_collector_progress"] = json.loads(registry_progress_file.read_text())
    if registry_collection_file.exists():
        registry_collection = json.loads(registry_collection_file.read_text())
        registry_metadata.update(collection_complete=registry_collection["collection_complete"],
                                 counts=registry_collection["counts"],
                                 legal_group_template_or_original_publication_independence_certified=False)
        summary["result_files_sha256"][str(registry_collection_file.relative_to(ROOT))] = sha(registry_collection_file)
    registry_risk_file = registry_root / "current_registry_risk_audit.json"
    if registry_risk_file.exists():
        risk = json.loads(registry_risk_file.read_text())
        if not risk["complete"] or risk["metadata_collection_sha256"] != sha(registry_collection_file):
            raise ValueError("Complete source-matched current registry risk audit required")
        registry_metadata["current_registry_risk_audit"] = {
            "complete": True, "counts": risk["counts"],
            "exact_registration_identity_collisions": len(risk["exact_registration_identity_collisions"]),
            "exact_registered_alias_collisions_not_used_to_merge_entities": len(risk["exact_registered_alias_collisions_not_used_to_merge_entities"]),
            "current_relationships_not_assumed_valid_for_report_years": True,
            "independent_source_eligibility_certified": False,
        }
        summary["result_files_sha256"][str(registry_risk_file.relative_to(ROOT))] = sha(registry_risk_file)
    temporal_alias_file = registry_root / "temporal_alias_case_20261016.json"
    if temporal_alias_file.exists():
        alias = json.loads(temporal_alias_file.read_text())
        if not alias["complete"]:
            raise ValueError("Complete temporal alias review required")
        registry_metadata["temporal_alias_review"] = {key: alias[key] for key in (
            "complete", "registered_identifiers_differ",
            "source_history_shows_noncontemporaneous_use_of_the_shared_name",
            "no_filing_history_documents_or_financial_accounts_opened",
            "historical_group_business_or_template_independence_not_certified")}
        summary["result_files_sha256"][str(temporal_alias_file.relative_to(ROOT))] = sha(temporal_alias_file)
    if registry_metadata:
        summary["official_registry_identity_metadata"] = registry_metadata
    state = json.loads((ROOT / "execution_state.json").read_text())
    state.update(updated_utc=summary["updated_utc"], all_original_experiments_completed=False,
                 ontology_mechanism_advantage_established=False,
                 current_work=["v12 shared row/column-context DeepSeek observation", "automatic v12 equal-input field learning/scoring continuation"],
                 human_runtime_confirmations=0)
    if context_scores.exists():
        state.update(
            current_work=["v12 development result qualification and failure diagnosis"],
            v12_observation_training_scoring_complete=True,
            running_v12_observation_or_training=False,
            required_remaining=summary["required_remaining"],
        )
    if binding_manifest_file.exists():
        state.update(
            current_work=["historical full-binding labels prepared; public candidate construction and model design pending"],
            historical_full_binding_teacher_preparation_complete=True,
            full_binding_model_training_started=False,
        )
    if binding_candidates_file.exists():
        state.update(
            current_work=["shared public quantity/unit/period candidates prepared; entity/dimension resolution and joint model pending"],
            public_binding_component_candidate_preparation_complete=True,
            canonical_entity_and_dimension_resolution_complete=False,
            full_binding_model_training_started=False,
        )
    if replay_progress_file.exists():
        state.update(
            current_work=(["v15 field replay scores complete; full-binding entity/dimension resolution and joint model pending"]
                          if replay_progress["phase"] == "complete" else ["same-input historical-replay field update comparison"]),
            field_replay_pipeline_phase=replay_progress["phase"],
            field_replay_development_scores_complete=replay_result_file.exists(),
            full_binding_model_training_started=False,
        )
    if binding_v16_progress_file.exists():
        state.update(
            current_work=(["v16 historical candidate audit complete; period/dimension candidate coverage repair required before full joint binding training"]
                          if binding_v16_progress["phase"] == "candidate_preparation_complete"
                          else ["v16 entity/dimension candidate preparation: " + binding_v16_progress["phase"]]),
            entity_dimension_candidate_pipeline_phase=binding_v16_progress["phase"],
            canonical_entity_dimension_candidate_preparation_complete=binding_v16_manifest_file.exists(),
            canonical_entity_and_dimension_resolution_complete=False,
            full_binding_model_training_started=False,
        )
        if binding_v16_audit_file.exists():
            state["historical_joint_candidate_representability"] = {
                "targets": binding_v16_audit["counts"]["historical_targets"],
                "all_components_representable_without_certifying_scope": binding_v16_audit["counts"]["all_components_representable_without_certifying_scope"],
                "not_model_accuracy_or_full_visible_scope_certification": True,
            }
    if binding_v17_progress_file.exists():
        phase_v17 = binding_v17_progress["phase"]
        state.update(
            current_work=(["v17 independent-component tuple study complete; ontology advantage absent; cross-component joint inference and numeric evidence repair pending"]
                          if phase_v17 == "development_semantic_tuple_scores_complete" else ["v17 complete semantic-tuple development pipeline: " + phase_v17]),
            full_binding_pipeline_phase=phase_v17,
            full_binding_model_training_started=binding_v17_progress.get("full_semantic_tuple_training_started", False),
            full_binding_model_development_scoring_complete=binding_v17_result_file.exists(),
            certified_visual_header_scope_experiment_complete=False,
            independent_native_test_completed=False,
            cross_component_joint_inference_test_complete=False,
        )
        if binding_v17_audit_file.exists():
            state["historical_joint_candidate_representability"] = {
                "targets": audit_v17["counts"]["historical_targets"],
                "all_components_representable_without_certifying_scope": audit_v17["counts"]["all_components_representable_without_certifying_scope"],
                "period_candidates_contain_historical_answer": audit_v17["counts"]["period"],
                "nonempty_dimension_tuples_representable": audit_v17["nonempty_historical_dimensions"],
                "not_model_accuracy_or_full_visible_scope_certification": True,
            }
    if joint_v18_progress_file.exists():
        state.update(
            current_work=(["v18 joint development study complete; candidate filtering, visual header ownership and independent testing remain"]
                          if joint_v18_result_file.exists() else ["v18 whole-joint-state pipeline: " + joint_v18_progress["phase"]]),
            joint_state_pipeline_phase=joint_v18_progress["phase"],
            cross_component_joint_inference_training_started=joint_v18_progress.get("joint_state_training_started", False),
            cross_component_joint_inference_development_scoring_complete=joint_v18_result_file.exists(),
            cross_component_joint_inference_test_complete=False,
            independent_native_test_completed=False,
            certified_visual_header_scope_experiment_complete=False,
        )
    if joint_v18_audit_file.exists():
        state["historical_joint_candidate_representability"] = {
            "targets": audit_v18["counts"]["historical_targets"],
            "all_components_representable_without_certifying_scope": audit_v18["counts"]["all_components_representable_without_certifying_scope"],
            "own_company_excluded_joint_beam_by_seed": audit_v18["own_company_excluded_proposal_beam_by_seed"],
            "not_model_accuracy_or_full_visible_scope_certification": True,
        }
    if joint_v19_progress_file.exists():
        state.update(
            current_work=["v19 expanded common joint beams: " + progress_v19["phase"]],
            v19_pipeline_phase=progress_v19["phase"],
            v19_development_scores_complete=joint_v19_result_file.exists(),
            independent_native_test_completed=False,
            certified_visual_header_scope_experiment_complete=False,
        )
        v19_audit_file = joint_v19_root / "training_only/historical_feasibility.json"
        if v19_audit_file.exists():
            audit_v19 = json.loads(v19_audit_file.read_text())
            state["historical_joint_candidate_representability"] = {
                "targets": audit_v19["counts"]["historical_targets"],
                "all_components_representable_without_certifying_scope": audit_v19["counts"]["all_components_representable_without_certifying_scope"],
                "own_company_excluded_joint_beam_by_seed": audit_v19["own_company_excluded_proposal_beam_by_seed"],
                "version": "v19",
                "not_model_accuracy_or_full_visible_scope_certification": True,
            }
    if header_audit_file.exists():
        state.update(
            current_work=["v19 model comparison complete; historical native header declarations audited; visual ownership supervision and transfer remain"],
            historical_native_header_declaration_audit_complete=True,
            native_explicit_header_associations_available=header_audit["counts"]["qualified_publisher_header_association_available"],
            native_visual_scope_teacher_certified=False,
            native_visual_scope_ownership_training_started=False,
            certified_visual_header_scope_experiment_complete=False,
            independent_native_test_completed=False,
        )
    if transfer_v21_localization.exists():
        state.update(
            current_work=["v21 matched header-scope transfer development: " + progress_v21["phase"]
                          + (" / " + progress_v21["condition"] if progress_v21.get("condition") else "")],
            v21_pipeline_phase=progress_v21["phase"],
            v21_public_header_localization_complete=True,
            v21_native_donor_inference_complete=(transfer_v21_root / "donor_predictions/manifest.json").exists(),
            v21_development_scores_complete=transfer_v21_result.exists(),
            native_visual_scope_teacher_certified=False,
            native_visual_scope_ownership_training_started=False,
            independent_native_test_completed=False,
            certified_visual_header_scope_experiment_complete=False,
        )
        if "stage_completion_review" in study_v21:
            state.update(
                current_work=["v21 development comparison and review complete; stable transfer or ontology-specific advantage not established"],
                v21_stage_review_complete=True,
                v21_running=False,
                next_required_work=study_v21["stage_completion_review"]["next_required_work"],
            )
    if witness_v23_public.exists():
        learning_progress_v23 = study_v23.get("learning_progress", {})
        v23_started = learning_progress_v23.get("training_started", False)
        state.update(
            current_work=["v23 full-value witnessed binding: " + learning_progress_v23.get("phase", "full_learning_preparation")],
            historical_transfer_failure_diagnosis_v22_complete=diagnosis_v22.exists(),
            v23_public_witness_inventory_complete=True,
            v23_common_witness_features_complete="common_witness_features" in study_v23,
            v23_historical_candidate_audit_complete="historical_candidate_and_witness_audit" in study_v23,
            v23_mechanism_implementation_review_complete="implementation_review" in study_v23,
            v23_full_learning_adapter_ready="engineering_preflight" in study_v23,
            v23_training_started=v23_started,
            v23_pipeline_phase=learning_progress_v23.get("phase", "full_learning_preparation"),
            v23_development_scores_complete="development_results" in study_v23,
            v23_control_integrity_review_complete="control_integrity_review" in study_v23,
            v23_development_stage_review_complete="stage_completion_review" in study_v23,
            v23_finalization_phase=study_v23.get("finalization_progress", {}).get("phase", "not_started"),
            native_weak_scope_learning_started=v23_started,
            native_visual_scope_ownership_training_started=v23_started,
            native_scope_learning_supervision="Historical semantic values only; native visual ownership labels remain uncertified",
            native_visual_scope_teacher_certified=False,
            independent_native_test_completed=False,
            next_required_work=[
                {"status": "complete" if "prediction_manifest" in study_v23 else "in_progress" if v23_started else "pending", "task": "Train and freeze all full-graph controls using the same evidence, feedback, case sequences and optimizer budgets; measure actual inference/training costs."},
                {"status": "complete" if "control_integrity_review" in study_v23 else "pending", "task": "Independently audit all 126 parent and 504 local checkpoints, actual case sequences, and whether learned geometry is used as positive coverage or exclusion."},
                {"status": "complete" if "development_results" in study_v23 else "pending", "task": "Evaluate all full bindings and feedback error propagation after every prediction freezes; test whether any benefit persists against strong memory and generic reasoning."},
                {"status": "pending", "task": "Complete source/template/chronology qualification, natural change tests and reserved independent evaluation without changing frozen acceptance criteria."},
            ],
        )
    if source_metadata_audit_file.exists():
        state.update(
            native_source_metadata_roster_audit_complete=True,
            independent_source_eligibility_certified=False,
            actual_publication_chronology_certified=False,
        )
    if historical_grid_audit_file.exists():
        state.update(
            historical_source_grid_audit_v24_complete=historical_grid_audit["complete"],
            historical_grid_render_geometry_verified=historical_grid_audit["browser_geometry_and_visibility_verified"],
            additional_historical_grid_training_witnesses_qualified=historical_grid_audit["additional_training_witnesses_qualified"],
        )
    if historical_year_v24:
        state.update(
            v24_historical_render_geometry_audit_complete="render_geometry" in historical_year_v24,
            v24_common_weak_year_correspondences_prepared="common_ocr_year_links" in historical_year_v24,
            v24_common_weak_year_features_prepared="common_year_link_features" in historical_year_v24,
            v24_auxiliary_engineering_preflight_complete="auxiliary_engineering_preflight" in historical_year_v24,
            v24_auxiliary_preflight_is_not_an_accuracy_result=True,
            v24_joint_binding_adapter_preflight_complete="joint_binding_engineering_preflight" in historical_year_v24,
            v24_historical_inference_and_head_only_ablation_checked="historical_inference_cost_probe" in historical_year_v24,
            v24_native_full_period_or_scope_ownership_teacher_certified=False,
            v24_formal_full_binding_experiment_started=False,
        )
    if registry_metadata:
        state.update(
            official_registry_metadata_collection_complete=registry_metadata.get("collection_complete", False),
            registry_metadata_collector_last_phase=registry_metadata.get("last_collector_progress", {}).get("phase", "unknown"),
            registry_metadata_collection_is_not_source_independence_certification=True,
            official_registry_current_risk_audit_complete="current_registry_risk_audit" in registry_metadata,
            official_registry_temporal_alias_review_complete="temporal_alias_review" in registry_metadata,
        )
    if "posthoc_selection_diagnosis" in study_v23:
        state.update(
            current_work=["v23 completed without scope advantage; auditing shared candidate omissions and field selection before further training"],
            v23_posthoc_development_selection_diagnosis_complete=True,
            v25_shared_candidate_omission_audit_complete="candidate_omission_audit" in study_v23,
            v25_formal_full_binding_experiment_started=False,
        )
        state["next_required_work"].insert(-1, {
            "status": "pending",
            "task": "Repair common field and complete-period candidate omissions using public evidence only, then improve field correspondence and repeat strong-control attribution; keep original scores and reserved sources unchanged.",
        })
        summary["active_next_experiment"].update(
            phase="postfreeze_candidate_diagnosis",
            full_training_stage_complete=True,
            new_formal_training_started=False,
            next_priority="common field/period candidate repair and field correspondence before year-only scope training",
        )
    save(ROOT / "continuation_summary_20261014.json", summary)
    state["summary_sha256"] = sha(ROOT / "continuation_summary_20261014.json")
    save(ROOT / "execution_state.json", state)
    print("Saved verified component and development progress; independent tests remain incomplete", flush=True)


if __name__ == "__main__":
    run()

"""Reconcile only completed, hashed experimental artifacts into progress state."""

import json
from datetime import datetime, timezone

from prepare import OUT, save, sha


def read(path):
    return json.loads(path.read_text())


def main():
    common = OUT / "canonicalization_crop_learning_v3"
    paths = {
        "initial_learning": common
        / "program_correction_learning/development_results.json",
        "strong_controls": common / "mechanism_controls_v4/development_results.json",
        "component_attribution": common
        / "program_attribution_v5/development_results.json",
    }
    results = {name: read(path) for name, path in paths.items()}
    for name, path in paths.items():
        manifest = read(path.parent / "inference_manifest.json")
        if (
            not manifest["complete"]
            or sha(path.parent / "predictions.json") != manifest["predictions_sha256"]
        ):
            raise RuntimeError(
                "Only complete frozen experiments can update status: " + name
            )
    chart_path = OUT / "chart_native_api_corrected_v2/inference_manifest.json"
    chart = read(chart_path)
    if (
        not chart["complete"]
        or sha(chart_path.parent / "outcomes.json") != chart["outcomes_sha256"]
    ):
        raise RuntimeError("Chart API qualification incomplete")
    observations = read(common / "common_visual/manifest.json")
    tasks = read(common / "public/tasks.json")
    task_map = {t["task_id"]: t for t in tasks}
    failed = [
        i
        for batch in observations["records"]
        if batch["status"] != "completed"
        for i in batch["task_ids"]
    ]
    table = {
        name: {
            f"{r['method']}:{r['local_automatic_feedback']}": r[
                "mean_forced_quantity_accuracy"
            ]
            for r in result["summaries"]
        }
        for name, result in results.items()
    }
    summary = {
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "primary_generation_and_visual_model": "DeepSeek Flash; no Qwen generation or inference",
        "fixed_shared_feature_encoders": ["LayoutLMv3", "MiniLM"],
        "human_confirmations": 0,
        "public_assisted_targets": len(tasks),
        "future_development_targets": 560,
        "past_2023_targets": 280,
        "legal_entity_identifiers": 14,
        "local_automatic_feedback_budgets": [0, 5, 10],
        "local_zero_does_not_mean_zero_total_training_labels": True,
        "all_controls_share_other_company_2023_pretraining": True,
        "new_trained_configurations": {
            "initial_learning": 378,
            "strong_controls": 378,
            "component_attribution": 756,
        },
        "unique_future_predictions_including_one_common_visual_condition": 61040,
        "same_field_region_multiplier_oracles_for_every_control": True,
        "common_observation_batches": {
            "processed": 202,
            "completed": 185,
            "attempt_budget_exhausted": 17,
        },
        "common_observation_failed_targets": len(failed),
        "common_observation_failed_future_targets": sum(
            task_map[i]["source_year"] in (2024, 2025) for i in failed
        ),
        "quantity_results": table,
        "interpretation_zh": [
            "在辅助条件下，按数值程序的组成部分建模，比本轮通用联合模型和学习型记忆有更高的开发集数值正确率。",
            "去掉修正器的字段特征后，正确率从 66.90% 变为 66.55%，不能将整体提升归因于额外的字段语义或本体约束。共同的视觉基础预测仍含字段信息。",
            "乘性交互与加性交互版本基本相同；目前没有证据支持乘性交互本身是关键。",
            "去掉修正器的局部上下文特征后降至 64.11%。该特征块包含文字、角色提议、图片特征和位置，不能将差异全归因于视觉特征。",
            "去掉图片编码及位置特征后为 65.65%；其差异的公司分组置信区间包含零，独立视觉特征贡献尚未确认。",
            "5 条本地反馈在三次重复中各修正 9 至 11 个原错误，同时使 27 至 30 个原正确答案变错；全参数局部微调存在明显负迁移。",
            "新来源独立验证、完整可见范围归属和本体独有收益仍未成立，不能据此宣称 CVPR 方法优势或 SOTA。",
        ],
        "chartqapro_native_api_qualification": {
            "records": chart["records"],
            "statuses": chart["statuses"],
            "preserved_byte_identically": chart["byte_identical_preserved_records"],
            "registered_new_input_or_transport_records": chart[
                "registered_new_input_or_transport_records"
            ],
            "scored": False,
            "terminal_reference_answers_read": False,
            "uniform_image_policy_and_four_attempt_maximum_required_for_future_controls": True,
            "last_saved_account_balance_cny": read(
                chart_path.parent / "account_probe.json"
            )["current_balance_cny"],
        },
        "implementation_checks": {
            "unit_tests_passed": 111,
            "new_continuation_modules_lint": "PASS",
        },
        "new_mechanism_advantage_established": False,
        "full_native_visual_bindings_certified": False,
        "all_original_cvpr_experiments_completed": False,
        "running_work": [],
        "next_required_work_zh": [
            "构建不提供字段、倍率等答案相关辅助输入的对应任务；保留不可判定的字段、单位、期间和范围。",
            "训练可见证据与字段、单位、期间的对应及适用范围，而不是继续只对正负号修正器调参。数值一致性不能充当范围归属标签。",
            "采用冻结基础模型的局部更新，比较受影响对应与未受影响对应的正确覆盖和错误传播；让强记忆和通用结构对照获得同样证据和自动反馈。",
            "在方法、来源去重及评分规则冻结后，开展未用来源上的完整绑定验证，并完成其余原定视觉实验。",
        ],
        "artifacts": {
            name: {"file": str(path), "sha256": sha(path)}
            for name, path in paths.items()
        }
        | {
            "chart_api_qualification": {
                "file": str(chart_path),
                "sha256": sha(chart_path),
            }
        },
    }
    save(OUT / "continuation_summary_20261014.json", summary)
    state_path = OUT / "execution_state.json"
    state = read(state_path)
    state["updated_utc"] = summary["updated_utc"]
    state["unit_tests_passed"] = 111
    state["implementation_checks"]["unit_tests_passed"] = 111
    state["implementation_checks"]["new_continuation_modules_lint"] = "PASS"
    state["last_balance_cny"] = summary["chartqapro_native_api_qualification"][
        "last_saved_account_balance_cny"
    ]
    state["last_balance_artifact"] = str(chart_path.parent / "account_probe.json")
    state["running_work"] = []
    state["new_mechanism_advantage_established"] = False
    state["all_original_cvpr_experiments_completed"] = False
    state["completed_development_results"][
        "shared_program_learning_and_attribution"
    ] = summary["quantity_results"]
    state["completed_development_results"]["shared_program_learning_limits"] = {
        "oracle_field_region_multiplier": True,
        "future_development_targets": 560,
        "pretraining_from_other_company_2023": True,
        "field_feature_extra_contribution_not_established": True,
        "multiplicative_interaction_extra_contribution_not_established": True,
        "local_feedback_can_cause_negative_transfer": True,
        "full_visible_scope_ownership_certified": False,
        "independent_source_advantage_established": False,
    }
    state["chart_native_API_corrected_v2"] = summary[
        "chartqapro_native_api_qualification"
    ]
    state["next_required_work"] = summary["next_required_work_zh"]
    for stage in state["stages"]:
        if stage["id"] == "E1":
            stage["status"] = (
                "assisted_program_learning_and_component_attribution_completed"
            )
            stage["dependency"] = (
                "Direct visual binding, independent range ownership, calibration and independent-source qualification remain"
            )
        elif stage["id"] == "E3":
            stage["status"] = "native_baseline_API_qualified_inference_only"
            stage["dependency"] = (
                "Full matched mechanism comparison and official scoring after main-method freeze"
            )
        elif stage["id"] == "E4":
            stage["status"] = (
                "assisted_0_5_10_development_completed_with_negative_transfer"
            )
            stage["dependency"] = (
                "Full visual binding reuse and error propagation; selective local update still required"
            )
    save(state_path, state)
    print(
        "Updated authoritative continuation state; no main-method advantage claimed",
        flush=True,
    )


if __name__ == "__main__":
    main()

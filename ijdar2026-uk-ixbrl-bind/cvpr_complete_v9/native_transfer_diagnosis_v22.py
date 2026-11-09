"""Historical-only failure attribution for the frozen v21 transfer experiment.

This is a post-hoc diagnostic, not another trained model or independent result.
The semantic teachers identify candidate values, never visual header ownership.
No 2024/2025 or reserved-source references are loaded. Frozen v21 parents are
queried on their excluded company's 2023 cases without local adaptation.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import native_joint_learning_v18 as learning
import numpy as np
import torch
from continue_native_transfer_v21 import CONDITIONS
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import historical_positive
from native_binding_prepare_v14 import DEST as TEACHERS
from native_header_alignment_v21 import DEST as INPUT
from native_joint_features_v18 import SEEDS, state_features
from native_scope_joint_features_v21 import REL_DIM, STATE_DIM
from prepare import save, sha

DEST = INPUT.parent / "transfer_diagnosis_v22"
METHODS = ("joint_prior", "dense_joint", "coupled_joint")
SCORE_NAMES = ("generic_probability", "generic_span_probability", "scope_probability")


def failure_category(full, beam, selected):
    """Disjoint attribution; unavailable correct values are not null targets."""
    if not all(full):
        return "correct_component_missing_from_full_candidates"
    if not all(beam):
        return "correct_component_pruned_from_joint_beam"
    return "semantic_tuple_exact" if all(selected) else "selection_error_with_correct_state_available"


def pair_separation(scores, positive, available):
    """Target-level correct/wrong semantic-value ranking, with half credit ties.

    Missing visual evidence cannot serve as a certified negative. This measures
    semantic candidate discrimination, not correctness of visual scope edges.
    """
    scores = np.asarray(scores, dtype=float)
    positive = np.asarray(positive, dtype=bool)
    available = np.asarray(available, dtype=bool)
    if scores.ndim != 1 or scores.shape != positive.shape or scores.shape != available.shape:
        raise ValueError("Aligned candidate vectors required")
    if not np.isfinite(scores).all():
        raise ValueError("Nonfinite probability")
    correct, wrong = scores[positive & available], scores[~positive & available]
    if not len(correct) or not len(wrong):
        return None
    delta = correct[:, None] - wrong[None, :]
    return float(np.mean((delta > 1e-8) + .5 * (np.abs(delta) <= 1e-8)))


def node_transfer_signals(states, relations, column, positives, rows):
    indices, first = np.unique(states[:, column], return_index=True)
    keep = np.array([j < len(rows) and rows[j]["value"] is not None for j in indices])
    indices, first = indices[keep], first[keep]
    offset = 8 if column == 1 else 12
    values = relations[first, offset:offset + 4]
    # Donor features must be node-specific, independent of the other choices.
    for j, value in zip(indices, values, strict=True):
        if not np.allclose(relations[states[:, column] == j, offset:offset + 4], value, rtol=0, atol=1e-8):
            raise ValueError("A node's transfer evidence depends on another state component")
    positive = positives[indices]
    available = values[:, 3] > .5
    return {
        "correct_semantic_value_in_beam": bool(positive.any()),
        "correct_semantic_value_has_available_transfer": bool((positive & available).any()),
        "wrong_semantic_value_has_available_transfer": bool((~positive & available).any()),
        "both_correct_and_wrong_have_available_transfer": bool((positive & available).any() and (~positive & available).any()),
        "available_transfer_is_constant_across_semantic_candidates": bool(available.sum() > 1 and np.all(np.ptp(values[available, :3], axis=0) <= 1e-8)),
        "semantic_candidate_pair_separation": {
            name: pair_separation(values[:, k], positive, available)
            for k, name in enumerate(SCORE_NAMES)
        },
        "native_visual_scope_accuracy_measured": False,
    }


def condition_relations(condition, relations, pixels_zeroed):
    result = relations.copy()
    if condition == "donor_pixels_zeroed":
        result[:, -8:] = pixels_zeroed
    elif condition == "localization_only":
        result[:, [-8, -7, -6, -4, -3, -2]] = 0
    elif condition != "full_transfer":
        raise ValueError("Unknown frozen condition")
    return result


def historical_teacher(task, teachers):
    if task["source_year"] != 2023:
        raise ValueError("Future or independent semantic references are forbidden")
    teacher = teachers[task["task_id"]]
    if teacher["source_year"] != 2023 or teacher["task_id"] != task["task_id"]:
        raise ValueError("Historical semantic teacher identity differs")
    return teacher


def run():
    output = DEST / "training_only/failure_attribution.json"
    if output.exists():
        raise RuntimeError("Historical diagnostic is already frozen")
    torch.set_num_threads(2)
    feature_file = INPUT / "features/manifest.json"
    feature = json.loads(feature_file.read_text())
    task_file, record_file = INPUT / "candidates/tasks.json", INPUT / "features/records.json"
    context_file = INPUT / "features/context.npy"
    if not feature["complete"] or sha(task_file) != feature["public_candidate_sha256"]:
        raise ValueError("Frozen v21 public inventory required")
    for path in (record_file, context_file):
        if sha(path) != feature["files"][path.name]:
            raise ValueError("Frozen shared context/records changed")
    teacher_file = TEACHERS / "training_only/past_binding_feedback.json"
    if sha(teacher_file) != json.loads((TEACHERS / "manifest.json").read_text())["teacher_sha256"]:
        raise ValueError("Historical teacher changed")
    tasks, records, teachers = [json.loads(p.read_text()) for p in (task_file, record_file, teacher_file)]
    historical = [(i, t) for i, t in enumerate(tasks) if t["source_year"] == 2023]
    if len(historical) != 280 or {t["task_id"] for _, t in historical} != set(teachers):
        raise ValueError("All 280 historical targets must remain")
    if sha(Path(learning.__file__)) != sha(INPUT / "learning_full_transfer/runner_snapshot.py"):
        raise ValueError("Frozen parent algorithm changed")
    contexts = torch.from_numpy(np.load(context_file))
    learning.STATE_DIM, learning.REL_DIM = STATE_DIM, REL_DIM
    models, weight_hashes, design_hashes = {}, {}, {}
    companies = sorted({t["company"] for _, t in historical})
    for condition in CONDITIONS:
        folder = INPUT / ("learning_" + condition)
        design = json.loads((folder / "design.json").read_text())
        design_hashes[condition] = sha(folder / "design.json")
        if design["historical_teacher_sha256"] != sha(teacher_file):
            raise ValueError("Parent teacher provenance differs")
        for company in companies:
            for seed in SEEDS:
                for method in METHODS[1:]:
                    p = folder / "parents" / company / str(seed) / method
                    metadata = json.loads((p / "metadata.json").read_text())
                    weight = p / "weights.pt"
                    if metadata["excluded_company"] != company or metadata["design_sha256"] != design_hashes[condition] or sha(weight) != metadata["weights_sha256"]:
                        raise ValueError("Historical query's company was not excluded from the frozen parent")
                    model = learning.JointMatcher(method, feature["context_dimension"])
                    model.load_state_dict(torch.load(weight, weights_only=True))
                    model.eval()
                    models[condition, company, seed, method] = model
                    weight_hashes[str(weight.relative_to(INPUT))] = metadata["weights_sha256"]
    counts = defaultdict(Counter)
    signals, target_rows = {}, {}
    header_link_counts = {c: Counter() for c in ("period", "unit_quantity")}
    for done, (i, task) in enumerate(historical, 1):
        ident, company = task["task_id"], task["company"]
        teacher = historical_teacher(task, teachers)
        positive = {c: historical_positive(task, records[i][c], teacher, c).numpy() for c in CHANNELS}
        full = [bool(positive[c].any()) for c in CHANNELS]
        for channel, counts_header in header_link_counts.items():
            correct = np.flatnonzero(positive[channel])
            linked = [bool((records[i][channel][j]["metadata"].get("header") or {}).get("id")) for j in correct]
            counts_header["historical_targets"] += 1
            counts_header["correct_value_proposed"] += bool(len(correct))
            counts_header["correct_value_has_model_header_id"] += any(linked)
            counts_header["correct_values_all_without_header_id"] += bool(len(correct)) and not any(linked)
        target_rows[ident] = {"company": company, "source_year": 2023, "full_candidate_components_available": dict(zip(CHANNELS, full, strict=True)), "results": {}}
        file = INPUT / "features" / (ident + ".npz")
        if sha(file) != feature["files"][file.name]:
            raise ValueError("Frozen historical feature archive changed")
        with np.load(file) as arrays:
            nodes = {c: arrays["node_" + c] for c in CHANNELS}
            for seed in SEEDS:
                suffix = company + "_" + str(seed)
                states = arrays["states_" + suffix]
                unary = arrays["unary_" + suffix]
                original_relations = arrays["relations_" + suffix]
                beam = [bool(positive[c][states[:, j]].any()) for j, c in enumerate(CHANNELS)]
                for condition in CONDITIONS:
                    relations = condition_relations(condition, original_relations, arrays["scope_pixels_zeroed_" + suffix])
                    values = torch.from_numpy(state_features(nodes, states, unary, relations))
                    prior = torch.from_numpy(unary.sum(1) - (relations[:, 0] * (1 - relations[:, 1]) + relations[:, 2] * (1 - relations[:, 3])))
                    for column, channel in ((1, "period"), (3, "unit_quantity")):
                        signal = node_transfer_signals(states, relations, column, positive[channel], records[i][channel])
                        signals[f"{ident}/{condition}/{seed}/{channel}"] = signal
                    with torch.no_grad():
                        for method in METHODS:
                            logits = prior if method == "joint_prior" else prior + models[condition, company, seed, method](values, contexts[i])
                            selected = states[int(logits.argmax())]
                            matched = [bool(positive[c][j]) for c, j in zip(CHANNELS, selected, strict=True)]
                            category = failure_category(full, beam, matched)
                            key = f"{condition}/{method}/{seed}"
                            count = counts[key]
                            count["historical_targets"] += 1
                            count[category] += 1
                            for c, a, b, m in zip(CHANNELS, full, beam, matched, strict=True):
                                count["full_candidate_available/" + c] += a
                                count["beam_available/" + c] += b
                                count["selected_correct/" + c] += m
                            count["all_components_in_full_candidates"] += all(full)
                            count["all_components_in_beam"] += all(beam)
                            target_rows[ident]["results"][key] = {"category": category, "selected_component_correct": dict(zip(CHANNELS, matched, strict=True)), "beam_component_available": dict(zip(CHANNELS, beam, strict=True))}
        if done % 40 == 0:
            print("Historical transfer diagnosis", done, "/ 280", flush=True)
    signal_summary = {}
    for condition in CONDITIONS:
        for seed in SEEDS:
            for channel in ("period", "unit_quantity"):
                rows = [signals[f"{t['task_id']}/{condition}/{seed}/{channel}"] for _, t in historical]
                summary = {k: sum(r[k] for r in rows) for k in rows[0] if k not in {"semantic_candidate_pair_separation", "native_visual_scope_accuracy_measured"}}
                summary["historical_targets"] = len(rows)
                summary["semantic_candidate_pair_separation"] = {}
                for name in SCORE_NAMES:
                    values = [r["semantic_candidate_pair_separation"][name] for r in rows if r["semantic_candidate_pair_separation"][name] is not None]
                    summary["semantic_candidate_pair_separation"][name] = {"eligible_targets": len(values), "mean": float(np.mean(values)) if values else None}
                signal_summary[f"{condition}/{seed}/{channel}"] = summary
    for row in counts.values():
        categories = sum(row[c] for c in ("correct_component_missing_from_full_candidates", "correct_component_pruned_from_joint_beam", "semantic_tuple_exact", "selection_error_with_correct_state_available"))
        if categories != 280:
            raise ValueError("Failure attribution is not an exhaustive disjoint partition")
    snapshot = DEST / "auditor_snapshot.py"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(output, {
        "complete": True, "completed_utc": datetime.now(timezone.utc).isoformat(),
        "post_hoc_historical_diagnostic_not_new_method_or_independent_test": True,
        "historical_targets": 280, "company_identifiers": len(companies), "seeds": list(SEEDS),
        "conditions": list(CONDITIONS), "frozen_parent_methods": list(METHODS),
        "no_local_feedback_or_optimizer_updates": True,
        "frozen_parent_query_company_excluded_from_training": True,
        "per_configuration": {k: dict(v) for k, v in counts.items()},
        "candidate_value_header_link_counts": {k: dict(v) for k, v in header_link_counts.items()},
        "semantic_value_transfer_discrimination": signal_summary,
        "per_target": target_rows,
        "per_target_transfer_signals": signals,
        "input_feature_manifest_sha256": sha(feature_file), "historical_teacher_sha256": sha(teacher_file),
        "frozen_parent_design_sha256": design_hashes, "frozen_parent_weights_sha256": weight_hashes,
        "auditor_snapshot_sha256": sha(snapshot),
        "future_or_independent_reference_files_read": False, "native_visual_scope_accuracy_measured": False,
        "human_runtime_confirmations": 0, "new_generative_visual_calls": 0,
        "ontology_unique_advantage_established": False, "all_original_cvpr_experiments_completed": False,
    })
    print("Historical transfer attribution complete", str(output), flush=True)


if __name__ == "__main__":
    run()

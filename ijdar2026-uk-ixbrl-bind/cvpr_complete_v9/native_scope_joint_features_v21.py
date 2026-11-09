"""Same relocated geometry and donor scores for every v21 binding control."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from native_binding_features_v17 import CHANNELS
from native_header_alignment_v21 import DEST
from native_joint_features_v18 import NODE_DIM, SEEDS, header_alignment
from native_joint_features_v19 import DEST as PREVIOUS
from prepare import save, sha

REL_DIM = 16
STATE_DIM = 4 * NODE_DIM + 4 + REL_DIM


def donor_vector(row):
    generic, scope = row.get("generic", {}), row.get("scope", {})
    a, b = generic.get("available_transfer_hypothesis", False), scope.get("available_transfer_hypothesis", False)
    if a != b:
        raise ValueError("Donor controls have unequal available lexical anchors")
    if not a:
        return np.zeros(4, dtype="float32")
    values = [generic["mean_joint_probability"], generic["mean_optimal_span_joint_probability"], scope["mean_joint_probability"], 1.0]
    if not all(v is not None and np.isfinite(v) and 0 <= v <= 1 for v in values):
        raise ValueError("Invalid frozen donor hypothesis probability")
    return np.array(values, dtype="float32")


def transfer_mask(known):
    mask = torch.zeros(STATE_DIM)
    for j, value in enumerate(known):
        if value:
            mask[j * NODE_DIM:(j + 1) * NODE_DIM] = 1
            mask[4 * NODE_DIM + j] = 1
    f, p, d, q = known
    mask[4 * NODE_DIM + 4:4 * NODE_DIM + 12] = torch.tensor([f and p, f and p, f and q, f and q, f and d, d, p, q])
    mask[-8:-4] = float(p)
    mask[-4:] = float(q)
    return mask


def node_evidence(rows, task, public, predicted):
    values, alignment = [], []
    for row in rows:
        hid = (row.get("metadata", {}).get("header") or {}).get("id")
        scores = predicted.get(hid, {})
        vector = donor_vector(scores)
        values.append(vector)
        locations = {v["anchor_id"] for v in scores.get("generic", {}).get("locations", [])}
        anchors = [a for a in public["headers"].get(hid, {}).get("anchors", []) if a["anchor_id"] in locations]
        align = [header_alignment({"metadata": {"header": {"box": a["box"]}}}, task["target_box"]) for a in anchors]
        alignment.append(float(np.mean(align)) if align else 0.0)
    return np.asarray(values + [np.zeros(4)], dtype="float32"), np.array(alignment + [0.0], dtype="float32")


def prepare():
    folder = DEST / "features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Joint transfer feature inventory already frozen")
    old_file = PREVIOUS / "features/manifest.json"
    old = json.loads(old_file.read_text())
    transfer_file = DEST / "donor_predictions/manifest.json"
    transfer = json.loads(transfer_file.read_text())
    localization = json.loads((DEST / "localization/manifest.json").read_text())
    anchors_file = DEST / "localization/public_anchors.json"
    source_file = PREVIOUS / "candidates/tasks.json"
    if not old["complete"] or not transfer["complete"] or sha(source_file) != old["public_candidate_sha256"] or sha(anchors_file) != localization["public_anchors_sha256"]:
        raise ValueError("Frozen complete source and transfer inventories required")
    public = json.loads(anchors_file.read_text())
    tasks = json.loads(source_file.read_text())
    predictions = {}
    for name, digest in transfer["files_sha256"].items():
        path = DEST / "donor_predictions/pages" / name
        if sha(path) != digest:
            raise ValueError("Frozen donor predictions changed")
        page = json.loads(path.read_text())
        if page["design_sha256"] != transfer["design_sha256"] or set(page["targets"]) & set(predictions):
            raise ValueError("Changed or duplicate donor target inventory")
        predictions.update(page["targets"])
    if len(tasks) != 840 or set(predictions) != set(public) or {t["task_id"] for t in tasks} != set(public):
        raise ValueError("All 840 targets must remain in transfer")
    folder.mkdir(parents=True, exist_ok=True)
    (DEST / "candidates").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_file, DEST / "candidates/tasks.json")
    for name in ("context.npy", "ids.json", "records.json", "projection.npy"):
        if sha(PREVIOUS / "features" / name) != old["files"][name]:
            raise ValueError("Frozen native context/candidate features changed")
        shutil.copyfile(PREVIOUS / "features" / name, folder / name)
    records = json.loads((folder / "records.json").read_text())
    companies = sorted({t["company"] for t in tasks})
    for i, (task, record) in enumerate(zip(tasks, records, strict=True)):
        ident = task["task_id"]
        old_path = PREVIOUS / "features" / (ident + ".npz")
        if sha(old_path) != old["files"][old_path.name]:
            raise ValueError("Frozen v19 state space changed")
        with np.load(old_path) as archive:
            arrays = {"node_" + c: archive["node_" + c] for c in CHANNELS}
            for seed in SEEDS:
                observed = predictions[ident]["by_seed"][str(seed)]
                per, palign = node_evidence(record["period"], task, public[ident], observed["full_pixels"])
                qty, qalign = node_evidence(record["unit_quantity"], task, public[ident], observed["full_pixels"])
                per_zero, palign_zero = node_evidence(record["period"], task, public[ident], observed["pixels_zeroed"])
                qty_zero, qalign_zero = node_evidence(record["unit_quantity"], task, public[ident], observed["pixels_zeroed"])
                if not np.array_equal(palign, palign_zero) or not np.array_equal(qalign, qalign_zero) or not np.array_equal(per[:, 3], per_zero[:, 3]) or not np.array_equal(qty[:, 3], qty_zero[:, 3]):
                    raise ValueError("Pixel ablation changed shared lexical localization/availability")
                for company in companies if task["source_year"] == 2023 else [task["company"]]:
                    suffix = company + "_" + str(seed)
                    states = archive["states_" + suffix]
                    arrays["states_" + suffix] = states
                    arrays["unary_" + suffix] = archive["unary_" + suffix]
                    relations = archive["relations_" + suffix].copy()
                    # Same tighter lexical geometry in every condition. The
                    # original unary scores and all state choices stay frozen.
                    relations[:, 6] = palign[states[:, 1]]
                    relations[:, 7] = qalign[states[:, 3]]
                    extras = np.concatenate((per[states[:, 1]], qty[states[:, 3]]), 1)
                    arrays["relations_" + suffix] = np.concatenate((relations, extras), 1)
                    arrays["scope_pixels_zeroed_" + suffix] = np.concatenate((per_zero[states[:, 1]], qty_zero[states[:, 3]]), 1)
        np.savez_compressed(folder / (ident + ".npz"), **arrays)
        if (i + 1) % 140 == 0:
            print("Matched binding transfer features", i + 1, "/ 840", flush=True)
    snapshot = folder / "feature_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "targets": 840,
        "node_dimension": NODE_DIM, "state_dimension": STATE_DIM, "relation_dimension": REL_DIM,
        "context_dimension": old["context_dimension"], "public_candidate_sha256": sha(DEST / "candidates/tasks.json"),
        "previous_state_feature_manifest_sha256": sha(old_file), "donor_prediction_manifest_sha256": sha(transfer_file),
        "learning_adapter_sha256": sha(Path(__file__).with_name("continue_native_transfer_v21.py")),
        "all_cases_within_a_fold_use_its_same_excluded_company_proposal_parent": True,
        "all_controls_receive_same_joint_states_unary_scores_and_public_compatibility_features": True,
        "same_relocalized_geometry_in_all_three_conditions": True,
        "states_nodes_and_unary_values_unchanged_from_v19": True,
        "new_scope_probabilities_are_uncalibrated_transfer_hypotheses_not_certificates": True,
        "donor_and_native_legal_entity_template_overlap_not_certified": True,
        "files": {p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.name != "manifest.json"},
        "future_or_independent_reference_files_read": False, "human_runtime_confirmations": 0,
    })


if __name__ == "__main__":
    prepare()

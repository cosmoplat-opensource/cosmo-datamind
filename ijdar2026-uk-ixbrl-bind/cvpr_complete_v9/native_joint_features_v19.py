"""Expand frozen v18 beams with four additional public-prior hypotheses.

The old states, features and scores remain an exact subset. No new observation,
teacher or future answer is used. Every control receives the same expansion.
"""

import itertools
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from native_binding_candidates_v18 import DEST as PREVIOUS
from native_binding_expand_v17 import DEST as UNARY
from native_binding_features_v17 import CHANNELS
from native_binding_learning_v17 import BindingMatcher, prior
from native_dimension_network_v17 import DEST as NETWORK
from native_joint_features_v18 import (
    NODE_DIM,
    REL_DIM,
    SEEDS,
    STATE_DIM,
    header_alignment,
    unit_compatibility,
)
from prepare import save, sha

DEST = PREVIOUS.parent / "joint_binding_v19"
ADDITIONAL_PUBLIC_CANDIDATES = 4


def ordered_column(states, column):
    values, first = np.unique(states[:, column], return_index=True)
    return values[np.argsort(first)].tolist()


def expanded_beam(rows, old, scores, channel):
    sentinel = len(rows)
    if len(set(old)) != len(old) or old[-1:] != [sentinel]:
        raise ValueError("Old beam must retain one final explicit unknown")
    if channel not in {"field", "dimensions"}:
        return list(old)
    chosen = list(old[:-1])
    values = {json.dumps(rows[i]["value"], sort_keys=True) for i in chosen}
    count = 0
    for i in sorted(range(len(rows)), key=lambda j: (-float(scores[j]), j)):
        value = rows[i]["value"]
        key = json.dumps(value, sort_keys=True)
        if value is None or key in values:
            continue
        chosen.append(i)
        values.add(key)
        count += 1
        if count == ADDITIONAL_PUBLIC_CANDIDATES:
            break
    return chosen + [sentinel]


class RelationTable:
    """Vectorized equivalent of v18 public relations, checked on old states."""

    def __init__(self, rows, box, graph):
        fields = rows["field"] + [None]
        amounts = rows["unit_quantity"] + [None]
        dims = rows["dimensions"] + [None]
        periods = rows["period"] + [None]
        kinds = {"instant": 1, "duration": 2}
        self.field_period = np.array([kinds.get(f["metadata"].get("period_type"), 0) if f else 0 for f in fields])
        self.period = np.array([kinds.get(p["value"]["kind"], 0) if p and p["value"] else 0 for p in periods])
        self.unit = np.array([[unit_compatibility(f, q) for q in amounts] for f in fields], dtype="float32")
        self.axis = np.zeros((len(fields), len(dims)), dtype="float32")
        for i, field in enumerate(fields):
            hints = graph["field_axis_hints"].get(field["value"], []) if field else []
            for j, dimension in enumerate(dims):
                values = dimension["value"] if dimension else None
                if values:
                    self.axis[i, j] = sum(v["axis"] in hints for v in values) / len(values)
        members = {(p["axis"], p["member"]) for p in graph["axis_member_hints"]}
        self.member = np.array([
            sum((v["axis"], v["member"]) in members for v in d["value"]) / len(d["value"]) if d and d["value"] else 0.0
            for d in dims
        ], dtype="float32")
        self.period_alignment = np.array([header_alignment(p, box) for p in periods], dtype="float32")
        self.unit_alignment = np.array([header_alignment(q, box) for q in amounts], dtype="float32")

    def get(self, states):
        f, p, d, q = states.T
        known = (self.field_period[f] > 0) & (self.period[p] > 0)
        match = known & (self.field_period[f] == self.period[p])
        return np.column_stack((known, match, self.unit[f, q], self.axis[f, d], self.member[d],
                                self.period_alignment[p], self.unit_alignment[q])).astype("float32")


def prepare():
    torch.set_num_threads(2)
    folder = DEST / "features"
    if (folder / "manifest.json").exists():
        raise RuntimeError("Expanded v19 feature inventory already frozen")
    v18_file = PREVIOUS / "features/manifest.json"
    v17_file = UNARY / "features/manifest.json"
    old, unary_meta = [json.loads(p.read_text()) for p in (v18_file, v17_file)]
    if not old["complete"] or not unary_meta["complete"]:
        raise ValueError("Both frozen feature inventories are required")
    source = PREVIOUS / "candidates/tasks.json"
    if sha(source) != old["public_candidate_sha256"]:
        raise ValueError("Common v18 candidates changed")
    graph_file = NETWORK / "public/report_graphs.json"
    if sha(graph_file) != old["public_graph_sha256"]:
        raise ValueError("Common public graph changed")
    records_file = PREVIOUS / "features/records.json"
    unary_records_file = UNARY / "features/records.json"
    if sha(records_file) != old["files"]["records.json"] or sha(unary_records_file) != unary_meta["files"]["records.json"]:
        raise ValueError("Frozen candidate records changed")
    tasks, records, old_records = [json.loads(p.read_text()) for p in (source, records_file, unary_records_file)]
    if len(tasks) != 840 or not all(a[c] == b[c] for a, b in zip(records, old_records, strict=True) for c in ("field", "dimensions")):
        raise ValueError("All field/dimension records must equal the original unary cache")
    design = {
        "additional_distinct_public_prior_candidates_per_field_or_dimension": ADDITIONAL_PUBLIC_CANDIDATES,
        "old_v18_nodes_states_features_and_scores_preserved": True,
        "period_and_quantity_beams_unchanged": True,
        "maximum_joint_states": 9 * 5 * 9 * 5,
        "old_v18_feature_manifest_sha256": sha(v18_file),
        "unary_v17_feature_manifest_sha256": sha(v17_file),
        "candidate_inventory_sha256": sha(source),
        "builder_sha256": sha(__file__),
        "shared_training_and_scoring_adapter_sha256": sha(Path(__file__).with_name("continue_native_joint_v19.py")),
        "new_teacher_or_future_reference_used_to_choose_beams": False,
        "all_controls_receive_the_same_beams": True,
        "new_generative_visual_calls": 0,
        "human_runtime_confirmations": 0,
        "post_v18_development_not_independent_test": True,
    }
    design_file = DEST / "preparation_design.json"
    if design_file.exists() and json.loads(design_file.read_text()) != design:
        raise ValueError("Frozen v19 preparation design changed")
    save(design_file, design)
    folder.mkdir(parents=True, exist_ok=True)
    (DEST / "candidates").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, DEST / "candidates/tasks.json")
    for name in ("context.npy", "ids.json", "records.json", "projection.npy"):
        if sha(PREVIOUS / "features" / name) != old["files"][name]:
            raise ValueError("Common feature artifact changed")
        shutil.copyfile(PREVIOUS / "features" / name, folder / name)
    contexts = np.load(folder / "context.npy")
    projection = np.load(folder / "projection.npy")
    graphs = json.loads(graph_file.read_text())
    companies = sorted({t["company"] for t in tasks})
    models = {}
    for company in companies:
        for seed in SEEDS:
            path = UNARY / "learning_v1/parents" / company / str(seed) / "generic_joint/weights.pt"
            meta = json.loads(path.with_name("metadata.json").read_text())
            if meta["excluded_company"] != company or sha(path) != old["common_unary_parent_sha256"][str(path)]:
                raise ValueError("Excluded-company common unary scorer changed")
            model = BindingMatcher("generic_joint", 448, old["context_dimension"])
            model.load_state_dict(torch.load(path, weights_only=True))
            models[(company, seed)] = model.eval()
    maximum_error = 0.0
    for i, (task, row) in enumerate(zip(tasks, records, strict=True)):
        ident = task["task_id"]
        old_path = PREVIOUS / "features" / (ident + ".npz")
        unary_path = UNARY / "features" / (ident + ".npz")
        if sha(old_path) != old["files"][old_path.name] or sha(unary_path) != unary_meta["files"][unary_path.name]:
            raise ValueError("Frozen per-target feature cache changed")
        with np.load(old_path) as archive, np.load(unary_path) as full:
            arrays = {"node_" + c: archive["node_" + c] for c in CHANNELS}
            vectors = {c: torch.from_numpy(full[c]) for c in ("field", "dimensions")}
            priors = {c: prior(task, c, vectors[c], row[c]) for c in vectors}
            for c, vector in vectors.items():
                equivalent = np.concatenate((vector[:, :384].numpy() @ projection, vector[:, 384:].numpy()), 1)
                error = float(np.max(np.abs(equivalent - arrays["node_" + c])))
                maximum_error = max(maximum_error, error)
                if error > 1e-5:
                    raise ValueError("Old unary cache differs from common v18 node features")
            table = RelationTable(row, task["target_box"], graphs[task["report"]])
            for fold in companies if task["source_year"] == 2023 else [task["company"]]:
                for seed in SEEDS:
                    suffix = fold + "_" + str(seed)
                    old_states = archive["states_" + suffix]
                    old_unary = archive["unary_" + suffix]
                    old_relations = archive["relations_" + suffix]
                    if not np.allclose(table.get(old_states), old_relations, rtol=0, atol=1e-7):
                        raise ValueError("Vectorized public relations differ from frozen v18")
                    beams, scores = [], {}
                    for j, c in enumerate(CHANNELS):
                        beam = ordered_column(old_states, j)
                        if c in vectors:
                            with torch.no_grad():
                                scores[c] = (priors[c] + models[(fold, seed)](vectors[c], torch.from_numpy(contexts[i]))).numpy()
                            expanded = expanded_beam(row[c], beam, priors[c].numpy(), c)
                        else:
                            scores[c] = np.zeros(len(row[c]) + 1, dtype="float32")
                            expanded = beam
                        # Preserve the exact original unary score for old nodes.
                        for index in beam:
                            positions = np.flatnonzero(old_states[:, j] == index)
                            values = old_unary[positions, j]
                            if not np.all(values == values[0]):
                                raise ValueError("Old unary score depends on a joint-state partner")
                            scores[c][index] = values[0]
                        beams.append(expanded)
                    states = np.array(list(itertools.product(*beams)), dtype="int32")
                    arrays["states_" + suffix] = states
                    arrays["unary_" + suffix] = np.stack([scores[c][states[:, j]] for j, c in enumerate(CHANNELS)], 1)
                    arrays["relations_" + suffix] = table.get(states)
        np.savez_compressed(folder / (ident + ".npz"), **arrays)
        if (i + 1) % 28 == 0:
            save(DEST / "preparation_progress.json", {"targets_prepared": i + 1, "total": 840,
                 "updated_utc": datetime.now(timezone.utc).isoformat(), "future_reference_files_read": False})
            print("Expanded common candidate beam", i + 1, "/ 840", flush=True)
    snapshot = folder / "feature_builder_snapshot.py"
    snapshot.write_bytes(Path(__file__).read_bytes())
    save(folder / "manifest.json", {
        "complete": True, "prepared_utc": datetime.now(timezone.utc).isoformat(), "targets": 840,
        "node_dimension": NODE_DIM, "state_dimension": STATE_DIM, "relation_dimension": REL_DIM,
        "context_dimension": old["context_dimension"], "seeds": list(SEEDS),
        "public_candidate_sha256": sha(DEST / "candidates/tasks.json"),
        "preparation_design_sha256": sha(design_file),
        "all_cases_within_a_fold_use_its_same_excluded_company_proposal_parent": True,
        "all_controls_receive_same_joint_states_unary_scores_and_public_compatibility_features": True,
        "all_original_v18_joint_states_and_unary_values_preserved": True,
        "maximum_cached_encoder_feature_difference": maximum_error,
        "public_graph_sha256": sha(graph_file),
        "common_unary_parent_sha256": old["common_unary_parent_sha256"],
        "files": {p.name: sha(p) for p in folder.iterdir() if p.is_file() and p.name != "manifest.json"},
        "teacher_or_future_reference_files_read": False,
        "teacher_states_inserted_into_joint_beam": False,
        "visual_header_ownership_or_full_dts_certified": False,
    })
    print("Expanded v19 feature inventory frozen", flush=True)


if __name__ == "__main__":
    prepare()

"""Equal-input past-feedback controls for assisted display-direction diagnosis.

Scope-conditioned pooling is compared to structured semantic memory and a
generic joint head. An identical typed-memory implementation is included to
detect a storage-only renaming claim. Neither reference years nor future answers
are read. Probabilities are uncalibrated; this is not a same-risk main result.
"""

from decimal import Decimal
import json
import math

import numpy as np
import torch

from canonicalization_prepare import DEST, ROOT
from canonicalization_observe import PRESENTATIONS
from pilot_features import encode_texts, pool, TEXT_MODEL
from prepare import save, sha

METHODS = (
    "common_visual",
    "semantic_memory",
    "scoped_pooling",
    "typed_memory_equivalent",
    "generic_joint",
)
SEEDS = (19, 41, 73)
STEPS = 256
PRESENTATION_NAMES = sorted(PRESENTATIONS)


def allowed_feedback(tasks, feedback, ids):
    by_id = {t["task_id"]: t for t in tasks}
    for ident in ids:
        if (
            by_id[ident]["source_year"] != 2023
            or feedback[ident]["source_year"] != 2023
        ):
            raise ValueError("Future answers cannot enter diagnostic training")
    return [feedback[i] for i in ids]


def sign_label(task, feedback):
    matching = {
        c["polarity"]
        for c in task["candidates"]
        if Decimal(c["computed_answer"]) == Decimal(feedback["answer"])
    }
    # Zero or mixed OCR parses cannot identify polarity. Missing candidate bags
    # consume the same feedback slot but must not create a false negative label.
    return next(iter(matching)) if len(matching) == 1 else None


def parse_anchor(task, observation):
    index = observation.get("candidate_index")
    if index is not None:
        return task["candidates"][index]["literal"]
    literals = {c["literal"] for c in task["candidates"]}
    return next(iter(literals)) if len(literals) == 1 else None


def base_probability(task, observation):
    index = observation.get("candidate_index")
    if index is None:
        return 0.5
    confidence = 0.6 if observation["uncertain"] else 0.8
    return confidence if task["candidates"][index]["polarity"] == 1 else 1 - confidence


def semantic_texts(task, observation):
    props = task["field_properties"]
    field = " ".join(
        str(props.get(k) or "(unknown)")
        for k in (
            "namespace",
            "name",
            "type",
            "balance",
            "period_type",
            "declaration_status",
        )
    )
    context = " ".join(
        [
            observation.get("row_text", ""),
            observation.get("section_text", ""),
            observation.get("presentation", "unknown"),
            task["public_row_text"],
            task["public_top_text"],
        ]
    )
    return field, context


def structured_key(task, observation):
    props = task["field_properties"]
    role = observation.get("presentation", "unknown")
    if role == "unknown":
        return None
    if props["declaration_status"] != "exact_public_schema":
        return None
    return (
        props["namespace"],
        props["type"],
        props["balance"],
        props["period_type"],
        role,
    )


def exact_context_key(task, observation):
    props = task["field_properties"]
    role = observation.get("presentation", "unknown")
    if role == "unknown" or props.get("namespace") is None:
        return None
    return (props["namespace"], props["name"], role)


def memory_probability(index, active, features, tasks, observations, scoped=False):
    task = tasks[index]
    obs = observations[task["task_id"]]
    prior = base_probability(task, obs)
    if not active:
        return prior, []
    candidates = list(active)
    if scoped:
        exact = exact_context_key(task, obs)
        selected = [
            (j, y)
            for j, y in active
            if exact is not None
            and exact_context_key(tasks[j], observations[tasks[j]["task_id"]]) == exact
        ]
        if not selected:
            key = structured_key(task, obs)
            selected = [
                (j, y)
                for j, y in active
                if key is not None
                and structured_key(tasks[j], observations[tasks[j]["task_id"]]) == key
            ]
        if selected:
            candidates = selected
        # Unknown scopes fall back to the same strong semantic memory. They do
        # not fabricate an applicability certificate or label all unknowns equal.
    scored = []
    for j, label in candidates:
        similarity = float(np.dot(features[index], features[j]))
        exact = exact_context_key(task, obs)
        if (
            exact is not None
            and exact_context_key(tasks[j], observations[tasks[j]["task_id"]]) == exact
        ):
            similarity += 0.25
        scored.append((similarity, j, label))
    scored.sort(key=lambda item: (-item[0], tasks[item[1]]["task_id"]))
    selected = scored[:3]
    weights = np.array([math.exp(6 * (v[0] - selected[0][0])) for v in selected])
    weights *= 6 / weights.sum()
    positive = sum(w for w, (_, _, label) in zip(weights, selected) if label == 1)
    posterior = float((2 * prior + positive) / (2 + weights.sum()))
    return posterior, [tasks[j]["task_id"] for _, j, _ in selected]


def prediction(task, observation, probability, used_feedback):
    literal = parse_anchor(task, observation)
    polarity = 1 if probability >= 0.5 else -1
    available = [
        i
        for i, c in enumerate(task["candidates"])
        if c["literal"] == literal and c["polarity"] == polarity
    ]
    index = available[0] if available else None
    return {
        "candidate_index": index,
        "identity_probability": probability,
        "confidence_not_calibrated": True,
        "feedback_dependencies": used_feedback,
        "forced_answer": task["candidates"][index]["computed_answer"]
        if index is not None
        else None,
        "parse_preserved_from_same_shared_observation": True,
        "full_binding_or_scope_ownership_certified": False,
    }


def run():
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    task_file = DEST / "public/tasks.json"
    tasks = json.loads(task_file.read_text())
    protocol_file = DEST / "protocol.json"
    protocol = json.loads(protocol_file.read_text())
    if sha(task_file) != protocol["public_tasks_sha256"]:
        raise RuntimeError("Frozen diagnostic tasks changed")
    prefix_file = DEST / "feedback_prefixes.json"
    if sha(prefix_file) != protocol["feedback_prefixes_sha256"]:
        raise RuntimeError("Frozen feedback prefixes changed")
    prefixes = json.loads(prefix_file.read_text())
    common_file = DEST / "common_visual/manifest.json"
    common = json.loads(common_file.read_text())
    if not common["complete"] or common["public_tasks_sha256"] != sha(task_file):
        raise RuntimeError("Complete same-input common observations required")
    observations = {}
    for record in common["records"]:
        if record["status"] == "completed":
            for row in record["targets"]:
                if row["task_id"] in observations:
                    raise RuntimeError("Duplicate common observation")
                observations[row["task_id"]] = row
    for task in tasks:
        observations.setdefault(
            task["task_id"],
            {
                "candidate_index": None,
                "row_text": "",
                "section_text": "",
                "presentation": "unknown",
                "uncertain": True,
                "common_observation_failed": True,
            },
        )
    feedback_file = DEST / "training_only/past_feedback.json"
    feedback = json.loads(feedback_file.read_text())
    if any(v["source_year"] != 2023 for v in feedback.values()):
        raise RuntimeError("Past-only feedback file contains future labels")
    dest = DEST / "training"
    design = {
        "methods": METHODS,
        "seeds_for_generic_joint": SEEDS,
        "optimizer_steps": STEPS,
        "learning_rate": 0.003,
        "hidden": 64,
        "optimizer": "AdamW",
        "weight_decay": 0.03,
        "loss": "past identified polarity cross entropy",
        "feedback_budgets": [0, 5, 10],
        "feedback_year_only": 2023,
        "evaluation_years": [2024, 2025],
        "memory_neighbors": 3,
        "memory_similarity_temperature": 6,
        "total_memory_pseudocount": 6,
        "common_visual_prior_pseudocount": 2,
        "exact_known_field_context_bonus": 0.25,
        "scope_pooling_hierarchy": [
            "exact public namespace/field/presentation",
            "exact public namespace/type/balance/period/presentation",
            "same strong semantic memory fallback",
        ],
        "all_controls_same_native_images_OCR_field_declarations_scope_observations": True,
        "all_controls_same_FEEDBACK_PREFIX_AND_NUMERIC_CANDIDATES": True,
        "typed_memory_identical_algorithm_is_storage_equivalence_control": True,
        "unknown_scope_not_an_applicability_certificate": True,
        "no_reference_sign_mapping_hardcoded": True,
        "no_calibrated_same_risk_claim": True,
        "null_visual_choice_backoff": "fixed identity prior and unique OCR literal only; ambiguity retained",
        "forced_answer_metric_not_a_certified_or_calibrated_coverage_metric": True,
        "oracle_region_field_scale_track": True,
        "human_confirmations": 0,
        "public_tasks_sha256": sha(task_file),
        "common_manifest_sha256": sha(common_file),
        "past_feedback_sha256": sha(feedback_file),
        "feedback_prefix_sha256": sha(prefix_file),
        "runner_sha256": sha(__file__),
    }
    design_path = dest / "design.json"
    if design_path.exists() and json.loads(design_path.read_text()) != json.loads(
        json.dumps(design)
    ):
        raise RuntimeError("Frozen diagnostic comparison design changed")
    save(design_path, design)
    texts = {t["task_id"]: semantic_texts(t, observations[t["task_id"]]) for t in tasks}
    embeddings = encode_texts(sorted({s for pair in texts.values() for s in pair}))
    visual_manifest_file = ROOT / "visual_features/manifest.json"
    visual_manifest = json.loads(visual_manifest_file.read_text())
    visual_meta = {p["page_id"]: p for p in visual_manifest["pages"]}
    patch_cache = {}
    features = []
    joint_features = []
    feature_inputs = {}
    for task in tasks:
        page_id = task["page_id"]
        file = ROOT / "visual_features" / (page_id + ".npy")
        if page_id not in patch_cache:
            if sha(file) != visual_meta[page_id]["features_sha256"]:
                raise RuntimeError("Frozen shared visual features changed")
            patch_cache[page_id] = np.load(file)
            feature_inputs[str(file)] = sha(file)
        field, context = texts[task["task_id"]]
        visual = pool(patch_cache[page_id], task["target_box"])
        visual = visual / max(float(np.linalg.norm(visual)), 1e-8)
        obs = observations[task["task_id"]]
        props = task["field_properties"]
        bal = [
            int(
                props["declaration_status"] == "exact_public_schema"
                and props["balance"] == v
            )
            for v in ("debit", "credit", None)
        ]
        bal.append(int(props["declaration_status"] != "exact_public_schema"))
        period = [int(props["period_type"] == v) for v in ("instant", "duration", None)]
        role = [int(obs["presentation"] == v) for v in PRESENTATION_NAMES]
        # These exact structured features, including interactions, are also
        # available to the generic joint head and semantic memory.
        interactions = [a * b for a in bal for b in role]
        structure = np.array(bal + period + role + interactions, dtype="float32")
        structure /= max(float(np.linalg.norm(structure)), 1e-8)
        full = np.concatenate(
            [embeddings[field], embeddings[context], visual, structure]
        ).astype("float32")
        features.append(full / max(float(np.linalg.norm(full)), 1e-8))
        joint_features.append(full)
    features = np.stack(features)
    joint = torch.tensor(np.stack(joint_features))
    np.save(dest / "shared_features.npy", np.stack(joint_features))
    save(
        dest / "feature_manifest.json",
        {
            "features_sha256": sha(dest / "shared_features.npy"),
            "dimension": joint.shape[1],
            "public_image_features": feature_inputs,
            "text_encoder_files": {
                f.name: sha(f) for f in TEXT_MODEL.iterdir() if f.is_file()
            },
            "field_type_presentation_interactions_available_to_all_controls": True,
            "future_reference_or_scoring_files_read": False,
        },
    )
    by_id = {t["task_id"]: j for j, t in enumerate(tasks)}
    rows = []
    ledger = []
    for company in sorted(prefixes):
        evaluation = [
            j
            for j, t in enumerate(tasks)
            if t["company"] == company and t["source_year"] in (2024, 2025)
        ]
        for budget in (0, 5, 10):
            ids = prefixes[company][str(budget)]
            events = allowed_feedback(tasks, feedback, ids)
            active = [
                (by_id[e["task_id"]], sign_label(tasks[by_id[e["task_id"]]], e))
                for e in events
            ]
            active = [(j, label) for j, label in active if label is not None]
            for method in METHODS[:-1]:
                for j in evaluation:
                    task = tasks[j]
                    obs = observations[task["task_id"]]
                    if method == "common_visual":
                        prob, used = base_probability(task, obs), []
                    else:
                        prob, used = memory_probability(
                            j,
                            active,
                            features,
                            tasks,
                            observations,
                            scoped=method != "semantic_memory",
                        )
                    rows.append(
                        {
                            "task_id": task["task_id"],
                            "company": company,
                            "method": method,
                            "budget": budget,
                            "seed": None,
                            "prediction": prediction(task, obs, prob, used),
                        }
                    )
            for seed in SEEDS:
                torch.manual_seed(seed)
                model = torch.nn.Sequential(
                    torch.nn.Linear(joint.shape[1], 64),
                    torch.nn.Tanh(),
                    torch.nn.Linear(64, 2),
                )
                torch.nn.init.zeros_(model[-1].weight)
                torch.nn.init.zeros_(model[-1].bias)
                if active:
                    optimizer = torch.optim.AdamW(
                        model.parameters(), lr=0.003, weight_decay=0.03
                    )
                    ix = torch.tensor([j for j, _ in active])
                    labels = torch.tensor(
                        [0 if label == 1 else 1 for _, label in active]
                    )
                    prior = torch.tensor(
                        [
                            [
                                math.log(
                                    base_probability(
                                        tasks[j], observations[tasks[j]["task_id"]]
                                    )
                                ),
                                math.log(
                                    1
                                    - base_probability(
                                        tasks[j], observations[tasks[j]["task_id"]]
                                    )
                                ),
                            ]
                            for j, _ in active
                        ]
                    )
                    for _ in range(STEPS):
                        optimizer.zero_grad()
                        logits = model(joint[ix]) + prior
                        loss = torch.nn.functional.cross_entropy(logits, labels)
                        loss.backward()
                        optimizer.step()
                with torch.no_grad():
                    residuals = model(joint[evaluation])
                for n, j in enumerate(evaluation):
                    task = tasks[j]
                    obs = observations[task["task_id"]]
                    prob = base_probability(task, obs)
                    prior = torch.tensor([math.log(prob), math.log(1 - prob)])
                    prob = float((residuals[n] + prior).softmax(0)[0])
                    rows.append(
                        {
                            "task_id": task["task_id"],
                            "company": company,
                            "method": "generic_joint",
                            "budget": budget,
                            "seed": seed,
                            "prediction": prediction(
                                task, obs, prob, ids if active else []
                            ),
                        }
                    )
                file = dest / "checkpoints" / f"{company}_{budget}_{seed}.pt"
                file.parent.mkdir(parents=True, exist_ok=True)
                torch.save(model.state_dict(), file)
                ledger.append(
                    {
                        "company": company,
                        "budget": budget,
                        "seed": seed,
                        "actual_feedback_slots": len(ids),
                        "identifiable_polarity_feedback": len(active),
                        "optimizer_steps": STEPS if active else 0,
                        "checkpoint_sha256": sha(file),
                    }
                )
            print(
                "Past-feedback polarity controls",
                company,
                budget,
                "usable",
                len(active),
                "/",
                len(ids),
                flush=True,
            )
    expected = (
        sum(t["source_year"] in (2024, 2025) for t in tasks) * 3 * (4 + len(SEEDS))
    )
    if len(rows) != expected:
        raise RuntimeError("Incomplete equal-input diagnostic prediction matrix")
    save(dest / "predictions.json", rows)
    save(dest / "training_ledger.json", ledger)
    save(
        dest / "inference_manifest.json",
        {
            "complete": True,
            "expected_predictions": expected,
            "predictions_sha256": sha(dest / "predictions.json"),
            "design_sha256": sha(design_path),
            "common_manifest_sha256": sha(common_file),
            "public_tasks_sha256": sha(task_file),
            "future_reference_or_scoring_files_read": False,
            "scoring_completed": False,
            "human_confirmations": 0,
        },
    )
    print("Frozen complete diagnostic predictions:", len(rows), flush=True)


if __name__ == "__main__":
    run()

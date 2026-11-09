"""Shared frozen public inputs and strictly historical program-supervision bags."""

import json

import numpy as np
import torch
from crop_candidates_v3 import DEST
from prepare import sha
from program_scope_learning_v3 import (
    positive_programs,
    prior_and_candidates,
    validate_feedback_protocol,
)


def load():
    task_file = DEST / "public/tasks.json"
    manifest_file = DEST / "features/manifest.json"
    tasks = json.loads(task_file.read_text())
    meta = json.loads(manifest_file.read_text())
    if meta["public_task_sha256"] != sha(task_file):
        raise RuntimeError("Frozen public program target inventory differs")
    for name, digest in meta["files"].items():
        if sha(DEST / "features" / name) != digest:
            raise RuntimeError("Frozen common program feature block differs")
    if json.loads((DEST / "features/ids.json").read_text()) != [
        t["task_id"] for t in tasks
    ]:
        raise RuntimeError("Common feature identity/order differs")
    fields = torch.tensor(np.load(DEST / "features/field.npy"))
    contexts = torch.tensor(np.load(DEST / "features/context.npy"))
    if (
        fields.shape != (len(tasks), meta["field_dimension"])
        or contexts.shape != (len(tasks), meta["context_dimension"])
        or not torch.isfinite(fields).all()
        or not torch.isfinite(contexts).all()
    ):
        raise RuntimeError("Invalid common program feature matrices")
    feedback_file = DEST / "training_only/past_feedback.json"
    prefix_file = DEST / "feedback_prefixes.json"
    feedback = json.loads(feedback_file.read_text())
    prefixes = json.loads(prefix_file.read_text())
    validate_feedback_protocol(tasks, feedback, prefixes)
    candidates = json.loads((DEST / "features/candidates.json").read_text())
    observations = json.loads((DEST / "features/observations.json").read_text())
    packs = []
    for j, task in enumerate(tasks):
        prior, candidate = prior_and_candidates(
            task, observations[task["task_id"]], candidates[task["task_id"]]
        )
        positive = (
            positive_programs(task, feedback[task["task_id"]])
            if task["source_year"] == 2023
            else torch.zeros_like(prior, dtype=torch.bool)
        )
        packs.append(
            {
                "field": fields[j],
                "context": contexts[j],
                "prior": prior,
                "candidate": candidate,
                "positive": positive,
            }
        )
    return {
        "tasks": tasks,
        "prefixes": prefixes,
        "packs": packs,
        "field_dimension": fields.shape[1],
        "context_dimension": contexts.shape[1],
        "public_tasks_sha256": sha(task_file),
        "feature_manifest_sha256": sha(manifest_file),
        "past_feedback_sha256": sha(feedback_file),
        "feedback_prefix_sha256": sha(prefix_file),
    }

"""Fail-closed historical auxiliary data for a future full-binding experiment.

An excluded company's source-grid labels are never extra parent or local
supervision. Local 0/5/10 feedback remains the original semantic tuples;
auxiliary replay may use other companies' historical links only.
"""

import json
from collections import Counter

import numpy as np
import torch
from native_year_scope_features_v24 import DEST
from prepare import sha


def parent_indices(index, excluded_company):
    if not excluded_company:
        raise ValueError("An excluded company identifier is required")
    if any(r["source_year"] != 2023 for r in index):
        raise ValueError("Auxiliary supervision must be historical only")
    if len({r["pair_id"] for r in index}) != len(index):
        raise ValueError("Duplicate auxiliary pair identifiers")
    return [i for i, r in enumerate(index) if r["company"] != excluded_company]


def load_auxiliary(excluded_company):
    manifest_file = DEST / "manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if not manifest["complete"] or manifest["observed_binary_year_links"] != 1308:
        raise ValueError("Complete historical weak year-link features required")
    for filename, digest in manifest["files_sha256"].items():
        if sha(DEST / filename) != digest:
            raise ValueError("Frozen historical auxiliary feature/teacher data changed")
    inputs = np.load(DEST / "anchors.npy")
    labels = np.load(DEST / "teacher_labels.npy")
    index = json.loads((DEST / "administrative_index.json").read_text())
    if inputs.shape != (len(index), 112) or inputs.dtype != np.float32 or labels.shape != (len(index),):
        raise ValueError("Historical auxiliary feature/label dimensions differ")
    if not np.isfinite(inputs).all() or not np.isin(labels, (0, 1)).all() or not (inputs[:, 100] == 1).all():
        raise ValueError("Only observed finite labelled links may supervise")
    active = parent_indices(index, excluded_company)
    if not active:
        raise ValueError("No other-company historical auxiliary links available")
    record = {"excluded_company": excluded_company, "extra_own_company_supervision_cases": 0,
              "source_feature_manifest_sha256": sha(manifest_file),
              "eligible_cases": len(active), "cases_by_company": dict(Counter(index[i]["company"] for i in active)),
              "own_company_source_links_must_not_be_used_during_local_feedback_updates": True,
              "local_feedback_semantic_tuple_budget_unchanged": True,
              "weak_year_links_are_not_certified_periods_or_ownership": True,
              "company_identifier_exclusion_does_not_certify_legal_group_or_template_independence": True}
    return torch.from_numpy(inputs), torch.from_numpy(labels).float(), active, index, record

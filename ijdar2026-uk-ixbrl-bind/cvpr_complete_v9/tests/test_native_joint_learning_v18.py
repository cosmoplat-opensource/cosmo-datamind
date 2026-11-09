from pathlib import Path
import sys

import numpy as np
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from native_joint_learning_v18 import JointMatcher, prototype_mask, state_feedback
from native_joint_features_v18 import NODE_DIM, STATE_DIM


def test_joint_model_parameter_counts_match_within_one_percent():
    counts = [sum(p.numel() for p in JointMatcher(m,1156).parameters()) for m in ("dense_joint","coupled_joint","joint_memory")]
    assert max(counts)/min(counts) < 1.01


def test_partial_feedback_does_not_label_missing_component_as_null():
    task = {"task_id":"t","source_year":2023}
    teacher = {"task_id":"t","source_year":2023,"teacher_binding":{"field":"OUTSIDE_BEAM","period":{"kind":"instant","instant":"2022-12-31"},"dimensions":[],"unit":{},"quantity":"5"}}
    records = {"field":[{"value":"A"}],"period":[{"value":{"kind":"instant","instant":"2022-12-31"}}],"dimensions":[{"value":[]}],"unit_quantity":[]}
    states = np.array([[0,0,0,0],[1,0,0,0],[0,1,0,0]])
    positive,known = state_feedback(task,records,teacher,states)
    assert known == [False,True,True,False]
    assert positive.tolist() == [True,True,False]


def test_partial_prototype_masks_hide_unknown_components_and_cross_relations():
    mask = prototype_mask([True,False,False,False])
    assert mask[:NODE_DIM].all() and not mask[NODE_DIM:4*NODE_DIM].any()
    assert not mask[-8:].any()


def test_coupling_changes_relative_joint_state_scores_and_ablation_removes_it():
    torch.manual_seed(3)
    model = JointMatcher("coupled_joint",5)
    torch.nn.init.normal_(model.pairs[0].weight)
    x,context = torch.randn(2,STATE_DIM),torch.randn(5)
    coupled,uncoupled = model(x,context),model(x,context,no_pair=True)
    assert torch.allclose(uncoupled,torch.zeros_like(uncoupled))
    assert abs(float((coupled[0]-coupled[1]).detach())) > 1e-7

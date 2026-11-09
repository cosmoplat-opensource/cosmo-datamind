from pathlib import Path
import sys

import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_binding_features_v17 import quantity_value
from native_binding_learning_v17 import BindingMatcher, historical_positive, prediction
from score_native_binding_v17 import component_correct


def test_generic_factor_and_memory_parameter_counts_are_matched():
    sizes = [sum(p.numel() for p in BindingMatcher(m,448,1156).parameters()) for m in ("generic_joint","factor_binding","learned_semantic_memory")]
    assert max(sizes)/min(sizes) < 1.001


def test_historical_positive_never_reads_future_labels_or_invents_null_positive():
    task, teacher = {"task_id":"t","source_year":2023}, {"task_id":"t","source_year":2023,"teacher_binding":{"field":"known"}}
    assert historical_positive(task,[{"value":"different"}],teacher,"field").tolist() == [False,False]
    task["source_year"] = 2024
    with pytest.raises(ValueError,match="historical"):
        historical_positive(task,[],teacher,"field")


def test_source_quantity_scaling_retains_more_than_28_decimal_digits():
    value = "123456789012345678901234567890123456789"
    assert quantity_value(value,"1000",-1) == "-123456789012345678901234567890123456789000"


def test_empty_dimensions_are_different_from_unknown_dimensions():
    choices = {"field":"F","period":{"kind":"instant","instant":"2023-12-31"},"dimensions":[],
               "unit_quantity":{"unit":{"numerator":["GBP"],"denominator":[]},"quantity":"10"}}
    task = {"task_id":"t","entity_candidates":[{"entity":{"identifier":"C","scheme":"LEI"}}]}
    result = prediction(task,choices)
    assert result["semantic_tuple_complete"]
    assert result["scope_assignment"] == "unknown" and not result["visual_header_ownership_certified"]
    assert component_correct(result["predicted_binding"],result["predicted_binding"])["semantic_tuple_exact"]
    choices["dimensions"] = None
    assert not prediction(task,choices)["semantic_tuple_complete"]


def test_memory_linear_decomposition_matches_concatenation_with_context():
    torch.manual_seed(5)
    model = BindingMatcher("learned_semantic_memory",4,3)
    torch.nn.init.normal_(model.out.weight)
    fields, context = torch.randn(2,4), torch.randn(3)
    supports = [(torch.randn(4),torch.randn(3)) for _ in range(3)]
    sf, sc = (torch.stack(v) for v in zip(*supports))
    nearest = torch.argsort((sc-context).square().mean(1),stable=True)
    pairs = torch.cat(((fields[:,None,:]-sf[nearest]).abs(),(context-sc[nearest]).abs()[None,:,:].expand(2,-1,-1)),2)
    expected = torch.logsumexp(model.out(torch.tanh(model.first(pairs))).squeeze(-1),1)-torch.log(torch.tensor(3.))
    assert torch.allclose(model(fields,context,supports),expected,atol=1e-6)


def test_generic_affine_decomposition_keeps_the_concatenated_baseline():
    torch.manual_seed(7)
    model = BindingMatcher("generic_joint",4,3)
    torch.nn.init.normal_(model.out.weight)
    fields, context = torch.randn(2,4), torch.randn(3)
    expected = model.out(torch.tanh(model.first(torch.cat((fields,context.expand(2,-1)),1)))).flatten()
    assert torch.allclose(model(fields,context),expected,atol=1e-6)

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_joint_features_v18 import public_relations
from native_joint_features_v19 import RelationTable, expanded_beam, ordered_column


def test_expansion_keeps_old_hypotheses_and_adds_distinct_public_candidates():
    rows = [{"value": x} for x in ("a", "b", "b", "c", "d", "e", "f", "g")]
    expanded = expanded_beam(rows, [0, 8], np.array([1, 4, 5, 3, 2, 0, -1, -2, -.75]), "field")
    assert expanded == [0, 2, 3, 4, 5, 8]
    assert len({rows[i]["value"] for i in expanded[:-1]}) == 5


def test_nonexpanded_channels_and_unknown_order_remain_unchanged():
    rows = [{"value": x} for x in (1, 2, 3)]
    assert expanded_beam(rows, [2, 0, 3], np.arange(4), "period") == [2, 0, 3]
    states = np.array([[2, 4], [2, 3], [0, 4], [0, 3], [3, 4], [3, 3]])
    assert ordered_column(states, 0) == [2, 0, 3]


def test_vectorized_relations_match_original_with_unknowns_and_incomplete_graph():
    monetary = "{http://www.xbrl.org/2003/instance}monetaryItemType"
    field = {"value": "F", "metadata": {"period_type": "instant", "type": monetary}}
    amount = {"value": {"unit": {"numerator": ["{http://www.xbrl.org/2003/iso4217}GBP"], "denominator": []}}, "metadata": {}}
    header = {"box": [0, 0, .5, .2]}
    rows = {
        "field": [field],
        "period": [{"value": {"kind": "instant"}, "metadata": {"header": header}},
                   {"value": {"kind": "duration"}, "metadata": {}}],
        "dimensions": [{"value": [], "metadata": {}},
                       {"value": [{"axis": "A", "member": "M"}], "metadata": {}}],
        "unit_quantity": [amount],
    }
    graph = {"field_axis_hints": {"F": ["A"]}, "axis_member_hints": [{"axis": "A", "member": "M"}], "pair_set": {("A", "M")}}
    states = np.array([[0, 0, 0, 0], [0, 1, 1, 0], [1, 2, 2, 1], [0, 0, 1, 1]])
    box = [.1, .4, .2, .5]
    actual = RelationTable(rows, box, graph).get(states)
    expected = np.stack([public_relations(rows, s, box, graph) for s in states])
    assert actual.shape == (4, 8)
    assert np.allclose(actual, expected, rtol=0, atol=1e-7)
    assert actual[1, 0] == 1 and actual[1, 1] == 0
    assert actual[1, 4] == 1 and actual[1, 5] == 1

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_binding_expand_v17 import dimension_query, graph_dimension_tuples, merge_hypotheses


def test_original_candidate_is_preserved_when_new_evidence_has_same_value():
    old = {"candidate_id": "old", "dimensions": [], "absence_not_certified": True}
    new = {"candidate_id": "new", "dimensions": []}
    assert merge_hypotheses([old], [new], "dimensions") == [old]


def test_network_pairs_are_uncertified_and_do_not_repeat_axes():
    hints = [{"axis": "A", "member": "M", "retrieval_score": .9, "source_path_arc_ids": ["x"]},
             {"axis": "A", "member": "N", "retrieval_score": .8, "source_path_arc_ids": ["y"]},
             {"axis": "B", "member": "P", "retrieval_score": .7, "source_path_arc_ids": ["z"]}]
    rows = graph_dimension_tuples(hints)
    assert any(len(r["dimensions"]) == 2 for r in rows)
    for row in rows:
        assert not row["axis_member_membership_certified"] and not row["visible_dimension_binding_certified"]
        assert len({d["axis"] for d in row["dimensions"]}) == len(row["dimensions"])


def test_dimension_query_cannot_read_target_reference_or_source_year():
    task = {"source_year": 2199, "public_top_text": "Group Company 2022", "public_row_text": "Cash",
            "teacher_binding": {"dimensions": ["PRIVATE"]},
            "common_visual_observation": {"dimension_text": "Company", "version_text": "restated", "headers": []}}
    query = dimension_query(task)
    assert "PRIVATE" not in str(query) and "2199" not in str(query)

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_binding_schema_v16 import declaration_roles, dimension_declaration_candidate, dimension_hypotheses, entity_candidates, type_role, valid_lei


def test_qname_prefix_shadowing_is_resolved_at_declaration():
    body = b'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:t="http://www.xbrl.org/2003/instance" targetNamespace="urn:issuer">
      <xs:element name="Cash" type="t:monetaryItemType"/>
      <xs:element xmlns:t="urn:custom" name="Other" type="t:monetaryItemType"/>
    </xs:schema>'''
    rows = declaration_roles(body)
    assert rows[0]["declaration_role"] == "numeric_item"
    assert rows[1]["type"] == "{urn:custom}monetaryItemType"
    assert rows[1]["declaration_role"] == "unknown"


def test_declared_axis_is_not_a_member_or_certified_membership_edge():
    body = b'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:d="http://xbrl.org/2005/xbrldt" xmlns:t="http://www.xbrl.org/2003/instance" targetNamespace="urn:issuer">
      <xs:element name="SegmentAxis" type="t:stringItemType" substitutionGroup="d:dimensionItem" d:typedDomainRef="#segment" abstract="true"/>
    </xs:schema>'''
    row = declaration_roles(body)[0]
    assert row["declaration_role"] == "dimension_axis"
    assert row["typed_domain_ref"] == "#segment"
    assert not row["axis_member_membership_certified"]
    assert type_role("{http://www.xbrl.org/dtr/type/2022-03-31}domainItemType", None) == "domain_or_member"


def test_abstract_domains_and_members_are_not_discarded_as_nonexistent():
    assert dimension_declaration_candidate({"declaration_role": "domain_or_member", "abstract": True})
    assert dimension_declaration_candidate({"declaration_role": "dimension_axis", "abstract": True})
    assert not dimension_declaration_candidate({"declaration_role": "nonnumeric_item", "abstract": True})


def test_entity_is_common_metadata_assistance_and_bad_checksums_stay_unresolved():
    lei = "2138001P49OLAEU33T68"
    assert valid_lei(lei) and not valid_lei(lei[:-1] + "9")
    record = entity_candidates({"company": lei, "source_year": 2025,
                                "common_visual_observation": {"entity_text": "Phoenix Group"},
                                "teacher_binding": {"entity": {"identifier": "PRIVATE"}}})[0]
    assert record["entity"]["identifier"] == lei
    assert record["common_metadata_assistance_not_visual_identity_learning"]
    assert not record["visible_text_to_entity_mapping_certified"]
    assert "PRIVATE" not in str(record)
    assert entity_candidates({"company": "invalid", "common_visual_observation": None}) == []


def test_explicit_tuples_keep_unknown_membership_and_no_duplicate_axes():
    axes = [{"field": "{urn:test}AxisA", "retrieval_score": .8, "typed_domain_ref": None},
            {"field": "{urn:test}AxisB", "retrieval_score": .7, "typed_domain_ref": None}]
    members = [{"field": "{urn:test}Member", "retrieval_score": .6}]
    tuples = dimension_hypotheses(axes, members)
    assert tuples[0]["dimensions"] == [] and tuples[0]["absence_not_certified"]
    assert any(len(t["dimensions"]) == 2 for t in tuples)
    assert len({t["candidate_id"] for t in tuples}) == len(tuples)
    for item in tuples:
        assert not item["axis_member_membership_certified"]
        assert len({d["axis"] for d in item["dimensions"]}) == len(item["dimensions"])


def test_typed_dimension_does_not_receive_a_fabricated_explicit_member():
    axes = [{"field": "{urn:test}Axis", "retrieval_score": .8, "typed_domain_ref": "#typed"}]
    members = [{"field": "{urn:test}Member", "retrieval_score": .6}]
    assert [t["dimensions"] for t in dimension_hypotheses(axes, members)] == [[]]


def test_dtd_and_unresolved_qnames_are_rejected():
    with pytest.raises(ValueError, match="safe"):
        declaration_roles(b'<!DOCTYPE schema><xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:x"/>')
    with pytest.raises(ValueError, match="Unresolved"):
        declaration_roles(b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:x"><xs:element name="A" type="bad:string"/></xs:schema>')

from pathlib import Path
import sys

from lxml import etree
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_binding_prepare_v14 import XI, audit_file_for, context, expanded, period, source_date, unique_ids, unit


def xml(body):
    return etree.fromstring((
        '<root xmlns:x="http://www.xbrl.org/2003/instance" '
        'xmlns:iso="http://www.xbrl.org/2003/iso4217" '
        'xmlns:d="http://xbrl.org/2006/xbrldi" xmlns:c="https://example.org/schema">'
        + body + '</root>').encode())[0]


def test_source_instant_is_not_guessed_from_serialized_period():
    assert period(xml('<x:period><x:instant>2022-12-31</x:instant></x:period>')) == {
        "kind": "instant", "instant": "2022-12-31"}


def test_source_date_timezone_is_not_reinterpreted_as_a_time():
    assert source_date("2022-12-31+09:00") == "2022-12-31+09:00"
    assert source_date("2022-12-31Z") == "2022-12-31Z"


def test_source_lookup_uses_the_same_frozen_reference_view_as_training():
    result = audit_file_for({"lei": "A", "period_end": "2023-12-31"})
    assert result.parts[-3:] == ("full_publisher_reference_view_v2", "scoring_only", "A_2023-12-31.json")


def test_later_year_source_annotation_is_rejected_before_opening():
    with pytest.raises(ValueError, match="Only historical"):
        audit_file_for({"lei": "A", "period_end": "2024-12-31"})


def test_duration_keeps_exact_source_boundaries():
    result = period(xml('<x:period><x:startDate>2023-01-01</x:startDate>'
                        '<x:endDate>2023-12-31</x:endDate></x:period>'))
    assert result == {"kind": "duration", "start_date": "2023-01-01", "end_date": "2023-12-31"}
    with pytest.raises(ValueError, match="Inverted"):
        period(xml('<x:period><x:startDate>2024-01-01</x:startDate>'
                   '<x:endDate>2023-12-31</x:endDate></x:period>'))


def test_unit_ratio_preserves_numerator_and_denominator():
    result = unit(xml('<x:unit><x:divide><x:unitNumerator><x:measure>iso:GBP</x:measure>'
                      '</x:unitNumerator><x:unitDenominator><x:measure>x:shares</x:measure>'
                      '</x:unitDenominator></x:divide></x:unit>'))
    assert result["numerator"] == ["{http://www.xbrl.org/2003/iso4217}GBP"]
    assert result["denominator"] == [XI + "shares"]


def test_unresolved_namespace_and_duplicate_ids_are_not_silently_merged():
    with pytest.raises(ValueError, match="Unresolved"):
        expanded("missing:GBP", {})
    with pytest.raises(ValueError, match="duplicate"):
        unique_ids([etree.fromstring(b'<unit id="u"/>'), etree.fromstring(b'<unit id="u"/>')])


def test_dimension_members_use_source_namespace_identity():
    node = xml('<x:context><x:entity><x:identifier scheme="https://example.org/id">A</x:identifier>'
               '<x:segment><d:explicitMember dimension="c:Axis">c:Member</d:explicitMember>'
               '</x:segment></x:entity><x:period><x:instant>2023-12-31</x:instant></x:period></x:context>')
    result = context(node)
    assert result["entity"] == {"scheme": "https://example.org/id", "identifier": "A"}
    assert result["dimensions"] == [{"axis": "{https://example.org/schema}Axis", "kind": "explicit",
                                      "member": "{https://example.org/schema}Member"}]

import sys
from collections import defaultdict
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_header_supervision_audit_v20 import declaration_for


def example(xml):
    root = etree.fromstring(xml)
    ids = defaultdict(list)
    for node in root.iter():
        if node.get("id"):
            ids[node.get("id")].append(node)
    return declaration_for(ids["fact"][0], ids)


def test_explicit_reference_is_a_publisher_witness_and_not_a_visual_certificate():
    result = example('<table><tr><th id="h" scope="col">2023</th></tr><tr><td headers="h"><span id="fact">5</span></td></tr></table>')
    assert result["qualified_publisher_header_association_available"]
    assert result["explicit_header_ids"][0]["literal_text"] == "2023"
    assert not result["visible_header_ownership_certified"]


def test_year_text_in_preceding_cell_does_not_create_a_header_label():
    result = example('<table><tr><td>2023</td></tr><tr><td><span id="fact">5</span></td></tr></table>')
    assert result["target_in_html_table"]
    assert not result["qualified_publisher_header_association_available"]
    assert not result["explicit_header_ids"]


def test_duplicate_or_foreign_table_header_ids_remain_unknown():
    duplicate = example('<table><tr><th id="h">A</th><th id="h">B</th></tr><tr><td headers="h"><span id="fact">5</span></td></tr></table>')
    foreign = example('<div><table><tr><th id="h">A</th></tr></table><table><tr><td headers="h"><span id="fact">5</span></td></tr></table></div>')
    assert duplicate["unresolved_or_invalid_header_ids"] == ["h"]
    assert foreign["unresolved_or_invalid_header_ids"] == ["h"]
    assert not duplicate["qualified_publisher_header_association_available"]
    assert not foreign["qualified_publisher_header_association_available"]


def test_div_layout_and_inline_hidden_targets_are_disclosed():
    div = example('<div><span id="fact">5</span></div>')
    hidden = example('<table style="display: none"><tr><td><span id="fact">5</span></td></tr></table>')
    assert not div["target_in_html_table"]
    assert hidden["fact_has_hidden_inline_ancestor"]

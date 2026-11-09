from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_dimension_network_v17 import ARC, definition_arcs, definition_member, relationship_hints, resolve_locator


def edge(source, target, kind, **extra):
    return {"source": source, "target": target, "arcrole": ARC+kind, "role": "R", "target_role": "R",
            "usable": True, "use": "optional", "priority": "0", "arc_id": source+target+kind, **extra}


def test_only_definition_members_are_opened_and_unsafe_paths_rejected():
    assert definition_member("issuer/report_def.xml")
    assert not definition_member("issuer/report.xhtml")
    assert not definition_member("issuer/report_lab-en.xml")
    with pytest.raises(ValueError, match="Unsafe"):
        definition_member("../report_def.xml")


def test_locator_resolution_keeps_collisions_unresolved():
    assert resolve_locator("https://public/schema.xsd#cash", {("schema.xsd", "cash"): {"{urn:x}Cash"}}) == "{urn:x}Cash"
    assert resolve_locator("schema.xsd#cash", {("schema.xsd", "cash"): {"A", "B"}}) is None


def test_target_role_and_nonusable_intermediate_members_are_followed():
    arcs = [edge("Axis", "Root", "dimension-domain", target_role="NEXT", usable=False),
            edge("Root", "Middle", "domain-member", role="NEXT", target_role="LAST", usable=False),
            edge("Middle", "Leaf", "domain-member", role="LAST", target_role="LAST")]
    assert [(p["axis"], p["member"]) for p in relationship_hints(arcs)["axis_member_hints"]] == [("Axis", "Leaf")]


def test_prohibited_and_default_members_never_become_explicit_candidates():
    arcs = [edge("Axis", "Root", "dimension-domain"), edge("Root", "Leaf", "domain-member"),
            edge("Axis", "Root", "dimension-default"), edge("Root", "Leaf", "domain-member", use="prohibited", priority="1")]
    assert relationship_hints(arcs)["axis_member_hints"] == []


def test_unresolved_locators_are_recorded_instead_of_invented():
    xml = b'''<l:linkbase xmlns:l="http://www.xbrl.org/2003/linkbase" xmlns:x="http://www.w3.org/1999/xlink">
    <l:definitionLink x:role="R"><l:loc x:label="a" x:href="s.xsd#a"/>
    <l:loc x:label="b" x:href="missing.xsd#b"/>
    <l:definitionArc x:from="a" x:to="b" x:arcrole="http://xbrl.org/int/dim/arcrole/domain-member"/>
    </l:definitionLink></l:linkbase>'''
    arcs, unresolved = definition_arcs(xml, {("s.xsd", "a"): {"A"}}, "hash")
    assert not arcs and len(unresolved) == 1
    assert unresolved[0]["target"] is None

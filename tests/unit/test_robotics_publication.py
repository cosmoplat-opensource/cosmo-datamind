"""Publish and consume the trusted OWL contract — DR-060 / DR-065 / ROB-04 / ROB-05.

T04 RED: the publisher and consumer do not exist yet; imports stay inside tests.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from rdflib import Graph, Literal, OWL, RDF, URIRef, XSD
from rdflib.compare import isomorphic

from tests.robotics_support import FIXTURES, S, api, build, source_root, trees

ONTOLOGY = URIRef("https://example.org/ur-skill")
CONTRACT = "ur-pick-place-owl-v2"
TRUSTED_SHAPES_SHA256 = "b57e61b049b0a521cf1812987a24522178412867e6d358571a8a0658c6021ae2"


def test_fresh_build_publishes_and_loads_in_consumer(tmp_path):
    publisher, consumer = api("publisher"), api("consumer")
    graph = build()
    before = deepcopy(graph)
    assert not list(graph.objects(ONTOLOGY, S.artifactContract))
    target = tmp_path / "published.owl"
    publisher.publish_ontology(graph, target, mode="real")
    assert isomorphic(graph, before), "Publishing mutated the authoring draft"
    published = Graph().parse(target, format="xml")
    assert list(published.objects(ONTOLOGY, S.artifactContract)) == [Literal(CONTRACT, datatype=XSD.string)]
    shapes = list(published.objects(ONTOLOGY, S.validationShapes))
    assert len(shapes) == 1 and isinstance(shapes[0], Literal)
    assert hashlib.sha256(str(shapes[0]).encode()).hexdigest() == TRUSTED_SHAPES_SHA256
    assert not list(published.triples((None, OWL.imports, None)))
    context = consumer.load_ontology(target, mode="real")
    assert isomorphic(context["graph"], published)
    assert context["validation"]["ontology_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert context["validation"]["shacl_conforms"] is True
    assert context["validation"]["source_xml_required"] is False
    assert context["validation"]["external_artifacts_required"] is False
    assert trees(api("semantics").xml_from_graph(context["graph"])) == trees(source_root())
    assert len(set(context["graph"].subjects(RDF.type, S.SkillInvocation))) == 25
    assert context["graph"].value(S.PartType_A, S.hasPerceptionProfile) is None
    assert context["graph"].value(S.Slot_B_01, S.hasPlacementProfile) is None


@pytest.mark.parametrize("name,mode", [("agent_real.owl", "real"), ("agent_demo.owl", "demo")])
def test_existing_agent_owl_loads_without_external_files(tmp_path, name, mode):
    consumer = api("consumer")
    target = tmp_path / "existing.owl"
    target.write_bytes((FIXTURES / "owl" / name).read_bytes())
    before = target.read_bytes()
    # Sidecars are deliberately wrong: the published OWL must be self-contained.
    (tmp_path / "ur_skill_shapes.ttl").write_text("invalid Turtle; must not be used")
    (tmp_path / "hermit_report.json").write_text('{"consistent": true}')
    context = consumer.load_ontology(target, mode=mode)
    assert context["validation"]["shacl_conforms"] is True
    assert context["validation"]["external_artifacts_required"] is False
    assert target.read_bytes() == before
    if mode == "demo":
        with pytest.raises(ValueError):
            consumer.load_ontology(target, mode="real")


@pytest.mark.parametrize("change", ["missing_contract", "unknown_contract", "duplicate_contract",
                                    "missing_shapes", "changed_shapes", "imports"])
def test_untrusted_contract_rules_and_external_dependencies_are_rejected(tmp_path, change):
    consumer = api("consumer")
    graph = Graph().parse(FIXTURES / "owl/agent_real.owl", format="xml")
    if change == "missing_contract":
        graph.remove((ONTOLOGY, S.artifactContract, None))
    elif change == "unknown_contract":
        graph.set((ONTOLOGY, S.artifactContract, Literal("unknown-v99", datatype=XSD.string)))
    elif change == "duplicate_contract":
        graph.add((ONTOLOGY, S.artifactContract, Literal("duplicate", datatype=XSD.string)))
    elif change == "missing_shapes":
        graph.remove((ONTOLOGY, S.validationShapes, None))
    elif change == "changed_shapes":
        graph.set((ONTOLOGY, S.validationShapes, Literal(
            str(graph.value(ONTOLOGY, S.validationShapes)) + "\n# changed", datatype=XSD.string)))
    else:
        graph.add((ONTOLOGY, OWL.imports, URIRef("https://invalid.example/external.owl")))
    target = tmp_path / "untrusted.owl"
    graph.serialize(target, format="xml")
    with pytest.raises(ValueError) as caught:
        consumer.load_ontology(target, mode="real")
    assert str(caught.value).strip()


@pytest.mark.parametrize("change", ["binding", "condition", "duplicate_step"])
def test_semantically_incompatible_publication_is_rejected(tmp_path, change):
    publisher, consumer = api("publisher"), api("consumer")
    graph = build()
    if change == "binding":
        graph.set((S.grasp_3__target, S.bindsParameter, S.MoveJ_Skill__target))
    elif change == "condition":
        graph.set((S.PoseInRobotFrame, S.ruleId, Literal("invented_rule", datatype=XSD.string)))
    else:
        graph.set((S.main_Sequence_2, S.stepIndex, Literal(1, datatype=XSD.integer)))
    target = tmp_path / "invalid.owl"
    with pytest.raises(ValueError):
        publisher.publish_ontology(graph, target, mode="real")
    assert not target.exists(), "Rejected publication left a visible output"
    # Adding correct metadata alone must not bypass the consumer's semantic checks.
    reference = Graph().parse(FIXTURES / "owl/agent_real.owl", format="xml")
    for prop in (S.artifactContract, S.validationShapes):
        graph.set((ONTOLOGY, prop, reference.value(ONTOLOGY, prop)))
    graph.serialize(target, format="xml")
    with pytest.raises(ValueError):
        consumer.load_ontology(target, mode="real")


@pytest.mark.parametrize("declaration", [
    '<!DOCTYPE rdf:RDF SYSTEM "file:///must-not-read">',
    '<!ENTITY external SYSTEM "https://invalid.example/must-not-fetch">',
])
def test_dtd_and_entity_are_rejected_before_rdf_parsing(tmp_path, declaration):
    consumer = api("consumer")
    target = tmp_path / "unsafe.owl"
    target.write_text('<?xml version="1.0"?>\n' + declaration + '\n<rdf:RDF/>')
    with patch.object(Graph, "parse", side_effect=AssertionError("Unsafe XML reached RDF parser")):
        with pytest.raises(ValueError):
            consumer.load_ontology(target, mode="real")


def test_publication_does_not_overwrite_existing_input(tmp_path):
    publisher = api("publisher")
    target = tmp_path / "existing.owl"
    original = (FIXTURES / "owl/agent_real.owl").read_bytes()
    target.write_bytes(original)
    with pytest.raises((ValueError, FileExistsError)):
        publisher.publish_ontology(build(), target, mode="real")
    assert target.read_bytes() == original


def test_non_owl_input_is_rejected_before_reading(tmp_path):
    consumer = api("consumer")
    with patch.object(Path, "read_bytes", side_effect=AssertionError("Unsupported file was read")):
        with pytest.raises(ValueError):
            consumer.load_ontology(tmp_path / "input.ttl", mode="real")

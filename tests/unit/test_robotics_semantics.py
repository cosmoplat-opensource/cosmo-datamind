"""Preserve robotics graph meaning and reject unsupported inputs — DR-059 / DR-060 / ROB-03."""
from copy import deepcopy
import hashlib
import xml.etree.ElementTree as ET

import pytest
from rdflib import Graph, Literal, OWL, RDF
from rdflib.compare import isomorphic

from tests.robotics_support import MODELS, SOURCE, S, api, build, source_root, trees


def test_repeated_invocations_ports_bindings_and_scope_survive_build():
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (SOURCE, MODELS)]
    graph = build()
    assert isinstance(graph, Graph)
    assert len(set(graph.subjects(RDF.type, S.AtomicSkill))) == 13
    assert len(set(graph.subjects(RDF.type, S.CompositeSkill))) == 3
    calls = set(graph.subjects(RDF.type, S.SkillInvocation))
    assert len(calls) == 25
    grasp = sorted(graph.objects(S.Grasp_Skill, S.hasStep), key=lambda c: int(graph.value(c, S.stepIndex)))
    moves = [c for c in grasp if (c, S.invokesSkill, S.MoveL_Skill) in graph]
    assert len(set(moves)) == 3
    assert [int(graph.value(c, S.stepIndex)) for c in moves] == [3, 4, 6]
    for call in calls:
        assert (call, RDF.type, OWL.NamedIndividual) in graph
        assert graph.value(call, S.inProcedure) is not None
    bindings = set(graph.subjects(RDF.type, S.ParameterBinding))
    assert len(bindings) == 129
    for binding in bindings:
        assert len(list(graph.objects(binding, S.bindsParameter))) == 1
        assert len(list(graph.objects(binding, S.rawBinding))) == 1
    refs = set(graph.subjects(RDF.type, S.DataReference))
    assert len(refs) == 24
    assert len({(str(graph.value(r, S.scope)), str(graph.value(r, S.dataKey))) for r in refs}) == 24
    ports = list(graph.subjects(RDF.type, S.PortContract))
    assert ports and all(isinstance(graph.value(p, S.cppType), Literal) for p in ports)
    assert graph.value(S.PartType_A, S.hasPerceptionProfile) is None
    assert graph.value(S.Slot_B_01, S.hasPlacementProfile) is None
    assert not list(graph.subjects(RDF.type, S.Observation))
    assert not list(graph.triples((None, OWL.sameAs, None)))
    rebuilt = api("semantics").xml_from_graph(graph)
    assert trees(rebuilt) == trees(source_root())
    assert rebuilt.find("TreeNodesModel") is None
    roundtrip = Graph().parse(data=graph.serialize(format="xml"), format="xml")
    assert isomorphic(graph, roundtrip)
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (SOURCE, MODELS)]


@pytest.mark.parametrize("change", ["unknown_node", "reorder", "control_node", "conflicting_port"])
def test_unsupported_template_and_conflicting_models_are_rejected(tmp_path, change):
    importer = api("importer")
    source, models = deepcopy(source_root()), ET.parse(MODELS).getroot()
    sequence = source.find("BehaviorTree[@ID='grasp']/Sequence")
    if change == "unknown_node":
        sequence[0].tag = "InventedRobotAction"
    elif change == "reorder":
        first = sequence[0]
        sequence.remove(first)
        sequence.append(first)
    elif change == "control_node":
        sequence.tag = "Fallback"
    else:
        models.find("TreeNodesModel/Action[@ID='MoveL']/input_port[@name='target']").set("type", "bool")
    source_path, model_path = tmp_path / "source.xml", tmp_path / "models.xml"
    ET.ElementTree(source).write(source_path)
    ET.ElementTree(models).write(model_path)
    with pytest.raises(ValueError) as caught:
        importer.build_graph(source_path, model_path, profile="ur_pick_place")
    assert str(caught.value).strip()


@pytest.mark.parametrize("content", ["", "<root>", '<root BTCPP_format="4"/>'])
def test_empty_malformed_and_incomplete_inputs_are_rejected(tmp_path, content):
    importer = api("importer")
    source = tmp_path / "invalid.xml"
    source.write_text(content)
    with pytest.raises((ValueError, ET.ParseError)):
        importer.build_graph(source, MODELS, profile="ur_pick_place")


def test_unknown_profile_and_missing_model_are_rejected(tmp_path):
    importer = api("importer")
    with pytest.raises(ValueError):
        importer.build_graph(SOURCE, MODELS, profile="unknown_profile")
    with pytest.raises((ValueError, FileNotFoundError)):
        importer.build_graph(SOURCE, tmp_path / "missing.xml", profile="ur_pick_place")

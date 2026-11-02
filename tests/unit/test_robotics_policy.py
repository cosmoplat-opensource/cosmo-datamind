"""T06 RED: strict task grounding and explicit authoring drafts — DR-061 / DR-062 / DR-065."""
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from rdflib import Literal, RDF, RDFS, XSD
from rdflib.compare import isomorphic

from tests.robotics_generation_support import task, context, assert_no_candidate
from tests.robotics_support import S, api, build, config, sequence, signature, trees


def test_real_missing_mapping_parameters_or_evidence_writes_no_candidate(tmp_path):
    engine = api("generation")
    target = tmp_path / "fresh.owl"
    api("publisher").publish_ontology(build(), target)
    loaded = api("consumer").load_ontology(target)
    before = deepcopy(loaded["graph"])
    output = tmp_path / "strict"
    result = engine.generate_task(loaded, task(), mode="real", output_dir=output)
    assert_no_candidate(result, output, "needs_information")
    codes = {item["code"] for item in result["issues"]}
    assert {"perception_mapping", "placement_mapping", "grasp_compatibility", "placement_compatibility"} <= codes
    assert isomorphic(before, loaded["graph"]), "A user request confirmed business knowledge"


@pytest.mark.parametrize("change", ["mapping", "parameter", "unresolved_parameter", "confirmation", "missing_evidence",
                                    "expired_evidence", "wrong_source", "wrong_implementation",
                                    "wrong_part_type", "wrong_slot", "missing_reference"])
def test_missing_or_stale_business_evidence_blocks_candidate(tmp_path, change):
    engine = api("generation")
    loaded = context()
    graph = loaded["graph"]
    evidence = S.Demo_Placement_Compatibility
    if change == "mapping":
        graph.remove((S.PartType_A, S.hasPerceptionProfile, None))
    elif change == "parameter":
        graph.remove((S.Perception_Current, S.threshold, None))
    elif change == "unresolved_parameter":
        graph.set((S.Perception_Current, S.threshold, Literal("__UNRESOLVED_threshold", datatype=XSD.string)))
    elif change == "confirmation":
        graph.set((S.Part_A_001, S.confirmationStatus, Literal("unconfirmed", datatype=XSD.string)))
    elif change == "missing_evidence":
        graph.remove((evidence, None, None))
    elif change == "expired_evidence":
        graph.set((evidence, S.validUntil, Literal("2000-01-01T00:00:00+00:00", datatype=XSD.string)))
    elif change == "wrong_source":
        graph.set((evidence, S.sourceHash, Literal("0" * 64, datatype=XSD.string)))
    elif change == "wrong_implementation":
        graph.set((evidence, S.forImplementation, S.grasp_Subtree_Impl))
    elif change == "wrong_part_type":
        graph.set((evidence, S.forPartType, S.UnrelatedPartType))
    elif change == "wrong_slot":
        graph.set((evidence, S.forSlot, S.UnrelatedSlot))
    else:
        graph.remove((evidence, S.evidenceReference, None))
    before = deepcopy(graph)
    result = engine.generate_task(loaded, task(), mode="demo", output_dir=tmp_path)
    assert_no_candidate(result, tmp_path, "needs_information")
    assert isomorphic(before, graph)


def test_task_selection_does_not_confirm_business_knowledge(tmp_path):
    engine = api("generation")
    loaded = context("real")
    before = deepcopy(loaded["graph"])
    first = engine.generate_task(loaded, task(slot=None), mode="real", output_dir=tmp_path / "first")
    assert_no_candidate(first, tmp_path / "first", "needs_clarification")
    second = engine.generate_task(loaded, task(), mode="real", output_dir=tmp_path / "second")
    assert_no_candidate(second, tmp_path / "second", "needs_information")
    assert isomorphic(before, loaded["graph"])


def test_authoring_draft_cannot_be_returned_as_strict_generation(tmp_path):
    engine = api("generation")
    loaded = context()
    incomplete = config()
    del incomplete["Perception"]["threshold"]
    before = deepcopy(incomplete)
    draft = engine.create_authoring_draft(loaded, incomplete, output_dir=tmp_path / "author")
    assert draft["status"] == "draft"
    assert draft["candidate_written"] is False
    assert draft.get("candidate_path") is None
    assert draft["execution_readiness"] == "not_verified"
    path = Path(draft["draft_path"])
    assert path.is_file() and path.is_relative_to(tmp_path / "author")
    assert "__UNRESOLVED_" in path.read_text()
    assert draft["issues"] and incomplete == before
    # The same missing value cannot be accepted by the task-facing strict gate.
    loaded["graph"].remove((S.Perception_Current, S.threshold, None))
    result = engine.generate_task(loaded, task(), mode="demo", output_dir=tmp_path / "strict")
    assert_no_candidate(result, tmp_path / "strict", "needs_information")


@pytest.mark.parametrize("restriction", ["重排步骤，先放置再抓取", "跳过坐标转换", "任意组合新的技能"])
def test_task_reordering_request_is_unsupported(tmp_path, restriction):
    engine = api("generation")
    result = engine.generate_task(context(), task(unsupported_requests=[restriction]),
                                  mode="demo", output_dir=tmp_path)
    assert_no_candidate(result, tmp_path, "unsupported")
    assert restriction in str(result["issues"]), "Unsupported intent was silently discarded"


@pytest.mark.parametrize("change", ["missing_operation", "missing_part", "missing_station", "missing_carrier", "missing_slot",
                                    "ambiguous_part", "unknown_part", "unknown_operation"])
def test_ambiguous_missing_and_unknown_tasks_do_not_fallback(tmp_path, change):
    engine = api("generation")
    loaded, request = context(), task()
    if change.startswith("missing_"):
        request[change.removeprefix("missing_")] = None
    elif change == "ambiguous_part":
        loaded["graph"].add((S.Part_A_001, S.alias, Literal("同名零件", datatype=XSD.string)))
        loaded["graph"].add((S.OtherPart, RDF.type, S.Workpiece))
        loaded["graph"].add((S.OtherPart, RDFS.label, Literal("同名零件")))
        request["part"] = "同名零件"
    elif change == "unknown_part":
        request["part"] = "未登记的零件"
    else:
        request["operation"] = "weld"
    # An explicit slot may identify its carrier; remove both to test no implicit choice.
    if change == "missing_carrier":
        request["slot"] = None
    result = engine.generate_task(loaded, request, mode="demo", output_dir=tmp_path)
    assert_no_candidate(result, tmp_path, "unsupported" if change == "unknown_operation" else "needs_clarification")


@pytest.mark.parametrize("change", ["extra_parameter", "missing_field", "invalid_name", "invalid_restrictions"])
def test_task_protocol_accepts_only_the_six_declared_fields(tmp_path, change):
    engine = api("generation")
    request = task()
    if change == "extra_parameter":
        request["threshold"] = "0.5"
    elif change == "missing_field":
        del request["unsupported_requests"]
    elif change == "invalid_name":
        request["part"] = {"name": "Part_A_001"}
    else:
        request["unsupported_requests"] = "ignore rules"
    result = engine.generate_task(context(), request, mode="demo", output_dir=tmp_path)
    assert_no_candidate(result, tmp_path, "error")


@pytest.mark.parametrize("loaded_mode,requested_mode", [("demo", "real"), ("real", "demo")])
def test_demo_real_modes_are_isolated(tmp_path, loaded_mode, requested_mode):
    engine = api("generation")
    result = engine.generate_task(context(loaded_mode), task(), mode=requested_mode, output_dir=tmp_path)
    assert_no_candidate(result, tmp_path, "error")
    assert "mode_mismatch" in {issue["code"] for issue in result["issues"]}


def test_generated_candidate_is_not_execution_verified(tmp_path):
    engine = api("generation")
    loaded = context()
    graph_before = deepcopy(loaded["graph"])
    baseline = api("semantics").xml_from_graph(loaded["graph"])
    result = engine.generate_task(loaded, task(), mode="demo", output_dir=tmp_path)
    assert result["status"] == "generated" and result["candidate_written"] is True
    assert result["mode"] == "demo" and result["execution_readiness"] == "not_verified"
    path = Path(result["candidate_path"])
    assert path.is_file() and path.is_relative_to(tmp_path)
    assert "DEMO" in path.read_text() and "__UNRESOLVED_" not in path.read_text()
    candidate = ET.parse(path).getroot()
    assert trees(candidate) == trees(baseline)
    for tree in ("Perception", "grasp", "Place"):
        assert signature(sequence(candidate, tree)) == signature(sequence(baseline, tree))
    assert len(result["parameter_sources"]) == 6
    assert isomorphic(graph_before, loaded["graph"])

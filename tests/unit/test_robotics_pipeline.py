"""T08 RED: fresh source through explicit demo supplementation to candidate — DR-060 / DR-065."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from rdflib import RDF
from rdflib.compare import isomorphic

from tests.robotics_support import FIXTURES, SOURCE, MODELS, S, api, trees

SUPPLEMENT = FIXTURES / "demo_supplement.json"


def test_fresh_build_with_explicit_demo_supplement_generates_candidate(tmp_path, monkeypatch):
    demo = api("demo")
    source, models = tmp_path / "source.xml", tmp_path / "models.xml"
    source.write_bytes(SOURCE.read_bytes())
    models.write_bytes(MODELS.read_bytes())
    original_open = Path.open

    def forbid_legacy_owl(path, *args, **kwargs):
        assert not path.is_relative_to(FIXTURES / "owl"), "Fresh pipeline read a prebuilt OWL"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_legacy_owl)
    graph = api("importer").build_graph(source, models)
    before = deepcopy(graph)
    augmented = demo.apply_demo_supplement(graph, SUPPLEMENT)
    assert isomorphic(graph, before), "Demo supplement mutated the real input graph"
    assert augmented["changes"]
    assert augmented["supplement_sha256"] == hashlib.sha256(SUPPLEMENT.read_bytes()).hexdigest()
    enriched = augmented["graph"]
    # No source, condition, skill, invocation, binding or port definitions may be replaced.
    protected = {S.BehaviorTree_Source, S.NodeModels_Source}
    for cls in ("AtomicSkill", "CompositeSkill", "TaskProcedure", "SkillInvocation", "SkillParameter",
                "ParameterBinding", "PortContract", "DataReference", "SkillCondition", "SkillImplementation"):
        protected.update(graph.subjects(RDF.type, S[cls]))
    for subject in protected:
        assert set(graph.predicate_objects(subject)) == set(enriched.predicate_objects(subject))
    assert trees(api("semantics").xml_from_graph(enriched)) == trees(api("semantics").xml_from_graph(graph))
    published = tmp_path / "published.owl"
    api("publisher").publish_ontology(enriched, published, mode="demo")
    loaded = api("consumer").load_ontology(published, mode="demo")
    source.rename(tmp_path / "source.removed")
    models.rename(tmp_path / "models.removed")
    supplement = json.loads(SUPPLEMENT.read_text())
    result = api("generation").generate_task(loaded, supplement["task"], mode="demo", output_dir=tmp_path / "run")
    assert result["status"] == "generated" and result["execution_readiness"] == "not_verified"
    template = api("semantics").xml_from_graph(graph)
    expected = api("compiler").instantiate(template, supplement["config"], baseline=template,
        models_root=api("semantics").node_models_from_graph(graph),
        config_paths=api("semantics").configuration_paths(graph))
    assert trees(ET.parse(result["candidate_path"]).getroot()) == trees(expected)
    assert result["validation"]["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert result["validation"]["models_sha256"] == hashlib.sha256(MODELS.read_bytes()).hexdigest()
    assert result["validation"]["ontology_sha256"] == hashlib.sha256(published.read_bytes()).hexdigest()
    assert len(result["parameter_sources"]) == 6


@pytest.mark.parametrize("change", ["source_hash", "real_mode", "fixed_parameter"])
def test_demo_supplement_cannot_rewrite_template_or_claim_real_evidence(tmp_path, change):
    demo = api("demo")
    data = json.loads(SUPPLEMENT.read_text())
    if change == "source_hash":
        data["source_sha256"] = "0" * 64
    elif change == "real_mode":
        data["mode"] = "real"
    else:
        data["config"]["grasp"]["robot_ref"] = "unapproved"
    path = tmp_path / "invalid-supplement.json"
    path.write_text(json.dumps(data))
    graph = api("importer").build_graph(SOURCE, MODELS)
    before = deepcopy(graph)
    with pytest.raises(ValueError):
        demo.apply_demo_supplement(graph, path)
    assert isomorphic(graph, before)


def test_source_and_archive_files_remain_unchanged(tmp_path):
    demo = api("demo")
    inputs = [p for p in FIXTURES.rglob("*") if p.is_file()]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    graph = api("importer").build_graph(SOURCE, MODELS)
    result = demo.apply_demo_supplement(graph, SUPPLEMENT)
    api("publisher").publish_ontology(result["graph"], tmp_path / "published.owl", mode="demo")
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}

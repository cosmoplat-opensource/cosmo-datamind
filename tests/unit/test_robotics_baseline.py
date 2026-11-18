"""Verify frozen fixture provenance and observed baseline — DR-147 / ROB-01."""
import hashlib
import json
from pathlib import Path

from rdflib import Graph, Namespace, URIRef
import pytest

from tests.robotics_generation_support import assert_no_candidate, task
from tests.robotics_support import MODELS, SOURCE, api, source_root, trees

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "robotics"
S = Namespace("https://example.org/ur-skill#")


def test_fixture_digests_and_compatibility_cases_are_recorded():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    paths = [entry["path"] for entry in manifest["files"]]
    assert len(paths) == len(set(paths)) == 8
    for entry in manifest["files"]:
        path = (FIXTURES / entry["path"]).resolve()
        assert path.is_relative_to(FIXTURES.resolve())
        content = path.read_bytes()
        assert len(content) == entry["bytes"]
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        assert entry["source_path"]
    before = {name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() for name in paths}
    graph = api('importer').build_graph(SOURCE, MODELS)
    assert trees(api('semantics').xml_from_graph(graph)) == trees(source_root())
    assert before == {name: hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() for name in paths}


@pytest.mark.parametrize('filename,mode,accepted', [
    ('authoring.owl', 'real', False), ('agent_real.owl', 'real', True),
    ('agent_demo.owl', 'demo', True), ('agent_demo.owl', 'real', False),
])
def test_frozen_consumer_contract_cases_execute(filename, mode, accepted):
    consumer = api('consumer')
    if not accepted:
        with pytest.raises(ValueError):
            consumer.load_ontology(FIXTURES / 'owl' / filename, mode=mode)
        return
    loaded = consumer.load_ontology(FIXTURES / 'owl' / filename, mode=mode)
    assert loaded['validation']['shacl_conforms'] is True
    assert loaded['validation']['execution_readiness'] == 'not_verified'


def test_frozen_real_task_without_evidence_still_refuses_a_candidate(tmp_path):
    loaded = api('consumer').load_ontology(FIXTURES / 'owl' / 'agent_real.owl', mode='real')
    result = api('generation').generate_task(loaded, task(), mode='real', output_dir=tmp_path)
    assert_no_candidate(result, tmp_path, 'needs_information')


def test_authoring_and_agent_owl_are_distinct_contract_fixtures():
    ontology = URIRef("https://example.org/ur-skill")
    authoring = Graph().parse(FIXTURES / "owl/authoring.owl", format="xml")
    agent = Graph().parse(FIXTURES / "owl/agent_real.owl", format="xml")
    assert not list(authoring.objects(ontology, S.artifactContract))
    assert str(agent.value(ontology, S.artifactContract)) == "ur-pick-place-owl-v2"
    assert agent.value(ontology, S.validationShapes)

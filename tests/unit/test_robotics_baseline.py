"""Verify frozen fixture provenance and observed baseline — DR-065 / ROB-01."""
import hashlib
import json
from pathlib import Path

from rdflib import Graph, Namespace, URIRef

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
    audit = Path(__file__).resolve().parents[2] / "docs/audits/IR-016-baseline-results.json"
    results = json.loads(audit.read_text())
    assert results["source_unchanged"] and results["matched_baseline"]
    cases = {case["name"]: case for case in results["cases"]}
    assert cases["cross_directory_import_collision"]["actual_exit"] == 1
    assert "CONDITION_SPECS" in cases["cross_directory_import_collision"]["stderr"]
    assert "REJECT" in cases["consumer_contract_matrix"]["stdout"]
    assert "ACCEPT" in cases["consumer_contract_matrix"]["stdout"]
    assert "needs_information" in cases["strict_incomplete_task"]["stdout"]


def test_authoring_and_agent_owl_are_distinct_contract_fixtures():
    ontology = URIRef("https://example.org/ur-skill")
    authoring = Graph().parse(FIXTURES / "owl/authoring.owl", format="xml")
    agent = Graph().parse(FIXTURES / "owl/agent_real.owl", format="xml")
    assert not list(authoring.objects(ontology, S.artifactContract))
    assert str(agent.value(ontology, S.artifactContract)) == "ur-pick-place-owl-v2"
    assert agent.value(ontology, S.validationShapes)

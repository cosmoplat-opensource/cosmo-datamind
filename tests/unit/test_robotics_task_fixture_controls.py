"""T04 GREEN controls: fixtures and independent oracle work before task service exists — DR-062 / DR-066."""
import xml.etree.ElementTree as ET

import pytest

from domains.robotics import evidence, generation
from domains.robotics.consumer import load_ontology_bytes
from domains.robotics.task_completion import inspect_gaps
from tests.robotics_governance_support import NOW, TASK
from tests.robotics_support import S, trees
from tests.robotics_task_support import expected_xml, fixture


def test_ready_fixture_generates_with_existing_kernel_and_independent_xml_oracle(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'utc_now', lambda: NOW)
    assets, version, _ = fixture(tmp_path / 'assets', ready=True)
    context = load_ontology_bytes(assets.read_payload(version['ontology_id'], version['version_id']))
    outcome = generation.generate_task(context, TASK, mode='real', output_dir=tmp_path / 'candidate')
    assert outcome['status'] == 'generated', outcome
    assert trees(ET.parse(outcome['candidate_path']).getroot()) == trees(expected_xml())


@pytest.mark.parametrize('owner,field', [(S.Perception_Current, 'exposureTime'),
    (S.Perception_Current, 'hasCaptureJoints'), (S.Placement_Current, 'hasTargetPose'),
    (S.Part_A_001, 'hasPartType'), (S.Station_B, 'hasCarrier'), (S.Carrier_B_01, 'hasSlot')])
def test_incomplete_candidate_fixture_is_storable_and_projects_the_expected_gap(tmp_path, owner, field):
    from rdflib import Graph
    assets, version, _ = fixture(tmp_path / 'assets', ready=True,
        mutate=lambda graph: graph.remove((owner, S[field], None)))
    raw = assets.read_payload(version['ontology_id'], version['version_id'])
    projection = inspect_gaps(Graph().parse(data=raw, format='xml'), TASK, mode='real', now=NOW)
    assert projection['status'] == 'needs_information'
    assert any(item['field'] == field and item['entity_iri'] == str(owner) for item in projection['items'])

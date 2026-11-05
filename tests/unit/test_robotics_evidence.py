"""T02 RED: scoped human evidence, expiry and strict generation — DR-061 / DR-062 / DR-065."""
from copy import deepcopy

import pytest
from rdflib import Literal, RDF, URIRef, XSD

from tests.robotics_governance_support import (LATER, NOW, POLICY, approved_evidence,
                                               evidence_spec, graph, module)
from tests.robotics_support import S


def test_registration_is_pending_and_captures_actual_sources_and_scope():
    api = module('domains.robotics.evidence')
    original = graph()
    before = set(original)
    registered = api.register_evidence(original, evidence_spec(), mode='real', now=NOW)
    assert set(original) == before
    assert (S.TestEvidence, RDF.type, S.ReadinessEvidence) in registered
    assert str(registered.value(S.TestEvidence, S.confirmationStatus)) == 'pending'
    assert str(registered.value(S.TestEvidence, S.sourceHash)) == str(original.value(S.BehaviorTree_Source, S.sha256))
    assert str(registered.value(S.TestEvidence, S.modelsHash)) == str(original.value(S.NodeModels_Source, S.sha256))
    assert len(str(registered.value(S.TestEvidence, S.configurationHash))) == 64
    evaluated = api.evaluate_evidence(registered, str(S.TestEvidence), now=NOW, revocations=[])
    assert evaluated['valid'] is False
    assert 'unreviewed' in {i['code'] for i in evaluated['issues']}


def test_review_is_exact_content_signed_and_does_not_confirm_unrelated_entities():
    api = module('domains.robotics.evidence')
    registered = api.register_evidence(graph(), evidence_spec(), mode='real', now=NOW)
    before = set(registered)
    reviewed = api.review_evidence(registered, str(S.TestEvidence), decision='accepted', actor='reviewer', reason='test', now=NOW)
    assert set(registered) == before
    assert api.evaluate_evidence(reviewed, str(S.TestEvidence), now=NOW, revocations=[])['valid'] is True
    assert str(reviewed.value(S.TestEvidence, S.reviewActor)) == 'reviewer'
    assert len(str(reviewed.value(S.TestEvidence, S.reviewedContentHash))) == 64
    for entity in [S.Part_A_001, S.PartType_A, S.Station_B, S.Slot_B_01]:
        assert reviewed.value(entity, S.confirmationStatus) == registered.value(entity, S.confirmationStatus)
    changed = deepcopy(reviewed)
    changed.set((S.TestEvidence, S.evidenceReference, Literal('different proof', datatype=XSD.string)))
    assert api.evaluate_evidence(changed, str(S.TestEvidence), now=NOW, revocations=[])['valid'] is False


@pytest.mark.parametrize('changes', [
    {'reference': ''}, {'reference': 'demo-only:fake'}, {'reference': '/etc/passwd'},
    {'valid_until': '2030-01-02'}, {'valid_until': '2030-01-02T00:00:00'},
    {'valid_until': '2029-12-31T23:00:00+00:00'},
    {'kind': 'unknown'}, {'implementation': str(S.DoesNotExist)},
    {'part_type': str(S.Station_B)},
    {'kind': 'placement_compatibility', 'implementation': str(S.Place_Subtree_Impl), 'slot': None},
    {'source_hash': '0' * 64}, {'confirmation_status': 'confirmed'},
])
def test_invalid_or_forged_evidence_is_rejected_without_graph_mutation(changes):
    api = module('domains.robotics.evidence')
    original = graph()
    before = set(original)
    with pytest.raises(ValueError):
        api.register_evidence(original, evidence_spec(**changes), mode='real', now=NOW)
    assert set(original) == before


@pytest.mark.parametrize('actor,reason,decision', [('', 'test', 'accepted'), ('r', '', 'accepted'), ('r', 'x', 'maybe')])
def test_review_requires_signed_reason_and_valid_decision(actor, reason, decision):
    api = module('domains.robotics.evidence')
    registered = api.register_evidence(graph(), evidence_spec(), mode='real', now=NOW)
    with pytest.raises(ValueError):
        api.review_evidence(registered, str(S.TestEvidence), decision=decision, actor=actor, reason=reason, now=NOW)
    assert str(registered.value(S.TestEvidence, S.confirmationStatus)) == 'pending'


@pytest.mark.parametrize('case,code', [('source', 'source_changed'), ('models', 'models_changed'),
    ('parameter', 'scope_changed'), ('mapping', 'scope_changed'), ('implementation', 'scope_changed'),
    ('expired', 'expired'), ('revoked', 'revoked'), ('superseded', 'superseded')])
def test_evidence_invalidation_has_specific_reason_without_rewriting_snapshot(case, code):
    api = module('domains.robotics.evidence')
    original = graph()
    original.add((S.PartType_A, S.hasPerceptionProfile, S.Perception_Current))
    reviewed = approved_evidence(api, original)
    before = set(reviewed)
    changed, revocations, now = deepcopy(reviewed), [], NOW
    if case in ('source', 'models'):
        changed.set(((S.BehaviorTree_Source if case == 'source' else S.NodeModels_Source), S.sha256, Literal('0' * 64)))
    elif case == 'parameter':
        changed.set((S.Perception_Current, S.threshold, Literal('0.71', datatype=XSD.string)))
    elif case == 'mapping':
        changed.remove((S.PartType_A, S.hasPerceptionProfile, S.Perception_Current))
    elif case == 'implementation':
        changed.set((S.grasp_Sequence_1, S.stepIndex, Literal(9)))
    elif case == 'expired':
        now = LATER
    else:
        revocations = [{'evidence_iri': str(S.TestEvidence), 'kind': case, 'actor': 'reviewer',
                        'reason': 'synthetic cancellation', 'created_at': NOW}]
    evaluated = api.evaluate_evidence(changed, str(S.TestEvidence), now=now, revocations=revocations)
    assert evaluated['valid'] is False
    assert code in {i['code'] for i in evaluated['issues']}
    assert set(reviewed) == before


def test_unrelated_label_does_not_invalidate_configuration_scope():
    api = module('domains.robotics.evidence')
    reviewed = approved_evidence(api)
    reviewed.add((S.UnrelatedDisplay, S.alias, Literal('renamed')))
    assert api.evaluate_evidence(reviewed, str(S.TestEvidence), now=NOW, revocations=[])['valid'] is True


def test_placement_evidence_cannot_authorize_another_slot_or_part_type():
    api = module('domains.robotics.evidence')
    reviewed = approved_evidence(api, kind='placement_compatibility', implementation=str(S.Place_Subtree_Impl), slot=str(S.Slot_B_01))
    evaluated = api.evaluate_evidence(reviewed, str(S.TestEvidence), now=NOW, revocations=[],
        required_scope={'kind': 'placement_compatibility', 'implementation': str(S.Place_Subtree_Impl),
                        'part_type': str(S.PartType_A), 'slot': str(S.AnotherSlot)})
    assert not evaluated['valid']
    assert 'scope_mismatch' in {i['code'] for i in evaluated['issues']}


def test_new_policy_cannot_fall_back_to_legacy_confirmed_evidence(tmp_path):
    module('domains.robotics.evidence')
    # An otherwise working legacy demo is a positive control for old compatibility.
    from tests.storage_pipeline_support import fresh_payload
    consumer = module('domains.robotics.consumer')
    generation = module('domains.robotics.generation')
    context = consumer.load_ontology_bytes(fresh_payload('demo'), mode='demo')
    from tests.robotics_support import FIXTURES
    import json
    task = json.loads((FIXTURES / 'demo_supplement.json').read_text())['task']
    first = generation.generate_task(context, task, mode='demo', output_dir=tmp_path / 'legacy')
    assert first['status'] == 'generated'
    governed = deepcopy(context['graph'])
    governed.set((URIRef('https://example.org/ur-skill'), S.evidencePolicy, Literal(POLICY, datatype=XSD.string)))
    payload = governed.serialize(format='xml', encoding='utf-8')
    # Old confirmationStatus alone must not satisfy the new evidence policy.
    output = tmp_path / 'governed'
    try:
        loaded = consumer.load_ontology_bytes(payload, mode='demo')
    except ValueError:
        assert not output.exists()
    else:
        result = generation.generate_task(loaded, task, mode='demo', output_dir=output)
        assert result['status'] in ('needs_information', 'error')
        assert result['candidate_written'] is False
        assert not list(output.rglob('*.xml'))
        assert result['execution_readiness'] == 'not_verified'


def test_real_knowledge_projection_never_claims_execution_or_fills_evidence():
    authoring = module('domains.robotics.authoring')
    original = graph()
    projection = authoring.inspect_knowledge(original)
    assert projection['execution_readiness'] == 'not_verified'
    assert projection['issues']
    assert not list(original.subjects(RDF.type, S.ReadinessEvidence))


def test_rejected_evidence_cannot_satisfy_validity_gate():
    api = module('domains.robotics.evidence')
    registered = api.register_evidence(graph(), evidence_spec(), mode='real', now=NOW)
    rejected = api.review_evidence(registered, str(S.TestEvidence), decision='rejected',
                                  actor='test reviewer', reason='inadequate scope', now=NOW)
    outcome = api.evaluate_evidence(rejected, str(S.TestEvidence), now=NOW, revocations=[])
    assert not outcome['valid']
    assert 'rejected' in {i['code'] for i in outcome['issues']}
    assert str(registered.value(S.TestEvidence, S.confirmationStatus)) == 'pending'

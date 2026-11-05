"""T02 RED: semantic comparison and explicit three-way source merging — DR-059 / DR-060 / DR-061."""
from copy import deepcopy

import pytest
from rdflib import BNode, Graph, Literal, RDFS, XSD
from rdflib.compare import isomorphic

from tests.robotics_governance_support import (actual_build, field, graph, module, save_args)
from tests.robotics_support import S, SOURCE
from ontology_assets import AssetStore
from tests.build_pipeline_support import blob_bytes, db_rows


def test_diff_ignores_serialization_and_blank_node_identifiers():
    api = module('domains.robotics.version_diff')
    original = graph()
    original.add((S.Unknown, S.extension, BNode('first')))
    original.add((BNode('first'), S.value, Literal('x', lang='en')))
    reparsed = Graph().parse(data=original.serialize(format='xml'), format='xml')
    assert isomorphic(original, reparsed)
    assert api.diff_graphs(original, reparsed) == []
    reparsed.set((S.Perception_Current, S.threshold, Literal('0.74', datatype=XSD.string)))
    changes = api.diff_graphs(original, reparsed)
    assert len(changes) == 1
    assert changes[0]['subject'] == str(S.Perception_Current)
    assert changes[0]['predicate'] == str(S.threshold)
    assert changes[0]['before'] != changes[0]['after']


def test_diff_distinguishes_language_datatype_and_unrecognized_semantics():
    api = module('domains.robotics.version_diff')
    original = graph()
    original.add((S.Unknown, S.measure, Literal('1.20', datatype=XSD.decimal, normalize=False)))
    changed = deepcopy(original)
    changed.set((S.Unknown, S.measure, Literal('1.20')))
    changed.set((S.PartType_A, RDFS.label, Literal('示例零件类型A', lang='en')))
    diff = api.diff_graphs(original, changed)
    assert {(d['subject'], d['predicate']) for d in diff} == {
        (str(S.Unknown), str(S.measure)), (str(S.PartType_A), str(RDFS.label))}


def test_three_way_merge_retains_independent_manual_and_source_changes():
    api = module('domains.robotics.version_diff')
    base = graph()
    current, incoming = deepcopy(base), deepcopy(base)
    current.set((S.PartType_A, RDFS.label, Literal('manual', lang='zh')))
    incoming.set((S.Perception_Current, S.threshold, Literal('0.66', datatype=XSD.string)))
    before = [set(g) for g in [base, current, incoming]]
    result = api.merge_graphs(base, current, incoming, decisions={})
    assert result['conflicts'] == []
    merged = result['graph']
    assert (S.PartType_A, RDFS.label, Literal('manual', lang='zh')) in merged
    assert (S.Perception_Current, S.threshold, Literal('0.66', datatype=XSD.string)) in merged
    assert set(base) - set(base.triples((S.PartType_A, RDFS.label, None))) - set(base.triples((S.Perception_Current, S.threshold, None))) <= set(merged)
    assert [set(g) for g in [base, current, incoming]] == before


@pytest.mark.parametrize('choice,value', [('current', '0.71'), ('incoming', '0.72'), ('manual', '0.73')])
def test_same_field_conflict_requires_explicit_valid_choice(choice, value):
    api = module('domains.robotics.version_diff')
    base = graph()
    current, incoming = deepcopy(base), deepcopy(base)
    current.set((S.Perception_Current, S.threshold, Literal('0.71', datatype=XSD.string)))
    incoming.set((S.Perception_Current, S.threshold, Literal('0.72', datatype=XSD.string)))
    preview = api.merge_graphs(base, current, incoming, decisions={})
    assert preview['graph'] is None and len(preview['conflicts']) == 1
    conflict = preview['conflicts'][0]
    assert conflict['kind'] == 'field' and conflict['subject'] == str(S.Perception_Current)
    decision = {'choice': choice}
    if choice == 'manual': decision['value'] = value
    resolved = api.merge_graphs(base, current, incoming, decisions={conflict['id']: decision})
    assert resolved['conflicts'] == []
    assert str(resolved['graph'].value(S.Perception_Current, S.threshold)) == value
    with pytest.raises(ValueError):
        api.merge_graphs(base, current, incoming, decisions={conflict['id']: {'choice': 'manual', 'value': 'NaN'}})


def test_deletion_with_manual_changes_is_not_silently_replayed():
    api = module('domains.robotics.version_diff')
    base = graph()
    current, incoming = deepcopy(base), deepcopy(base)
    current.set((S.PartType_A, RDFS.label, Literal('edited', lang='zh')))
    incoming.remove((S.PartType_A, None, None))
    incoming.remove((None, None, S.PartType_A))
    result = api.merge_graphs(base, current, incoming, decisions={})
    assert result['graph'] is None
    assert any(c['kind'] == 'delete_modify' and c['subject'] == str(S.PartType_A) for c in result['conflicts'])


def test_moved_invocations_cannot_be_matched_by_old_sequence_index():
    api = module('domains.robotics.version_diff')
    base = graph()
    current, incoming = deepcopy(base), deepcopy(base)
    current.set((S.Perception_Current, S.threshold, Literal('0.71', datatype=XSD.string)))
    incoming.set((S.grasp_Sequence_1, S.stepIndex, Literal(2)))
    incoming.set((S.grasp_Sequence_2, S.stepIndex, Literal(1)))
    result = api.merge_graphs(base, current, incoming, decisions={})
    assert result['graph'] is None
    assert any(c['kind'] == 'source_identity' for c in result['conflicts'])
    with pytest.raises(ValueError):
        api.merge_graphs(base, current, incoming, decisions={c['id']: {'choice': 'current'} for c in result['conflicts']})


def test_merge_cannot_restore_confirmation_after_evidence_scope_change():
    api = module('domains.robotics.version_diff')
    evidence = module('domains.robotics.evidence')
    from tests.robotics_governance_support import NOW, approved_evidence
    base = graph()
    base.add((S.PartType_A, S.hasPerceptionProfile, S.Perception_Current))
    current = approved_evidence(evidence, base)
    incoming = deepcopy(base)
    incoming.set((S.Perception_Current, S.threshold, Literal('0.69', datatype=XSD.string)))
    result = api.merge_graphs(base, current, incoming, decisions={})
    assert result['graph'] is None
    scopes = [c for c in result['conflicts'] if c['kind'] == 'evidence_scope']
    assert scopes
    resolved = api.merge_graphs(base, current, incoming,
                               decisions={c['id']: {'choice': 'invalidate'} for c in scopes})
    assert resolved['graph'] is not None
    assert not evidence.evaluate_evidence(resolved['graph'], str(S.TestEvidence), now=NOW, revocations=[])['valid']


def merge_setup(tmp_path, api, *, conflict=False):
    root = tmp_path / 'merge'
    base, source_run = actual_build(root, name='base')
    oid = base['ontology_id']
    service = api.GovernanceService(root)
    current = service.save(oid, **save_args(base, operations=[field(S.Perception_Current, 'threshold', '0.71')] if conflict else None))
    # Change source-derived public configuration through actual paired-XML build.
    import xml.etree.ElementTree as ET
    tree = ET.fromstring(SOURCE.read_bytes())
    tree.find("BehaviorTree[@ID='main']/Sequence")[4].set('threshold', '0.73')
    incoming, incoming_run = actual_build(root, name='incoming', source=ET.tostring(tree))
    args = {'base': {'ontology_id': oid, 'version_id': base['version_id'], 'payload_sha256': base['payload_sha256']},
            'current': {'ontology_id': oid, 'version_id': current['version_id'], 'payload_sha256': current['payload_sha256']},
            'incoming': {'ontology_id': incoming['ontology_id'], 'version_id': incoming['version_id'], 'payload_sha256': incoming['payload_sha256']},
            'expected_head_version_id': current['version_id'], 'expected_revision': 1}
    return root, service, oid, base, current, incoming, args, [source_run, incoming_run]


def test_merge_preview_is_read_only_commit_keeps_lineage_and_sources(tmp_path):
    api = module('ontology_assets.governance')
    root, service, oid, base, current, incoming, args, runs = merge_setup(tmp_path, api)
    before, files = db_rows(root), blob_bytes(root)
    preview = service.preview_merge(oid, **args)
    assert preview['conflicts'] == [] and preview['merge_token']
    assert db_rows(root) == before and blob_bytes(root) == files
    commit = {'merge_token': preview['merge_token'], 'inputs': args, 'decisions': {},
              'request_id': 'merge-1', 'actor': 'test', 'reason': '显式新来源'}
    saved = service.commit_merge(oid, **commit)
    assert saved['ontology_id'] == oid and saved['parent_version_id'] == current['version_id']
    assert saved['publication_state'] == 'candidate'
    assert saved['source_asset_ids'] == incoming['source_asset_ids']
    assert saved['metadata']['merge']['base'] == args['base']
    assert saved['metadata']['merge']['incoming'] == args['incoming']
    assert service.commit_merge(oid, **commit) == saved
    assets = AssetStore(root)
    merged = Graph().parse(data=assets.read_payload(oid, saved['version_id']), format='xml')
    assert str(merged.value(S.Perception_Current, S.threshold)) == '0.73'
    assert str(merged.value(S.PartType_A, RDFS.label)) == '人工零件类型'
    for relative, content in files.items(): assert (root / relative).read_bytes() == content
    after = db_rows(root)
    assert after['runs'] == before['runs'] and after['run_artifacts'] == before['run_artifacts']
    assert all(run['status'] == 'completed' for run in runs)
    assert assets.read_payload(oid, base['version_id']) != assets.read_payload(oid, saved['version_id'])


@pytest.mark.parametrize('case', ['stale_head', 'changed_digest', 'forged_token', 'missing_source_lineage'])
def test_merge_rejects_stale_or_unproven_inputs_without_partial_commit(tmp_path, case):
    api = module('ontology_assets.governance')
    root, service, oid, base, current, incoming, args, _ = merge_setup(tmp_path, api)
    if case == 'missing_source_lineage':
        # Similar RDF alone is insufficient: no recorded source build for this candidate.
        assets = AssetStore(root)
        raw = assets.add_version(incoming['ontology_id'], assets.read_payload(incoming['ontology_id'], incoming['version_id']),
            payload_kind='robotics_rdf', source_asset_ids=incoming['source_asset_ids'], metadata={'profile_id': 'ur_pick_place'})
        args['incoming'] = {k: raw[k] for k in ['ontology_id', 'version_id', 'payload_sha256']}
        before = db_rows(root)
        with pytest.raises(api.GovernanceError): service.preview_merge(oid, **args)
        assert db_rows(root) == before
        return
    preview = service.preview_merge(oid, **args)
    if case == 'stale_head':
        service.save(oid, **save_args(current, revision=1, request_id='other', operations=[field(value='并发编辑')]))
    elif case == 'changed_digest': args['incoming']['payload_sha256'] = '0' * 64
    else: preview['merge_token'] += 'tampered'
    before = db_rows(root)
    with pytest.raises(api.GovernanceError) as caught:
        service.commit_merge(oid, merge_token=preview['merge_token'], inputs=args, decisions={},
                             request_id='merge-bad', actor='test', reason='test')
    assert caught.value.code in {'head_conflict', 'merge_conflict', 'integrity_conflict'}
    assert db_rows(root) == before


def test_unresolved_merge_conflicts_cannot_create_candidate(tmp_path):
    api = module('ontology_assets.governance')
    root, service, oid, _, current, _, args, _ = merge_setup(tmp_path, api, conflict=True)
    preview = service.preview_merge(oid, **args)
    assert preview['conflicts']
    before = db_rows(root)
    with pytest.raises(api.GovernanceError) as caught:
        service.commit_merge(oid, merge_token=preview['merge_token'], inputs=args, decisions={},
            request_id='unresolved', actor='reviewer', reason='missing decisions')
    assert caught.value.code == 'merge_conflict'
    assert db_rows(root) == before
    assert service.head(oid) == {'version_id': current['version_id'], 'revision': 1}

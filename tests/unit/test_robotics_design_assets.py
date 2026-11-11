"""Design candidate contracts, fixed exports and old consumer isolation — DR-070 / T06 RED."""
from copy import deepcopy
import json

import pytest
from rdflib import Graph, OWL, RDF
from rdflib.compare import isomorphic

from domain_runs import RunStore
from domain_runs import build_contracts
from domains.robotics.consumer import load_ontology_bytes
from ontology_assets import AssetStore
from tests.build_pipeline_support import blob_bytes, db_rows, sha
from tests.robotics_design_support import (
    Harness, assets_api, history_bytes, no_network, proposals, reply, service_api,
)
from tests.robotics_modeling_support import api, bundle, confirmation, ur_schema

__all__ = ['no_network']
pytestmark = pytest.mark.usefixtures('no_network')


def test_confirmed_abox_is_a_distinct_run_without_extra_model_calls(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    assert state['stage'] == 'abox' and state['status'] == 'completed' and state['revision'] == 4
    runs = RunStore(h.root).list_runs()
    assert [r['operation'] for r in runs] == ['design_tbox', 'design_tbox', 'extract_abox']
    assert len(h.runtime.calls) == 2
    final = runs[-1]
    assert final['status'] == 'completed' and final['result']['status'] == 'designed'
    assert final['components']['model']['invoked'] is False
    assert final['result']['contract'] == {'id': 'robotics-design', 'version': 1}
    assert final['result']['execution_readiness'] == 'not_verified'
    assert final['result']['task_readiness'] == 'unsupported'
    assert final['output_asset_ref'] == state['output_asset_ref']
    assert final['design_input']['confirmation_sha256'] == state['schema_confirmation']['sha256']
    assert final['design_input']['mapping_sha256']


def test_design_candidate_detail_and_download_without_ur_contract(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    ref = state['output_asset_ref']
    assets = AssetStore(h.root)
    version = assets.get_version(**ref)
    raw = assets.read_payload(**ref)
    assert version['publication_state'] == 'candidate' and version['publication_mode'] is None
    assert version['metadata']['design_contract'] == {'id': 'robotics-design', 'version': 1}
    assert version['metadata']['session_ref'] == state['session_ref']
    assert version['metadata']['design_revision'] == state['revision']
    assert version['metadata']['build_run_id'] == state['run_id']
    assert version['payload_sha256'] == sha(raw)
    output = Graph().parse(data=raw, format='xml')
    assert list(output.subjects(RDF.type, OWL.NamedIndividual))
    assert not any(str(p).endswith('artifactContract') for _, p, _ in output)
    with pytest.raises(ValueError):
        load_ontology_bytes(raw, mode='real')
    reader = assets_api().DesignAssets(h.root, read_only=True)
    before, files = db_rows(h.root), blob_bytes(h.root)
    detail = reader.detail(**ref)
    assert detail['version']['payload_sha256'] == sha(raw)
    assert detail['tbox'] == state['schema_confirmation']['tbox']
    coverage = detail['report']['source_coverage']
    assert coverage['calls_emitted'] == 25 and coverage['bindings_emitted'] == 129
    graph = reader.graph(**ref)
    assert {n['kind'] for n in graph['nodes']} >= {'class', 'individual'}
    assert graph['edges'] and len(detail['abox']['individuals']) > 25
    assert reader.payload(**ref) == raw
    assert db_rows(h.root) == before and blob_bytes(h.root) == files


def test_final_manifest_binds_actual_candidate_schema_instances_and_coverage(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    runs = RunStore(h.root)
    manifest = runs.manifest(state['run_id'])
    assert manifest['output_asset_ref'] == state['output_asset_ref']
    assert manifest['session_ref'] == state['session_ref']
    records = {a['name']: a for a in runs.artifacts(state['run_id'])}
    assert set(records) == {'candidate.owl', 'design.json', 'manifest.json'}
    actual = {name: runs.read_artifact(state['run_id'], row['artifact_id']) for name, row in records.items()}
    for name, row in records.items():
        assert sha(actual[name]) == row['sha256'] and len(actual[name]) == row['byte_size']
    assert actual['candidate.owl'] == AssetStore(h.root).read_payload(**state['output_asset_ref'])
    design = json.loads(actual['design.json'])
    assert design['state']['schema_confirmation'] == state['schema_confirmation']
    evidence = bundle()
    expected = api('abox').extract(state['schema_confirmation'], evidence)
    assert design['abox'] == expected['result']
    assert design['coverage'] == expected['report']
    assert design['validation'] == runs.get_run(state['run_id'])['result']
    assert design['output_asset_ref'] == state['output_asset_ref']
    assert design['payload_sha256'] == sha(actual['candidate.owl'])
    assert design['session_ref'] == state['session_ref'] and design['revision'] == state['revision']
    assert design['components'] == runs.get_run(state['run_id'])['components']
    assert design['state']['evidence'] == evidence
    assert design['state']['messages'] and design['state']['vocabulary']['sha256']
    assert design['state']['result']['competency_questions']
    assert 'clarifications' in design['state']['result']
    assert design['coverage']['mapping_sha256'] == runs.get_run(state['run_id'])['design_input']['mapping_sha256']
    reader = assets_api().DesignAssets(h.root, read_only=True)
    assert reader.export_json(**state['output_asset_ref']) == actual['design.json']
    # The downloaded JSON alone contains everything required to reproduce RDF semantics.
    graph = Graph().parse(data=api('export').abox_owl(design['abox'], design['state']['schema_confirmation']['tbox'],
                                                   design['state']['evidence']['evidence']), format='xml')
    assert isomorphic(graph, Graph().parse(data=actual['candidate.owl'], format='xml'))


def test_read_only_history_never_invokes_model_or_migrates(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    def forbidden():
        pytest.fail('Read-only access attempted model/configuration resolution')
    reader = service_api().DesignService(h.root, route_resolver=forbidden, read_only=True)
    before, files = db_rows(h.root), blob_bytes(h.root)
    assert reader.get(state['session_id']) == state
    for row in reader.list_revisions(state['session_id']):
        record = reader.get_revision(state['session_id'], row['revision'])
        assert json.loads(reader.export_revision(state['session_id'], row['revision'], format='json')) == record
    raw = reader.export_revision(state['session_id'], 3, format='owl')
    assert list(Graph().parse(data=raw, format='xml').subjects(RDF.type, OWL.Class))
    assert db_rows(h.root) == before and blob_bytes(h.root) == files and len(h.runtime.calls) == 2
    with pytest.raises(ValueError):
        reader.edit_classes(state['session_id'], request_id='forbidden', expected_revision=state['revision'])
    assert db_rows(h.root) == before


def test_edit_after_candidate_keeps_old_version_but_clears_current_output(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    assets = AssetStore(h.root)
    original = assets.read_payload(**state['output_asset_ref'])
    original_json = assets_api().DesignAssets(h.root, read_only=True).export_json(**state['output_asset_ref'])
    history = history_bytes(h, state)
    edited = h.service.edit_classes(state['session_id'], request_id='edit', expected_revision=state['revision'])
    assert edited['stage'] == 'classes' and edited['output_asset_ref'] is None
    assert edited['schema_confirmation'] is None and edited['class_confirmation'] is None
    assert assets.read_payload(**state['output_asset_ref']) == original
    assert all(history_bytes(h, edited)[n] == raw for n, raw in history.items())
    # Build a second immutable version within the same session/asset lineage.
    h.runtime.responses.append(reply(proposals()[0]))
    h.message(edited)
    h.run()
    current = h.get(edited)
    confirmed = h.service.confirm_classes(current['session_id'], request_id='confirm-again',
        expected_revision=current['revision'], snapshot_sha256=current['snapshot_sha256'])
    h.runtime.responses.append(reply(proposals()[1]))
    h.message(confirmed)
    h.run()
    current = h.get(confirmed)
    h.service.confirm_schema(current['session_id'], request_id='extract-again',
        expected_revision=current['revision'], snapshot_sha256=current['snapshot_sha256'])
    h.run()
    new_ref = h.get(current)['output_asset_ref']
    assert new_ref['ontology_id'] == state['output_asset_ref']['ontology_id']
    assert new_ref['version_id'] != state['output_asset_ref']['version_id']
    assert assets.get_version(**new_ref)['parent_version_id'] == state['output_asset_ref']['version_id']
    assert len(assets.list_versions(new_ref['ontology_id'])) == 2
    assert assets_api().DesignAssets(h.root, read_only=True).export_json(**state['output_asset_ref']) == original_json
    assert assets.read_payload(**state['output_asset_ref']) == original


def test_generic_design_cannot_be_submitted_through_old_build_completion_contract():
    # Independent baseline exercises the real old validator, even during RED.
    evidence, schema = bundle(), ur_schema()
    result = api('abox').extract(confirmation(schema, evidence), evidence)
    raw = api('export').abox_owl(result['result'], schema, evidence['evidence'])
    report = {'status': 'built', 'execution_readiness': 'not_verified', 'task_readiness': 'not_assessed',
              'issues': [], 'validation': {'ontology_sha256': sha(raw)}}
    with pytest.raises(ValueError):
        build_contracts.completion(report, raw, {'sources': []})


@pytest.mark.parametrize('change', ['payload', 'metadata', 'report'])
def test_design_reader_rejects_corrupt_candidate_or_cross_revision_artifacts(tmp_path, change):
    h = Harness(tmp_path / 'store')
    state = h.candidate()
    ref = state['output_asset_ref']
    assets = AssetStore(h.root)
    version = assets.get_version(**ref)
    if change == 'payload':
        (h.root / version['path']).write_bytes(b'<tampered/>')
    elif change == 'report':
        item = next(a for a in RunStore(h.root).artifacts(state['run_id']) if a['name'] == 'design.json')
        document = json.loads((h.root / item['path']).read_bytes())
        document['coverage'] = {'complete': True}
        (h.root / item['path']).write_text(json.dumps(document))
    else:
        metadata = deepcopy(version['metadata'])
        metadata['design_revision'] += 1
        with assets.storage.transaction(write=True) as conn:
            conn.execute('UPDATE versions SET metadata=? WHERE version_id=?', (json.dumps(metadata), ref['version_id']))
    reader = assets_api().DesignAssets(h.root, read_only=True)
    with pytest.raises(ValueError):
        reader.detail(**ref)
    assert reader.compatibility(**ref)['status'] == 'invalid'


def test_partial_extraction_report_survives_persistence_without_invented_assertions(tmp_path):
    h = Harness(tmp_path / 'store')
    state = h.classes()
    state = h.service.confirm_classes(state['session_id'], request_id='classes',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    proposal = proposals()[1]
    proposal['object_properties'] = [r for r in proposal['object_properties'] if r['id'] != 'hasBinding']
    h.runtime.responses.append(reply(proposal))
    h.message(state)
    h.run()
    state = h.get(state)
    h.service.confirm_schema(state['session_id'], request_id='schema',
        expected_revision=state['revision'], snapshot_sha256=state['snapshot_sha256'])
    assert h.run()['status'] == 'completed'
    saved = h.get(state)
    reader = assets_api().DesignAssets(h.root, read_only=True)
    detail = reader.detail(**saved['output_asset_ref'])
    assert any(r['term_id'] == 'hasBinding' for r in detail['report']['missing_terms'])
    assert detail['report']['skipped_assertions'] and detail['abox']['clarifications']
    document = json.loads(reader.export_json(**saved['output_asset_ref']))
    assert document['coverage'] == detail['report'] and document['abox'] == detail['abox']
    assert all(r['property'] != 'hasBinding' for r in detail['abox']['object_assertions'])
    assert len(h.runtime.calls) == 2

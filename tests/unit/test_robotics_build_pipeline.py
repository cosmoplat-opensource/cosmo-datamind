"""T02 RED: real XML extraction, candidate semantics and located failures — DR-060 / DR-065."""
from copy import deepcopy
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from rdflib import RDF

from ontology_assets import AssetStore
from tests.build_pipeline_support import (bad_xml, begin, blob_bytes, check_complete, compute,
                                          db_rows, setup, sha)
from tests.robotics_generation_support import task
from tests.robotics_support import MODELS, SOURCE, S, api, signature, trees
from tests.storage_pipeline_support import generate, pipeline

BAD_CASES = ['malformed_tree', 'malformed_models', 'dtd', 'utf16_entity', 'unknown_node',
             'model_conflict', 'duplicate_port', 'unknown_port', 'missing_port', 'template']


def test_compute_uses_real_sources_and_kernel_without_persisting_or_publishing(tmp_path, monkeypatch):
    call = compute()
    root, _, runs, sources, run = begin(tmp_path)
    observed = []
    original = api('importer').build_graph

    def observe(source, models, *, profile):
        observed.append((Path(source).read_bytes(), Path(models).read_bytes(), profile))
        return original(source, models, profile=profile)

    def forbidden(*args, **kwargs):
        pytest.fail('Build invoked demo evidence, generation, or formal asset publication')

    monkeypatch.setattr(api('importer'), 'build_graph', observe)
    monkeypatch.setattr(api('demo'), 'apply_demo_supplement', forbidden)
    monkeypatch.setattr(api('generation'), 'generate_task', forbidden)
    monkeypatch.setattr(AssetStore, 'publish_version', forbidden)
    before, files = db_rows(root), blob_bytes(root)
    product = call(root, run['run_id'])
    assert set(product) == {'result', 'ontology'}
    assert observed == [(SOURCE.read_bytes(), MODELS.read_bytes(), 'ur_pick_place')]
    assert db_rows(root) == before and blob_bytes(root) == files, 'Compute child must not commit or leave temp output'
    assert product['result']['status'] == 'built' and product['result']['issues'] == []
    assert product['result']['execution_readiness'] == 'not_verified'
    assert product['result']['task_readiness'] == 'not_assessed'
    validation = product['result']['validation']
    assert validation['source_sha256'] == sources[0]['sha256']
    assert validation['models_sha256'] == sources[1]['sha256']
    assert validation['ontology_sha256'] == sha(product['ontology'])
    assert validation['shacl_conforms'] is True
    assert validation['ontology_counts']['AtomicSkill'] > 0
    assert validation['ontology_counts']['CompositeSkill'] == 3
    assert str(root) not in json.dumps(product['result'], ensure_ascii=False)
    owl = tmp_path / 'download.owl'
    owl.write_bytes(product['ontology'])
    context = api('consumer').load_ontology(owl, mode='real')
    graph = context['graph']
    assert trees(api('semantics').xml_from_graph(graph)) == trees(ET.parse(SOURCE).getroot())
    # Check source models independently of build output/validation booleans.
    for node in ET.parse(MODELS).getroot().findall('TreeNodesModel/*'):
        impl = S[node.get('ID') + '_BT_Impl']
        stored_model = graph.value(impl, S.sourceModelXML)
        assert stored_model is not None
        assert signature(ET.fromstring(str(stored_model))) == signature(node)
        ports = {str(graph.value(p, S.parameterName)): p for p in graph.objects(impl, S.hasPortContract)}
        assert set(ports) == {p.get('name') for p in node}
        for port in node:
            contract = ports[port.get('name')]
            assert str(graph.value(contract, S.cppType)) == port.get('type')
            assert str(graph.value(contract, S.direction)) == port.tag.removesuffix('_port')
            assert str(graph.value(contract, S.defaultPresent)) == str('default' in port.attrib).lower()
            if 'default' in port.attrib:
                assert str(graph.value(contract, S.declaredDefault)) == port.get('default')
    assert not any(str(v) == 'simulated' for v in graph.objects(None, S.confirmationStatus))
    assert len(set(graph.subjects(RDF.type, S.SkillInvocation))) == 25
    assert runs.get_run(run['run_id']) == run


@pytest.mark.parametrize('case', BAD_CASES)
def test_invalid_sources_return_located_report_without_candidate(tmp_path, case):
    call = compute()
    source, models, role, code, location = bad_xml(case)
    root, assets, runs, sources, run = begin(tmp_path, source=source, models=models)
    before = db_rows(root)
    product = call(root, run['run_id'])
    assert product['ontology'] is None
    assert product['result']['status'] == 'error'
    assert product['result']['execution_readiness'] == 'not_verified'
    problems = [p for p in product['result']['issues'] if p['code'] == code and p['source_role'] == role]
    assert problems, product
    issue = problems[0]
    assert issue['source_asset_id'] == next(s['source_asset_id'] for s in sources if s['role'] == role)
    assert isinstance(issue['message'], str) and issue['message']
    assert isinstance(issue['location'], dict)
    for key, expected in location.items():
        assert issue['location'][key] == expected
    if code == 'invalid_xml':
        assert type(issue['location']['column']) is int and issue['location']['column'] >= 0
    if case == 'model_conflict':
        assert any(p['source_role'] == 'behavior_tree' for p in product['result']['issues']), 'Conflict must identify both declarations'
    assert str(root) not in json.dumps(product['result'], ensure_ascii=False)
    assert db_rows(root) == before
    saved = runs.finish_build(run['run_id'], **product)
    assert saved['status'] == 'failed' and saved['output_asset_ref'] is None
    assert assets.list_assets() == []
    assert sorted(a['kind'] for a in runs.artifacts(run['run_id'])) == ['manifest', 'validation']
    assert runs.manifest(run['run_id'])['result'] == product['result']


@pytest.mark.parametrize('case', ['malformed_tree', 'model_conflict', 'unknown_port'])
def test_importer_errors_are_structured_and_remain_value_error_compatible(tmp_path, case):
    source, models, role, code, expected = bad_xml(case)
    source_path, model_path = tmp_path / 'source.xml', tmp_path / 'models.xml'
    source_path.write_bytes(source)
    model_path.write_bytes(models)
    with pytest.raises(ValueError) as caught:
        api('importer').build_graph(source_path, model_path, profile='ur_pick_place')
    issues = getattr(caught.value, 'issues', None)
    assert isinstance(issues, list) and issues, 'Plain error strings cannot carry stable UI locations'
    issue = next(p for p in issues if p['code'] == code and p['source_role'] == role)
    assert all(issue['location'][k] == v for k, v in expected.items())


def test_compute_reports_corrupted_source_without_mutating_storage(tmp_path):
    call = compute()
    root, _, _, sources, run = begin(tmp_path)
    (root / sources[1]['path']).write_bytes(b'corrupted snapshot')
    before, files = db_rows(root), blob_bytes(root)
    product = call(root, run['run_id'])
    assert product['ontology'] is None and product['result']['status'] == 'error'
    assert any(p['code'] == 'source_integrity' and p['source_asset_id'] == sources[1]['source_asset_id']
               for p in product['result']['issues'])
    assert db_rows(root) == before and blob_bytes(root) == files


def test_unexpected_kernel_error_is_sanitized_and_cannot_become_success(tmp_path, monkeypatch):
    call = compute()
    root, _, _, _, run = begin(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError(str(root / 'internal/private.xml') + ': implementation detail')

    monkeypatch.setattr(api('importer'), 'build_graph', broken)
    before = db_rows(root)
    product = call(root, run['run_id'])
    assert product['ontology'] is None and product['result']['status'] == 'error'
    assert any(p['code'] == 'build_error' for p in product['result']['issues'])
    assert str(root) not in json.dumps(product['result'])
    assert db_rows(root) == before


def test_source_roles_override_input_order_and_same_names_do_not_replace_snapshots(tmp_path):
    call = compute()
    root, assets, runs, sources, args = setup(tmp_path)
    original_bytes = [assets.read_source(s['source_asset_id']) for s in sources]
    args['source_asset_ids'].reverse()
    first = runs.create_build_run(**args)
    runs.start(first['run_id'])
    one = runs.finish_build(first['run_id'], **call(root, first['run_id']))
    before = deepcopy(one)
    # A new upload with the same display name is another run/source snapshot.
    from tests.build_pipeline_support import inputs
    new_sources = inputs(assets)
    args['source_asset_ids'] = [s['source_asset_id'] for s in new_sources]
    second = runs.create_build_run(**args)
    runs.start(second['run_id'])
    two = runs.finish_build(second['run_id'], **call(root, second['run_id']))
    assert one['output_asset_ref'] != two['output_asset_ref']
    assert runs.get_run(first['run_id']) == before
    assert original_bytes == [assets.read_source(s['source_asset_id']) for s in sources]
    assert {s['source_asset_id'] for s in sources}.isdisjoint(s['source_asset_id'] for s in new_sources)
    for run, expected in [(one, sources), (two, new_sources)]:
        validation = run['result']['validation']
        assert validation['source_sha256'] == expected[0]['sha256']
        assert validation['models_sha256'] == expected[1]['sha256']


def test_candidate_requires_explicit_publication_and_real_task_still_lacks_evidence(tmp_path):
    call = compute()
    root, assets, runs, _, before = begin(tmp_path)
    saved = runs.finish_build(before['run_id'], **call(root, before['run_id']))
    version = check_complete(root, before, saved)
    ref = saved['output_asset_ref']
    original_run = deepcopy(saved)
    count = len(runs.list_runs())
    with pytest.raises(ValueError):
        generate(pipeline(), root, ref, version, task(), mode='real')
    assert len(runs.list_runs()) == count
    # Explicitly exercise existing publication API; build service must not do this itself.
    published = assets.publish_version(**ref, mode='real')
    generated = generate(pipeline(), root, ref, published, task(), mode='real')
    assert generated['result']['status'] == 'needs_information'
    assert generated['result']['candidate_written'] is False
    assert not any(a['kind'] == 'candidate' for a in runs.artifacts(generated['run_id']))
    assert generated['ontology_snapshot']['payload_sha256'] == version['payload_sha256']
    assert runs.get_run(saved['run_id']) == original_run
    assert runs.manifest(saved['run_id'])['output_asset_ref'] == ref


def test_compute_rejects_unstarted_run_without_side_effects(tmp_path):
    call = compute()
    root, _, runs, _, args = setup(tmp_path)
    run = runs.create_build_run(**args)
    before = db_rows(root)
    with pytest.raises(ValueError):
        call(root, run['run_id'])
    assert db_rows(root) == before

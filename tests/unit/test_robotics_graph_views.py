"""Read-only instance / procedure projections — DR-068."""
import importlib

import pytest
from rdflib import Graph, Literal, OWL, RDF, RDFS

from domains.robotics.graph import S
from domains.robotics.semantics import xml_from_graph
from tests.robotics_governance_support import graph
from tests.robotics_task_support import fixture
from tests.unit.test_robotics_task_completion import ready_graph
from tests.build_runtime_support import BASE_URL, http_app
from tests.build_pipeline_support import db_rows


def api():
    return importlib.import_module('domains.robotics.views')


def test_relations_preserve_identity_labels_literal_properties_and_real_edges():
    g = ready_graph()
    g.add((S.Part_A_001, RDFS.label, Literal('<img src=x onerror=alert(1)>', lang='zh')))
    before = set(g)
    result = api().relations(g)
    ids = {n['id'] for n in result['nodes']}
    assert str(S.PartType_A) in ids and str(S.Part_A_001) in ids
    assert str(S.PartType) not in ids
    assert len(ids) == len(result['nodes'])
    assert all((g.identifier is not None and e['source'] in ids and e['target'] in ids) for e in result['edges'])
    assert any(e['source'] == str(S.Part_A_001) and e['predicate'] == str(S.hasPartType)
               and e['target'] == str(S.PartType_A) for e in result['edges'])
    assert any('<img' in p['value'] for n in result['nodes'] for p in n['properties'])
    assert set(g) == before and result == api().relations(g)


def test_tree_projection_matches_all_stored_xml_nodes_order_and_parameters():
    g = graph()
    result = api().behavior_tree(g)
    root = xml_from_graph(g)
    assert result['main_tree'] == root.get('main_tree_to_execute')
    assert [t['name'] for t in result['trees']] == [t.get('ID') for t in root]
    seen = set()
    def compare(actual, expected):
        assert actual['id'] not in seen
        seen.add(actual['id'])
        assert actual['tag'] == expected.tag
        assert actual['attributes'] == expected.attrib
        assert len(actual['children']) == len(expected)
        for a, b in zip(actual['children'], expected): compare(a, b)
    for tree, xml in zip(result['trees'], root): compare(tree['root'], xml)
    assert len(seen) == sum(1 for tree in root for _ in tree.iter())
    assert 'TreeNodesModel' not in {n['name'] for n in result['trees']}


def test_empty_and_oversized_relations_are_explicit_not_truncated():
    assert api().relations(Graph()) == {'nodes': [], 'edges': []}
    g = Graph()
    for i in range(3): g.add((S[f'X{i}'], RDF.type, OWL.NamedIndividual))
    with pytest.raises(ValueError, match='规模'): api().relations(g, max_nodes=2)


def test_views_http_are_version_pinned_read_only_and_integrity_checked(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        prefix = f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}/views/"
        before = db_rows(root)
        for kind in ('relations', 'behavior-tree'):
            response = client.get(prefix + kind, base_url=BASE_URL)
            assert response.status_code == 200, response.get_data(as_text=True)
            body = response.get_json()
            assert body['version_id'] == version['version_id'] and body['version_number'] == 1
            assert body['ontology_id'] == version['ontology_id'] and body['name']
            assert str(root) not in response.get_data(as_text=True)
        assert db_rows(root) == before
        assert client.get(prefix + 'unknown', base_url=BASE_URL).status_code == 400
        assert client.get(prefix.replace(version['version_id'], 'missing') + 'relations', base_url=BASE_URL).status_code == 404
        (root / assets.get_version(version['ontology_id'], version['version_id'])['path']).write_bytes(b'damaged')
        assert client.get(prefix + 'relations', base_url=BASE_URL).status_code == 409


def test_views_http_reject_cross_asset_data_domain_and_incomplete_tree(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        prefix = f"/api/ontology-assets/{version['ontology_id']}/versions/"
        other = assets.create_asset(name='other', domain='robotics')
        assert client.get(f"/api/ontology-assets/{other['ontology_id']}/versions/{version['version_id']}/views/relations",
                          base_url=BASE_URL).status_code == 404
        data = assets.create_asset(name='data', domain='data')
        assert client.get(f"/api/ontology-assets/{data['ontology_id']}/versions/{version['version_id']}/views/relations",
                          base_url=BASE_URL).status_code == 400
        g = Graph().parse(data=assets.read_payload(version['ontology_id'], version['version_id']), format='xml')
        g.set((S.BehaviorTree_Source, S.rootAttributes, Literal('null')))
        broken = assets.add_version(version['ontology_id'], g.serialize(format='xml', encoding='utf-8'),
                                    payload_kind='robotics_rdf',
                                    source_asset_ids=version['source_asset_ids'], metadata=version['metadata'])
        response = client.get(prefix + broken['version_id'] + '/views/behavior-tree', base_url=BASE_URL)
        assert response.status_code == 422
        assert response.get_json()['error']['code'] == 'view_unavailable'
        assert '流程或参数绑定不完整' in response.get_json()['error']['message']

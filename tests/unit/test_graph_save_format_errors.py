"""RDF/XML limits explain save and export failures separately — DR-196."""
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_task_support import fixture

BAD = 'urn:example:7'
OPS = [dict(op='create', kind='data', name='编号', iri=BAD),
       dict(op='create', kind='individual', name='样例', iri='urn:example:sample'),
       dict(op='add_value', source='urn:example:sample', predicate=BAD, value='A-1', language='', datatype='')]


def test_unrepresentable_property_iri_has_save_and_export_specific_messages(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        oid, vid = version['ontology_id'], version['version_id']
        before = assets.list_versions(oid)
        url = f'/api/ontology-assets/{oid}/versions/{vid}/graph-edit-copy'
        body = dict(base_sha256=version['payload_sha256'], operations=OPS)

        saved = client.post(url + '/save', json=body, base_url=BASE_URL)
        assert saved.status_code == 422
        message = saved.json['error']['message']
        assert '保存' in message and BAD in message and '导出格式' not in message
        assert assets.list_versions(oid) == before

        exported = client.post(url, json={**body, 'format': 'owl'}, base_url=BASE_URL)
        assert exported.status_code == 422
        message = exported.json['error']['message']
        assert BAD in message and 'Turtle' in message and '保存' not in message

        for fmt in ('ttl', 'json', 'view'):
            assert client.post(url, json={**body, 'format': fmt}, base_url=BASE_URL).status_code == 200


def test_message_lists_a_bounded_number_of_iris():
    from rdflib import Graph, Literal, URIRef
    from ontology_assets.graph_edit_copy import format_failure, unrepresentable_predicates

    graph = Graph()
    bad = [f'urn:example:{index}' for index in range(10)] + ['http://example.org/a/', 'http://example.org/a#']
    for iri in bad:
        graph.add((URIRef('urn:example:s'), URIRef(iri), Literal('v')))
    graph.add((URIRef('urn:example:s'), URIRef('urn:example:ok'), Literal('v')))
    found = unrepresentable_predicates(graph)
    assert found == sorted(bad)
    for saving in (True, False):
        message = format_failure(found, saving=saving)
        assert all(iri in message for iri in found[:5]) and not any(iri in message for iri in found[5:])
        assert '等 12 个' in message

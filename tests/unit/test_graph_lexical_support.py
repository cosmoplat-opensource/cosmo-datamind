"""Edit copies depend on RDF/XML lexical preservation and fail closed without it — DR-197."""
import re
from pathlib import Path

import rdflib

from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_task_support import fixture

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_rdflib_range_contains_the_ci_pin():
    runtime = next(line for line in (ROOT / 'requirements.txt').read_text().splitlines() if line.startswith('rdflib'))
    pinned = re.search(r'^rdflib==([0-9.]+)', (ROOT / 'requirements-dev.txt').read_text(), re.M)[1]
    spec = runtime.split('#')[0].split('rdflib', 1)[1].replace(' ', '')
    assert spec == f'>={pinned},<{int(pinned.split(".")[0]) + 1}'
    assert rdflib.__version__.split('.')[0] == pinned.split('.')[0]


def test_edit_copy_fails_closed_when_lexical_forms_are_not_preserved(tmp_path, monkeypatch):
    from rdflib.plugins.parsers.rdfxml import RDFXMLHandler
    from ontology_assets import graph_edit_copy

    with http_app(tmp_path) as (_, client, _, root):
        assets, version, _ = fixture(root)
        oid, vid = version['ontology_id'], version['version_id']
        url = f'/api/ontology-assets/{oid}/versions/{vid}/graph-edit-copy'
        body = dict(base_sha256=version['payload_sha256'], operations=[], format='view')
        before = assets.list_versions(oid)

        monkeypatch.setattr(graph_edit_copy, '_lexical_supported', None)
        monkeypatch.setattr(graph_edit_copy.LexicalHandler, 'property_element_end',
                            RDFXMLHandler.property_element_end)
        for path, payload in ((url, body), (url + '/save', dict(base_sha256=body['base_sha256'], operations=[
                dict(op='create', kind='class', name='新增类别', iri='urn:example:added')]))):
            response = client.post(path, json=payload, base_url=BASE_URL)
            assert response.status_code == 503
            assert response.json['error']['code'] == 'graph_edit_unavailable'
            assert '原文' in response.json['error']['message']
        assert assets.list_versions(oid) == before
        view = client.get(f'/api/ontology-assets/{oid}/versions/{vid}/views/interactive-ontology-graph',
                          base_url=BASE_URL)
        assert view.status_code == 200

        monkeypatch.undo()
        monkeypatch.setattr(graph_edit_copy, '_lexical_supported', None)
        assert client.post(url, json=body, base_url=BASE_URL).status_code == 200

"""Object property endpoint selection and write validation — DR-190."""
import pytest
from rdflib import BNode, Graph, Namespace, OWL, RDF, RDFS
from rdflib.collection import Collection

from domains.robotics.graph import S
from ontology_assets.graph_edit_copy import apply_edit
from tests.build_runtime_support import BASE_URL, http_app
from tests.robotics_task_support import fixture

N = Namespace('urn:example:')


def sample():
    graph = Graph()
    for cls in (S.Workstation, S.Carrier, N.Cell, N.Substation, N.Place, N.Mobile):
        graph.add((cls, RDF.type, OWL.Class))
    for individual, cls in [(N.a, S.Workstation), (N.b, S.Carrier), (N.child, N.Substation),
                            (N.alias, N.Cell), (N.empty, OWL.NamedIndividual)]:
        graph.add((individual, RDF.type, cls))
    graph.add((N.Substation, RDFS.subClassOf, N.Place))
    graph.add((N.Place, OWL.equivalentClass, S.Workstation))
    graph.add((S.hasCarrier, RDF.type, OWL.ObjectProperty))
    graph.add((S.hasCarrier, RDFS.domain, S.Workstation))
    graph.add((S.hasCarrier, RDFS.range, S.Carrier))
    return graph


def link(source, target, verb='link', *, predicate=S.hasCarrier):
    return dict(op=verb, source=str(source), predicate=str(predicate), target=str(target))


def test_new_relation_validates_both_endpoints_but_legacy_relation_can_be_unlinked():
    graph = sample()
    for source, target, reason in [(N.b, N.a, '起点'), (N.a, N.a, '终点'), (N.empty, N.b, '起点')]:
        before = set(graph)
        with pytest.raises(ValueError, match=reason):
            apply_edit(graph, link(source, target))
        assert set(graph) == before
    apply_edit(graph, link(N.child, N.b))
    graph.add((N.b, S.hasCarrier, N.a))
    apply_edit(graph, link(N.b, N.a, 'unlink'))
    assert (N.child, S.hasCarrier, N.b) in graph


def test_confirmed_alias_union_and_multiple_declarations_use_current_copy():
    graph = sample()
    roles = dict(version='1', confirmed=True, bindings=[dict(kind='classes', iri=str(N.Cell), role='Workstation')])
    with pytest.raises(ValueError, match='起点'):
        apply_edit(graph, link(N.alias, N.b), role_map={**roles, 'confirmed': False})
    apply_edit(graph, link(N.alias, N.b), role_map=roles)
    union, head = BNode(), BNode()
    Collection(graph, head, [N.Mobile, S.Carrier])
    graph.add((union, OWL.unionOf, head))
    graph.add((S.hasCarrier, RDFS.domain, union))
    with pytest.raises(ValueError, match='起点'):
        apply_edit(graph, link(N.a, N.b))
    graph.add((N.a, RDF.type, N.Mobile))
    apply_edit(graph, link(N.a, N.b))
    graph.remove((S.hasCarrier, RDFS.domain, None))
    apply_edit(graph, link(N.empty, N.b))
    graph.set((S.hasCarrier, RDFS.range, OWL.Thing))
    apply_edit(graph, link(N.empty, N.empty))
    graph.set((S.hasCarrier, RDFS.range, BNode()))
    with pytest.raises(ValueError, match='终点'):
        apply_edit(graph, link(N.empty, N.a))


def test_edit_api_checks_selected_property_endpoints_on_view_and_export(tmp_path):
    with http_app(tmp_path) as (_, client, _, root):
        _, version, _ = fixture(root)
        path = f"/api/ontology-assets/{version['ontology_id']}/versions/{version['version_id']}/graph-edit-copy"
        creation = [dict(op='create', kind='individual', iri=str(iri), name='实例', parent=str(cls))
                    for iri, cls in [(N.a, S.Workstation), (N.b, S.Carrier)]]
        creation.append(dict(op='create', kind='object', iri=str(N.relatedTo), name='关联',
                             domain=str(S.Carrier), range=str(S.Workstation)))

        def submit(fmt, links):
            payload = dict(base_sha256=version['payload_sha256'], format=fmt, operations=creation + links)
            return client.post(path, json=payload, base_url=BASE_URL)

        valid = [(N.a, S.hasCarrier, N.b), (N.b, N.relatedTo, N.a)]
        for source, predicate, target in valid:
            for fmt in ('view', 'owl'):
                for start, end, reason in [(target, source, '起点'), (source, source, '终点')]:
                    result = submit(fmt, [link(start, end, predicate=predicate)])
                    assert result.status_code == 422
                    assert reason in result.json['error']['message']
        result = submit('owl', [link(start, end, predicate=prop) for start, prop, end in valid])
        assert result.status_code == 200
        exported = Graph().parse(data=result.json['content'], format='xml')
        assert all(triple in exported for triple in valid)


def test_editor_and_server_agree_on_projected_class_constraints():
    import json
    from pathlib import Path
    import shutil
    import subprocess

    import playwright
    from ontology_assets.interactive_ontology_graph_view import _project

    graph = sample()
    graph.add((S.Workstation, RDFS.subClassOf, N.Substation))  # cyclic class hierarchy
    graph.add((N.alias, RDF.type, N.Mobile))
    graph.add((N.child, RDF.type, N.Mobile))
    graph.add((S.hasCarrier, RDFS.domain, N.Mobile))
    group, head = BNode(), BNode()
    Collection(graph, head, [N.Mobile, S.Carrier])
    graph.add((group, OWL.unionOf, head))
    graph.set((S.hasCarrier, RDFS.range, group))
    graph.add((N.free, RDF.type, OWL.ObjectProperty))
    graph.add((N.unknown, RDF.type, OWL.ObjectProperty))
    graph.add((N.unknown, RDFS.domain, BNode()))
    graph.add((N.unknown, RDFS.range, OWL.Thing))
    roles = dict(version='1', confirmed=True, bindings=[dict(kind='classes', iri=str(N.Cell), role='Workstation')])
    view = _project(graph, {'kind': 'edit-copy'}, '', role_map=roles)
    node = shutil.which('node') or str(Path(playwright.__file__).parent / 'driver/node')
    program = """
        import {readFileSync} from 'node:fs';
        import {eligibleIndividuals} from './ui/modules/interactive-ontology-graph/relation-endpoints.js';
        const model = JSON.parse(readFileSync(0, 'utf8'));
        const ids = constraints => eligibleIndividuals(model, constraints).map(i => i.id);
        console.log(JSON.stringify(model.properties.map(p => [p.id, ids(p.domains), ids(p.ranges)])));
    """
    output = subprocess.run([node, '--input-type=module', '-e', program], cwd=Path(__file__).resolve().parents[2],
                            input=json.dumps(view), capture_output=True, text=True, check=True, timeout=10)
    candidates = json.loads(output.stdout)
    people = {n['id'] for n in view['individuals']}
    expected = {
        str(S.hasCarrier): ({str(N.alias), str(N.child)}, {str(N.alias), str(N.child), str(N.b)}),
        str(N.free): (people, people), str(N.unknown): (set(), people)
    }
    for prop, starts, ends in candidates:
        assert (set(starts), set(ends)) == expected[prop]
        for source in people:
            for target in people:
                op = dict(op='link', predicate=prop, source=source, target=target)
                if source in starts and target in ends:
                    apply_edit(graph, op, role_map=roles)
                    apply_edit(graph, {**op, 'op': 'unlink'}, role_map=roles)
                else:
                    with pytest.raises(ValueError, match='起点|终点'):
                        apply_edit(graph, op, role_map=roles)

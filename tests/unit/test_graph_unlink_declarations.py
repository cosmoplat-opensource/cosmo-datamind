"""Removing one assertion adds a property declaration only when it was the last use — DR-195."""
import pytest
from rdflib import Graph, Literal, Namespace, OWL, RDF

from ontology_assets.graph_edit_copy import apply_edit

N = Namespace('urn:example:')


def sample():
    graph = Graph()
    for person in (N.a, N.b, N.c):
        graph.add((person, RDF.type, OWL.NamedIndividual))
    # Undeclared predicates: their identity exists only through these uses.
    graph.add((N.a, N.link, N.b))
    graph.add((N.a, N.link, N.c))
    graph.add((N.a, N.note, Literal('一')))
    graph.add((N.b, N.note, Literal('二')))
    return graph


@pytest.mark.parametrize('first,last,predicate,declaration', [
    (dict(op='unlink', target=str(N.b)), dict(op='unlink', target=str(N.c)), N.link, OWL.ObjectProperty),
    (dict(op='remove_value', value='一'), dict(op='remove_value', value='二', source=str(N.b)), N.note,
     OWL.DatatypeProperty),
])
def test_declaration_is_added_only_after_last_use(first, last, predicate, declaration):
    graph = sample()
    base = dict(source=str(N.a), predicate=str(predicate))
    if first['op'] == 'remove_value':
        base.update(language='', datatype='')
    impact = apply_edit(graph, {**base, **first})
    assert (impact['removed'], impact['added']) == (1, 0)
    assert (predicate, RDF.type, declaration) not in graph
    impact = apply_edit(graph, {**base, **last})
    assert (impact['removed'], impact['added']) == (1, 1)
    assert (predicate, RDF.type, declaration) in graph
    assert not list(graph.subject_objects(predicate))


def test_declared_predicate_is_never_redeclared():
    graph = sample()
    graph.add((N.link, RDF.type, OWL.ObjectProperty))
    for target in (N.b, N.c):
        impact = apply_edit(graph, dict(op='unlink', source=str(N.a), predicate=str(N.link), target=str(target)))
        assert impact['added'] == 0
    assert set(graph.objects(N.link, RDF.type)) == {OWL.ObjectProperty}

"""Editing an entity keeps a Chinese name, as creating one does — DR-198."""
import pytest
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS

from ontology_assets.graph_edit_copy import apply_edit

N = Namespace('urn:example:')


def sample():
    graph = Graph()
    graph.add((N.Cell, RDF.type, OWL.Class))
    graph.add((N.Cell, RDFS.label, Literal('工位', lang='zh')))
    graph.add((N.Cell, RDFS.comment, Literal('说明', lang='zh')))
    return graph


@pytest.mark.parametrize('name', ['', '   '])
def test_blank_name_is_rejected_and_copy_is_unchanged(name):
    graph = sample()
    before = set(graph)
    for new_iri in (str(N.Cell), str(N.Renamed)):
        with pytest.raises(ValueError, match='名称'):
            apply_edit(graph, dict(op='edit', iri=str(N.Cell), new_iri=new_iri, name=name, comment='说明'))
        assert set(graph) == before


def test_comment_may_still_be_cleared_with_a_name():
    graph = sample()
    apply_edit(graph, dict(op='edit', iri=str(N.Cell), new_iri=str(N.Cell), name='工位', comment=''))
    assert (N.Cell, RDFS.label, Literal('工位', lang='zh')) in graph
    assert not list(graph.objects(N.Cell, RDFS.comment))

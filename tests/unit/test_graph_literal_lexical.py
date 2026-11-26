"""Date and dateTime values follow XSD lexical forms, independent of Python version — DR-193."""
import pytest
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, XSD

from ontology_assets.graph_edit_copy import apply_edit, validate_literal

N = Namespace('urn:example:')

VALID = {
    XSD.date: ['2026-11-26', '2026-11-26Z', '2026-11-26+08:00', '2026-11-26-14:00', '2024-02-29', '0001-01-01'],
    XSD.dateTime: ['2026-11-26T10:00:00', '2026-11-26T10:00:00Z', '2026-11-26T10:00:00.5+08:00',
                   '2026-11-26T23:59:59.1234567', '2026-11-26T00:00:00-14:00', '2000-02-29T12:30:00+14:00'],
}
INVALID = {
    XSD.date: ['20261126', '2026-W48-4', '2026-11-26T10:00:00', '2026-2-3', '2026-13-01', '2026-11-31',
               '2023-02-29', '2026-11-26+8:00', '2026-11-26+14:30', '2026-11-26 ', '0000-01-01', '12026-01-01',
               '-2026-01-01', '٢٠٢٦-١١-٢٦'],
    XSD.dateTime: ['2026-11-26T10', '2026-11-26T10:00', '2026-11-26 10:00:00', '2026-11-26T10:00:00+08:00Z',
                   '20261126T100000', '2026-11-26T24:00:00', '2026-11-26T10:60:00', '2026-11-26T10:00:60',
                   '2026-11-26T10:00:00.', '2026-11-26T10:00:00,5', '2026-11-26T10:00:00+0800',
                   '2026-11-26T10:00:00z', '2026-11-26', '2026-11-26T10:00:00+15:00'],
}


@pytest.mark.parametrize('datatype,value', [(t, v) for t, values in VALID.items() for v in values])
def test_valid_xsd_lexical_forms_are_accepted(datatype, value):
    validate_literal(value, str(datatype))


@pytest.mark.parametrize('datatype,value', [(t, v) for t, values in INVALID.items() for v in values])
def test_invalid_lexical_forms_are_rejected_with_format_hint(datatype, value):
    with pytest.raises(ValueError, match='2026-11-26'):
        validate_literal(value, str(datatype))


def test_rejected_date_value_is_not_written_to_the_copy():
    graph = Graph()
    graph.add((N.Cell, RDF.type, OWL.Class))
    graph.add((N.a, RDF.type, N.Cell))
    graph.add((N.at, RDF.type, OWL.DatatypeProperty))
    graph.add((N.at, RDFS.range, XSD.dateTime))
    before = set(graph)
    op = dict(op='add_value', source=str(N.a), predicate=str(N.at), language='', datatype=str(XSD.dateTime))
    with pytest.raises(ValueError, match='日期时间'):
        apply_edit(graph, {**op, 'value': '2026-11-26T10'})
    assert set(graph) == before
    apply_edit(graph, {**op, 'value': '2026-11-26T10:00:00+08:00'})
    assert (N.a, N.at, Literal('2026-11-26T10:00:00+08:00', datatype=XSD.dateTime, normalize=False)) in graph

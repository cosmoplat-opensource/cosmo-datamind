"""Selected schema and checked instance RDF/XML, adapted from Yang — DR-070."""
from rdflib import BNode, Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
from rdflib.collection import Collection

from .common import checked_tbox
from ._schema import validate_result


def _render(tbox, result=None):
    graph, ns = Graph(), Namespace(tbox['namespace'])
    instances = Namespace(tbox['namespace'] + 'individual/')
    for prefix, namespace in [('tbox', ns), ('abox', instances), ('owl', OWL), ('rdfs', RDFS), ('xsd', XSD)]:
        graph.bind(prefix, namespace)
    graph.add((URIRef(tbox['namespace'].rstrip('#/')), RDF.type, OWL.Ontology))
    for row in tbox['classes']:
        subject = ns[row['id']]
        graph.add((subject, RDF.type, OWL.Class))
        graph.add((subject, RDFS.label, Literal(row['label'], lang='zh')))
        graph.add((subject, RDFS.comment, Literal(row['definition'], lang='zh')))
        for parent in row['parent_ids']:
            graph.add((subject, RDFS.subClassOf, ns[parent]))

    def expression(identifiers):
        members = [OWL.Thing if identifier == 'owl:Thing' else ns[identifier] for identifier in identifiers]
        if len(members) == 1:
            return members[0]
        union, head = BNode(), BNode()
        graph.add((union, RDF.type, OWL.Class))
        graph.add((union, OWL.unionOf, head))
        Collection(graph, head, members)
        return union

    for key, kind in [('object_properties', OWL.ObjectProperty), ('data_properties', OWL.DatatypeProperty)]:
        for row in tbox[key]:
            subject = ns[row['id']]
            graph.add((subject, RDF.type, kind))
            graph.add((subject, RDFS.label, Literal(row['label'], lang='zh')))
            graph.add((subject, RDFS.comment, Literal(row['definition'], lang='zh')))
            if row['domain_ids']:
                graph.add((subject, RDFS.domain, expression(row['domain_ids'])))
            graph.add((subject, RDFS.range, expression(row['range_ids']) if key == 'object_properties'
                       else XSD[row['datatype'].removeprefix('xsd:')]))
    if result is not None:
        for row in result['individuals']:
            subject = instances[row['id']]
            graph.add((subject, RDF.type, OWL.NamedIndividual))
            graph.add((subject, RDFS.label, Literal(row['label'], lang='zh')))
            for cls in row['class_ids']:
                graph.add((subject, RDF.type, ns[cls]))
        for row in result['object_assertions']:
            graph.add((instances[row['subject']], ns[row['property']], instances[row['object']]))
        datatypes = {row['id']: XSD[row['datatype'].removeprefix('xsd:')] for row in tbox['data_properties']}
        for row in result['data_assertions']:
            graph.add((instances[row['subject']], ns[row['property']],
                       Literal(row['value'], datatype=datatypes[row['property']])))
    return graph.serialize(format='xml', encoding='utf-8')


def tbox_owl(result_or_schema, namespace):
    schema = {'namespace': namespace, **{k: result_or_schema.get(k, []) for k in
                                        ('classes', 'object_properties', 'data_properties')}}
    return _render(checked_tbox(schema))


def abox_owl(result, tbox, evidence_rows):
    schema = checked_tbox(tbox)
    # DR-070 §9/12: supplied evidence authorizes references, never the result itself.
    checked = validate_result(result, schema, evidence_rows, set())
    return _render(schema, checked)

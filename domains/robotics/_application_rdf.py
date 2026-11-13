"""Bounded self-contained RDF decoding for application artifacts — DR-072."""
from xml.sax import SAXException
from rdflib import Graph, OWL
from rdflib.exceptions import Error as RDFError


def parse_rdf(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=32*1024*1024:raise ValueError('RDF snapshot size')
    try:
        text=raw.decode('utf-8-sig')
        if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():raise ValueError('external declaration')
        graph=Graph().parse(data=text,format='xml')
        if list(graph.triples((None,OWL.imports,None))):raise ValueError('external import')
        return graph
    except (UnicodeError,SAXException,RDFError) as exc:
        raise ValueError('Invalid self-contained RDF snapshot') from exc

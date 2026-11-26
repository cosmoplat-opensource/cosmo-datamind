"""Datatype endpoints used by graph edit forms and service validation — DR-190."""
from rdflib import RDF, RDFS, XSD, URIRef

# XSD also defines facets (e.g. minLength), which are not datatypes.
XSD_TYPES = frozenset('''string boolean decimal float double duration dateTime time date gYearMonth
    gYear gMonthDay gDay gMonth hexBinary base64Binary anyURI QName NOTATION normalizedString
    token language Name NCName ID IDREF IDREFS ENTITY ENTITIES NMTOKEN NMTOKENS integer
    nonPositiveInteger negativeInteger long int short byte nonNegativeInteger unsignedLong
    unsignedInt unsignedShort unsignedByte positiveInteger yearMonthDuration dayTimeDuration
    dateTimeStamp anySimpleType anyAtomicType'''.split())
VALUE_TYPES = (XSD.string, XSD.integer, XSD.decimal, XSD.boolean, XSD.date, XSD.dateTime, RDF.langString)


def is_data_range(graph, term):
    if not isinstance(term, URIRef):
        return False
    if str(term).startswith(str(XSD)):
        return str(term)[len(str(XSD)):] in XSD_TYPES
    return term in (RDFS.Literal, RDF.langString, RDF.XMLLiteral, RDF.HTML) or (term, RDF.type, RDFS.Datatype) in graph


def value_types(ranges):
    """Literal kinds the basic editor can write within every declared range."""
    required = set(ranges) - {RDFS.Literal}
    return [kind for kind in VALUE_TYPES if required <= ({kind, XSD.decimal} if kind == XSD.integer else {kind})]

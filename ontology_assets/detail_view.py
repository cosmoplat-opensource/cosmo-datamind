"""Read-only display projection of the verified current fixed RDF payload — DR-073 §1–5."""
import hashlib

from rdflib import BNode, Graph, Literal, OWL, RDF, RDFS, URIRef
from rdflib.collection import Collection

from domains.robotics.graph import CLASSES, S
from domains.robotics.views import BUSINESS, SKILLS
from srv_context import _RDF_LOCK


def project(version, payload, *, role_map=None):
    """Caller validates the artifact contract; this view grants no execution permission."""
    if (version['payload_kind'] != 'robotics_rdf' or not isinstance(payload, bytes)
            or hashlib.sha256(payload).hexdigest() != version['payload_sha256']):
        raise ValueError('详情投影与固定版本不一致。')
    if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise ValueError('本体包含不允许的 XML 声明。')
    with _RDF_LOCK:
        graph = Graph().parse(data=payload, format='xml')
        return _project(graph, version, role_map or {})


def _project(graph, version, role_map):
    def text(subject, predicate, fallback=''):
        values = sorted(graph.objects(subject, predicate), key=lambda v: (getattr(v, 'language', None) != 'zh', str(v)))
        return str(values[0]) if values else fallback

    def label(subject):
        name = str(subject)
        fallback = CLASSES.get(name[len(str(S)):], name) if name.startswith(str(S)) else name
        return text(subject, RDFS.label, fallback)

    def identifiers(subject, predicate):
        values = set()
        for node in graph.objects(subject, predicate):
            if isinstance(node, URIRef):
                values.add(str(node))
            elif isinstance(node, BNode):
                for head in graph.objects(node, OWL.unionOf):
                    values.update(str(term) for term in Collection(graph, head) if isinstance(term, URIRef))
        return sorted(values)

    kinds = {'classes': (OWL.Class, RDFS.Class), 'object_properties': (OWL.ObjectProperty,),
             'data_properties': (OWL.DatatypeProperty,)}
    terms = {key: {s for kind in types for s in graph.subjects(RDF.type, kind) if isinstance(s, URIRef)}
             for key, types in kinds.items()}
    schema = set().union(*terms.values())
    result = {k: version[k] for k in ('ontology_id', 'version_id', 'payload_sha256')}
    for key, subjects in terms.items():
        rows = []
        for subject in sorted(subjects, key=str):
            row = {'id': str(subject), 'label': label(subject), 'definition': text(subject, RDFS.comment)}
            if key == 'classes':
                row['parent_ids'] = identifiers(subject, RDFS.subClassOf)
            else:
                row['domain_ids'] = identifiers(subject, RDFS.domain)
                if key == 'object_properties':
                    row['range_ids'] = identifiers(subject, RDFS.range)
                else:
                    row['datatype'] = text(subject, RDFS.range, '未声明')
            rows.append(row)
        result[key] = rows

    # DR-073 §4: use full trusted type IRIs, never local-name similarity.
    business, skills = {str(S[name]) for name in BUSINESS}, {str(S[name]) for name in SKILLS}
    for binding in role_map.get('bindings', []):
        if binding.get('kind') == 'classes':
            if binding.get('role') in BUSINESS:
                business.add(binding['iri'])
            if binding.get('role') in SKILLS:
                skills.add(binding['iri'])
    typed_classes = terms['classes'] | {S[name] for name in CLASSES}
    individuals = {s for cls in typed_classes | {OWL.NamedIndividual}
                   for s in graph.subjects(RDF.type, cls) if isinstance(s, URIRef)} - schema
    result['individuals'] = []
    for subject in sorted(individuals, key=str):
        types = sorted(str(t) for t in graph.objects(subject, RDF.type)
                       if isinstance(t, URIRef) and t != OWL.NamedIndividual)
        group = 'business' if business.intersection(types) else 'skills' if skills.intersection(types) else 'other'
        result['individuals'].append({'id': str(subject), 'label': label(subject), 'class_ids': types, 'group': group})
    result['object_assertions'] = []
    result['data_assertions'] = []
    for subject in sorted(individuals, key=str):
        for predicate, value in sorted(graph.predicate_objects(subject), key=lambda pair: tuple(map(str, pair))):
            if value in individuals and predicate != RDF.type:
                result['object_assertions'].append({'subject': str(subject), 'property': str(predicate), 'object': str(value)})
            elif isinstance(value, Literal) and predicate in terms['data_properties']:
                result['data_assertions'].append({'subject': str(subject), 'property': str(predicate), 'value': str(value),
                    'datatype': str(value.datatype) if value.datatype else None, 'language': value.language})
    return result

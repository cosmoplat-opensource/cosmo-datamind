"""Read-only robot interactive ontology graph projections — DR-161 §§3–6."""
from copy import deepcopy
import hashlib
import json

from rdflib import BNode, Graph, Literal, OWL, RDF, RDFS, URIRef
from rdflib.collection import Collection
from rdflib.compare import to_canonical_graph

from domains.robotics.modeling.common import digest
from domains.robotics.modeling.export import tbox_owl
from domains.robotics.modeling._schema import validate_namespace


def _id(term):
    return '_:' + str(term) if isinstance(term, BNode) else str(term)


def _edge(kind, source, predicate, target):
    s, p, t = map(_id, (source, predicate, target))
    return {'id': json.dumps([kind, s, p, t], ensure_ascii=False, separators=(',', ':')),
            'kind': kind, 'source': s, 'predicate': p, 'target': t}


def project_design(session):
    """Project only persisted, digest-bound proposals; ABox comes from fixed output."""
    state = deepcopy(session)
    validate_namespace(state['namespace'])
    result = state.get('result')
    stage = state['stage']
    if stage not in ('classes', 'properties', 'abox') or type(state['revision']) is not int or state['revision'] < 0:
        raise ValueError('设计阶段或修订无效。')
    if state.get('snapshot_sha256') != (digest(result) if result is not None else None):
        raise ValueError('设计快照摘要不一致。')
    if stage != 'classes':
        frozen = state.get('class_confirmation')
        if not frozen or not result or frozen['classes'] != result['classes']:
            raise ValueError('类别确认与当前结果不一致。')
    if stage == 'abox':
        frozen = state.get('schema_confirmation')
        if not frozen or digest(frozen['tbox']) != frozen['sha256']:
            raise ValueError('骨架确认摘要不一致。')
        if any(frozen['tbox'][k] != result[k] for k in ('classes', 'object_properties', 'data_properties')):
            raise ValueError('骨架与当前结果不一致。')
    context = {'kind': 'design', 'key': state['session_id'], 'revision': state['revision'],
               'stage': stage, 'status': state['status'], 'snapshot_sha256': state.get('snapshot_sha256')}
    graph = Graph()
    if result and result.get('classes'):
        schema = {key: result.get(key, []) if stage != 'classes' or key == 'classes' else []
                  for key in ('classes', 'object_properties', 'data_properties')}
        raw = tbox_owl(schema, state['namespace'])
        graph.parse(data=raw, format='xml')
    return _project(graph, context, state.get('name', ''))


def project_version(version, payload):
    """Artifact readers must first validate provenance; this verifies exact bytes."""
    if (version.get('payload_kind') != 'robotics_rdf' or not isinstance(payload, bytes)
            or hashlib.sha256(payload).hexdigest() != version.get('payload_sha256')):
        raise ValueError('固定版本与载荷摘要不一致。')
    if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise ValueError('不允许 DTD 或实体声明。')
    try:
        graph = Graph().parse(data=payload, format='xml')
    except Exception as exc:
        raise ValueError('无法读取固定版本 RDF。') from exc
    context = {'kind': 'version', 'key': json.dumps([version['ontology_id'], version['version_id']], separators=(',', ':')),
               'revision': version['payload_sha256'], 'snapshot_sha256': version['payload_sha256'],
               'stage': 'fixed', 'status': version.get('publication_state', 'candidate')}
    return _project(graph, context, version.get('name', ''))


def _project(source, context, name):
    # Bound work before canonicalization, whose blank-node processing can be expensive.
    if len(source) > 200000:
        raise ValueError('本体规模超过视图上限。')
    named = {s for s, p, o in source if p == RDF.type and isinstance(s, URIRef)}
    if len(named) > 20000:
        raise ValueError('本体实体规模超过视图上限。')
    graph = to_canonical_graph(source)
    nodes, properties, individuals, edges, relations = {}, {}, {}, {}, {}
    warnings = set()

    def check_budget(*, entities=0, links=0):
        if (len(nodes) + len(properties) + len(individuals) + entities > 20000
                or len(edges) + len(relations) + links > 100000):
            raise ValueError('本体规模超过视图上限。')

    def add_edge(target, kind, s, p, t):
        edge = _edge(kind, s, p, t)
        check_budget(links=int(edge['id'] not in target))
        target[edge['id']] = edge

    def labels(term):
        return [{'value': str(v), 'language': v.language or ''}
                for v in sorted(graph.objects(term, RDFS.label), key=lambda v: (getattr(v, 'language', '') or '', str(v)))
                if isinstance(v, Literal)]

    def base(term, kind):
        return {'id': _id(term), 'iri': _id(term), 'type': kind, 'labels': labels(term),
                'definition': str(next(iter(sorted(graph.objects(term, RDFS.comment), key=str)), ''))}

    def add_node(term, kind='class'):
        if term is None or isinstance(term, Literal):
            return None
        key = _id(term)
        if key in nodes:
            return key
        check_budget(entities=1)
        node = base(term, 'expression' if isinstance(term, BNode) else kind)
        node['individuals'] = []
        nodes[key] = node
        if isinstance(term, BNode):
            head = graph.value(term, OWL.unionOf)
            if head is not None:
                try:
                    members = list(Collection(graph, head))
                except ValueError as exc:
                    raise ValueError('并集列表无效。') from exc
                node['expression'] = {'operator': 'unionOf', 'operands': sorted(_id(m) for m in members)}
                for member in members:
                    add_node(member)
                    add_edge(edges, 'expression', term, OWL.unionOf, member)
            else:
                warnings.add('存在尚未绘制的匿名 OWL 表达式；请查看原始本体。')
        return key

    class_terms = set(graph.subjects(RDF.type, OWL.Class)) | set(graph.subjects(RDF.type, RDFS.Class))
    for term in sorted(class_terms, key=_id):
        add_node(term)
    property_terms = set()
    for rdf_type, kind in ((OWL.ObjectProperty, 'object'), (OWL.DatatypeProperty, 'data')):
        for term in sorted(graph.subjects(RDF.type, rdf_type), key=_id):
            property_terms.add(term)
            prop = base(term, kind)
            domains = sorted(set(graph.objects(term, RDFS.domain)), key=_id)
            ranges = sorted(set(graph.objects(term, RDFS.range)), key=_id)
            prop.update(domains=[_id(t) for t in domains], ranges=[_id(t) for t in ranges])
            check_budget(entities=int(_id(term) not in properties))
            properties[_id(term)] = prop
            for t in domains:
                add_node(t)
            for t in ranges:
                add_node(t, 'datatype' if kind == 'data' else 'class')
            check_budget(links=len(domains) * len(ranges))
            for s in domains:
                for t in ranges:
                    add_edge(edges, kind, s, term, t)
    for predicate, kind in ((RDFS.subClassOf, 'subclass'), (OWL.disjointWith, 'disjoint'),
                            (OWL.equivalentClass, 'equivalent')):
        for s, t in graph.subject_objects(predicate):
            add_node(s); add_node(t)
            add_edge(edges, kind, s, predicate, t)
    subjects = set(graph.subjects(RDF.type, OWL.NamedIndividual))
    for term in class_terms:
        subjects.update(graph.subjects(RDF.type, term))
    subjects = {s for s in subjects if isinstance(s, URIRef)} - class_terms - property_terms
    for term in sorted(subjects, key=_id):
        check_budget(entities=1)
        item = base(term, 'individual')
        item['types'] = sorted({_id(t) for t in graph.objects(term, RDF.type) if isinstance(t, (URIRef, BNode)) and t != OWL.NamedIndividual})
        item['data'] = []
        for cls in item['types']:
            if cls in nodes:
                nodes[cls]['individuals'].append(_id(term))
        for p, o in sorted(graph.predicate_objects(term), key=lambda pair: tuple(map(str, pair))):
            if isinstance(o, Literal):
                item['data'].append({'predicate': str(p), 'value': str(o), 'datatype': str(o.datatype) if o.datatype else '',
                                     'language': o.language or ''})
            elif o in subjects and p != RDF.type:
                add_edge(relations, 'relation', term, p, o)
        individuals[_id(term)] = item
    if len(nodes) + len(properties) + len(individuals) > 20000 or len(edges) + len(relations) > 100000:
        raise ValueError('本体规模超过视图上限。')
    # Unknown OWL constraints stay in source bytes; the view never silently asserts their meaning.
    supported = {OWL.unionOf, OWL.disjointWith, OWL.equivalentClass}
    if any(str(p).startswith(str(OWL)) and p not in supported for _, p, _ in graph):
        warnings.add('部分 OWL 公理未在画布展开；完整语义保留于原始本体。')
    return {'schema': 'datamind-interactive-ontology-graph/1', 'context': context, 'metadata': {'name': name, 'read_only': True},
            'nodes': [nodes[k] for k in sorted(nodes)], 'properties': [properties[k] for k in sorted(properties)],
            'individuals': [individuals[k] for k in sorted(individuals)], 'edges': [edges[k] for k in sorted(edges)],
            'relations': [relations[k] for k in sorted(relations)], 'warnings': sorted(warnings)}

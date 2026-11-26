"""固定版本的内存 RDF 编辑副本 —— DR-188。"""
import re

from rdflib import BNode, Literal, OWL, RDF, RDFS, URIRef, XSD

from domains.robotics.authoring import iri as absolute_iri
from domains.robotics.xml_text import invalid_xml_text
from .graph_class_projection import confirmed_class_aliases
from .graph_datatypes import is_data_range, value_types

KINDS = {'class': OWL.Class, 'individual': OWL.NamedIndividual,
         'object': OWL.ObjectProperty, 'data': OWL.DatatypeProperty}
CLASS_LINKS = {RDFS.subClassOf, OWL.equivalentClass, OWL.disjointWith}
STANDARD = tuple(map(str, (RDF, RDFS, OWL, XSD)))
FIELDS = {
    'create': ({'iri', 'kind', 'name'}, {'parent', 'domain', 'range'}),
    'edit': ({'iri', 'new_iri', 'name', 'comment'}, set()),
    'delete': ({'iri'}, set()),
    'link': ({'source', 'predicate', 'target'}, set()),
    'unlink': ({'source', 'predicate', 'target'}, set()),
    'add_value': ({'source', 'predicate', 'value', 'language', 'datatype'}, set()),
    'remove_value': ({'source', 'predicate', 'value', 'language', 'datatype'}, set()),
}


def iri(value):
    if re.search(r'[<>"{}|\\^`]', value):
        raise ValueError('IRI 含无效字符。')
    return absolute_iri(value)


def occurrences(graph, term):
    return (set(graph.triples((term, None, None))) | set(graph.triples((None, term, None)))
            | set(graph.triples((None, None, term))))


def roles(graph, term):
    types = set(graph.objects(term, RDF.type))
    found = {kind for kind, declaration in KINDS.items() if declaration in types}
    if OWL.AnnotationProperty in types or RDFS.Datatype in types or OWL.Ontology in types:
        return set()
    if RDFS.Class in types:
        found.add('class')
    if any(is_class(graph, cls) for cls in types):
        found.add('individual')
    if not found.intersection({'object', 'data'}):
        for _, _, target in graph.triples((None, term, None)):
            found.add('data' if isinstance(target, Literal) else 'object')
    return found


def is_class(graph, term):
    return (term, RDF.type, OWL.Class) in graph or (term, RDF.type, RDFS.Class) in graph


def editable(term):
    if str(term).startswith(STANDARD):
        raise ValueError('标准词汇不能作为编辑目标。')


def require_role(graph, term, allowed):
    if not roles(graph, term).intersection(allowed):
        raise ValueError('端点不存在或类型与操作不符。')


def simple_restriction(graph, node):
    facts = set(graph.triples((node, None, None)))
    fields = {p for _, p, _ in facts}
    fillers = {OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue}
    if (len(facts) != 3 or (node, RDF.type, OWL.Restriction) not in facts or OWL.onProperty not in fields
            or not fields.intersection(fillers) or any(isinstance(o, BNode) for _, _, o in facts)):
        raise ValueError('涉及无法安全分离的匿名公理或共享结构。')
    owners = set(graph.triples((None, None, node)))
    if any(not isinstance(s, URIRef) or p not in CLASS_LINKS for s, p, _ in owners):
        raise ValueError('限制表达式存在不支持的外层引用。')
    return facts, owners


def complete_removals(graph, direct):
    removed = set(direct)
    # Remove whole simple restrictions, retaining an expression still in use by another owner.
    anonymous = {x for s, _, o in direct for x in (s, o) if isinstance(x, BNode)}
    for node in anonymous:
        if (node, RDF.type, OWL.Axiom) in graph:
            continue
        facts, owners = simple_restriction(graph, node)
        if removed.intersection(facts) or owners <= removed:
            removed.update(facts | owners)

    # Follow annotations of precisely the affected assertion, including annotations of annotations.
    structural_types = {OWL.NegativePropertyAssertion, RDF.Statement, OWL.AllDifferent,
                        OWL.AllDisjointClasses, OWL.AllDisjointProperties, OWL.Restriction}
    pending = list(removed)
    annotations = set()
    while pending:
        s, p, o = pending.pop()
        if isinstance(s, URIRef) and (p in {RDF.first, RDF.rest, OWL.onProperty} or
                                     structural_types.intersection(graph.objects(s, RDF.type))):
            raise ValueError('涉及不支持的复合断言。')
        if any((n, RDF.predicate, p) in graph and (n, RDF.object, o) in graph for n in graph.subjects(RDF.subject, s)):
            raise ValueError('涉及 RDF 语句注解，不能单独移除。')
        for n in graph.subjects(OWL.annotatedSource, s):
            if (n, OWL.annotatedProperty, p) not in graph or (n, OWL.annotatedTarget, o) not in graph:
                continue
            if n in annotations:
                continue
            facts = set(graph.triples((n, None, None)))
            if (not isinstance(n, BNode) or (n, RDF.type, OWL.Axiom) not in facts
                    or any(len(set(graph.objects(n, key))) != 1 for key in
                           (OWL.annotatedSource, OWL.annotatedProperty, OWL.annotatedTarget))
                    or any(isinstance(obj, BNode) and pred not in (OWL.annotatedSource, OWL.annotatedTarget)
                           for _, pred, obj in facts)):
                raise ValueError('无法安全确定公理注解的边界。')
            annotations.add(n)
            pending.extend(facts - removed)
            removed.update(facts)
    for node in annotations:
        if not set(graph.triples((None, None, node))) <= removed:
            raise ValueError('公理注解仍有其他引用。')
    if any((s, RDF.type, OWL.Axiom) in graph and s not in annotations for s, _, _ in removed):
        raise ValueError('无法确定受影响公理。')
    return removed


def create(graph, op):
    term = iri(op['iri'])
    editable(term)
    kind = op['kind']
    if kind not in KINDS or not op['name'].strip():
        raise ValueError('请选择实体类型并填写名称。')
    if occurrences(graph, term):
        raise ValueError('IRI 已被使用。')
    added = {(term, RDF.type, KINDS[kind]), (term, RDFS.label, Literal(op['name'].strip(), lang='zh'))}
    for field, predicate in [('parent', RDF.type if kind == 'individual' else RDFS.subClassOf),
                             ('domain', RDFS.domain), ('range', RDFS.range)]:
        if not op.get(field):
            continue
        target = iri(op[field])
        if (field == 'parent') != (kind in {'individual', 'class'}):
            raise ValueError('字段不适用于此实体类型。')
        if kind == 'data' and field == 'range':
            if not is_data_range(graph, target):
                raise ValueError('请选择数据类型。')
        elif not is_class(graph, target):
            raise ValueError('请选择已有类别。')
        added.add((term, predicate, target))
    return set(), added


def entity_change(graph, op):
    term = iri(op['iri'])
    editable(term)
    kinds = roles(graph, term)
    if len(kinds) != 1 or (term, RDF.type, OWL.AnnotationProperty) in graph:
        raise ValueError('实体不存在或 IRI 具有多种角色，无法安全修改。')
    facts = occurrences(graph, term)
    if op['op'] == 'delete':
        facts = complete_removals(graph, facts)
        # Preserve individuals recognized only via their explicit class.
        preserved = {(s, RDF.type, OWL.NamedIndividual) for s in graph.subjects(RDF.type, term)
                     if 'class' in kinds and isinstance(s, URIRef) and s != term
                     and not any(cls != term and is_class(graph, cls) for cls in graph.objects(s, RDF.type))}
        if 'class' in kinds:
            neighbors = {node for s, p, o in facts if p in CLASS_LINKS for node in (s, o)
                         if isinstance(node, URIRef) and node != term and not str(node).startswith(STANDARD)}
            preserved.update((node, RDF.type, OWL.Class) for node in neighbors if not is_class(graph, node))
        for predicate in {p for _, p, _ in facts if p != term and not str(p).startswith(STANDARD)}:
            uses = set(graph.triples((None, predicate, None)))
            if uses <= facts and not any(graph.objects(predicate, RDF.type)):
                for kind in roles(graph, predicate) & {'object', 'data'}:
                    preserved.add((predicate, RDF.type, KINDS[kind]))
        return facts, preserved
    new = iri(op['new_iri'])
    editable(new)
    if new != term and occurrences(graph, new):
        raise ValueError('新 IRI 已被使用。')
    removed, added = set(), set()
    for p, value in ((RDFS.label, op['name']), (RDFS.comment, op['comment'])):
        old = {t for t in graph.triples((term, p, None))
               if isinstance(t[2], Literal) and (t[2].language or '').lower() == 'zh'}
        if len(old) > 1:
            raise ValueError('存在多个同语言文字，无法确定编辑目标。')
        updated = {(term, p, Literal(value, lang='zh'))} if value else set()
        if old != updated:
            removed.update(complete_removals(graph, old))
            added.update(updated)
    if new != term:
        added = {tuple(new if part == term else part for part in triple) for triple in (facts - removed) | added}
        removed.update(facts)
    return removed, added


# XSD lexical forms, limited to years 0001-9999 and without 24:00:00 so RDF readers accept the value.
_DAY = r'(?P<year>000[1-9]|00[1-9][0-9]|0[1-9][0-9]{2}|[1-9][0-9]{3})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12][0-9]|3[01])'
_ZONE = r'(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))?'
_LEXICAL = {
    str(XSD.integer): re.compile(r'[+-]?[0-9]+', re.ASCII),
    str(XSD.decimal): re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', re.ASCII),
    str(XSD.boolean): re.compile(r'true|false|0|1'),
    str(XSD.date): re.compile(_DAY + _ZONE, re.ASCII),
    str(XSD.dateTime): re.compile(_DAY + r'T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?' + _ZONE, re.ASCII),
}
_HINTS = {str(XSD.date): '日期须写为 2026-11-26，可带 Z 或 +08:00 等时区。',
          str(XSD.dateTime): '日期时间须写为 2026-11-26T10:00:00，秒可带小数，可带 Z 或 +08:00 等时区。'}


def validate_literal(value, datatype):
    """DR-193: check lexical forms explicitly, never via Python-version-dependent ISO parsers."""
    from calendar import monthrange
    if not datatype or datatype == str(XSD.string):
        return
    pattern = _LEXICAL.get(datatype)
    match = pattern.fullmatch(value) if pattern else None
    if match and 'day' in match.groupdict():
        year, month, day = (int(match[k]) for k in ('year', 'month', 'day'))
        if day > monthrange(year, month)[1]:
            match = None
    if match is None:
        raise ValueError('数据值与所选数据类型不符。' + _HINTS.get(datatype, ''))


def class_ancestors(graph, seeds, role_map, *, equivalent):
    aliases = {}
    if role_map:
        declared = {str(c) for kind in (OWL.Class, RDFS.Class) for c in graph.subjects(RDF.type, kind)}
        aliases, _ = confirmed_class_aliases(role_map, declared)
    equivalents = {}
    for source, app in aliases.items():
        for a, b in ((source, app), (app, source)):
            equivalents.setdefault(URIRef(a), []).append(URIRef(b))

    pending = list(seeds)
    known = set()
    while pending:
        cls = pending.pop()
        if cls in known:
            continue
        known.add(cls)
        pending.extend(equivalents.get(cls, ()))
        pending.extend(p for p in graph.objects(cls, RDFS.subClassOf) if isinstance(p, URIRef))
        if equivalent:
            pending.extend(p for p in graph.objects(cls, OWL.equivalentClass) if isinstance(p, URIRef))
            pending.extend(p for p in graph.subjects(OWL.equivalentClass, cls) if isinstance(p, URIRef))
    return known


def check_class_endpoint(graph, person, predicate, side, role_map, message):
    constraints = list(graph.objects(predicate, side))
    if not constraints:
        return
    types = {t for t in graph.objects(person, RDF.type) if isinstance(t, URIRef) and t != OWL.NamedIndividual}
    known = class_ancestors(graph, types | {OWL.Thing}, role_map, equivalent=True)

    def matches(constraint, known):
        pending = [constraint]
        visited = set()
        while pending:
            term = pending.pop()
            if term in known:
                return True
            if isinstance(term, BNode) and term not in visited:
                visited.add(term)
                head = graph.value(term, OWL.unionOf)
                if head is not None:
                    pending.extend(graph.items(head))
        return False

    if not all(matches(c, known) for c in constraints):
        raise ValueError(message)


def assertion_change(graph, op, role_map):
    subject, predicate = iri(op['source']), iri(op['predicate'])
    editable(subject)
    verb = op['op']
    if verb in ('add_value', 'remove_value'):
        require_role(graph, subject, {'individual'})
        editable(predicate)
        require_role(graph, predicate, {'data'})
        lang, datatype = op['language'].lower(), op['datatype']
        if lang and (datatype or not re.fullmatch(r'[a-z]+(?:-[a-z0-9]+)*', lang)):
            raise ValueError('语言标签或数据类型无效。')
        if verb == 'remove_value':
            target = next((v for v in graph.objects(subject, predicate) if isinstance(v, Literal)
                           and str(v) == op['value'] and (v.language or '').lower() == lang
                           and str(v.datatype or '') == datatype), None)
            if target is None:
                raise ValueError('所选数据值不存在。')
        else:
            check_class_endpoint(graph, subject, predicate, RDFS.domain, role_map, '实例不满足数据属性的定义域。')
            literal_type = RDF.langString if lang else URIRef(datatype) if datatype else XSD.string
            if literal_type not in value_types(graph.objects(predicate, RDFS.range)):
                raise ValueError('数据值的类型不满足数据属性的值域，或基础编辑器不支持此类型。')
            validate_literal(op['value'], datatype)
            target = Literal(op['value'], lang=lang or None, datatype=iri(datatype) if datatype else None, normalize=False)
    else:
        target = iri(op['target'])
        if predicate in CLASS_LINKS:
            if not is_class(graph, subject) or not is_class(graph, target) or (subject == target and verb == 'link'):
                raise ValueError('请选择两个不同的已有类别。')
            if verb == 'link' and predicate == RDFS.subClassOf:
                ancestors = class_ancestors(graph, [target], role_map, equivalent=False)
                if subject in ancestors:
                    raise ValueError('父类不能指向自身或已有子类，否则会形成循环继承。')
        elif predicate == RDF.type:
            require_role(graph, subject, {'individual'})
            require_role(graph, target, {'class'})
        elif predicate in (RDFS.domain, RDFS.range):
            require_role(graph, subject, {'object', 'data'})
            if predicate == RDFS.range and 'data' in roles(graph, subject):
                if verb == 'link' and not is_data_range(graph, target):
                    raise ValueError('数据属性的值域须为数据类型。')
            else:
                require_role(graph, target, {'class'})
        else:
            editable(predicate)
            require_role(graph, predicate, {'object'})
            for term in (subject, target):
                require_role(graph, term, {'individual'})
            if verb == 'link':
                check_class_endpoint(graph, subject, predicate, RDFS.domain, role_map, '起点实例不满足对象属性的定义域。')
                check_class_endpoint(graph, target, predicate, RDFS.range, role_map, '终点实例不满足对象属性的值域。')
    triple = (subject, predicate, target)
    if verb in ('link', 'add_value'):
        if triple in graph:
            raise ValueError('该断言已存在。')
        return set(), {triple}
    if triple not in graph:
        raise ValueError('所选断言不存在。')
    removed = complete_removals(graph, {triple})
    keep = set()
    if predicate == RDF.type and not any(t != target and is_class(graph, t) for t in graph.objects(subject, RDF.type)):
        keep.add((subject, RDF.type, OWL.NamedIndividual))
    # DR-195: the last use may be the only declaration of the predicate's identity.
    if (not str(predicate).startswith(STANDARD) and not list(graph.objects(predicate, RDF.type))
            and set(graph.triples((None, predicate, None))) <= removed):
        keep.add((predicate, RDF.type, OWL.DatatypeProperty if isinstance(target, Literal) else OWL.ObjectProperty))
    return removed, keep


def apply_edit(graph, op, *, role_map=None):
    if not isinstance(op, dict) or not all(isinstance(v, str) and len(v) <= 4096 and not invalid_xml_text(v)
                                           for v in op.values()) or op.get('op') not in FIELDS:
        raise ValueError('图谱编辑操作无效。')
    verb = op['op']
    required, optional = FIELDS[verb]
    fields = set(op) - {'op'}
    if not required <= fields <= required | optional:
        raise ValueError('编辑字段不完整或不受支持。')
    if verb == 'create':
        removed, added = create(graph, op)
    elif verb in ('delete', 'edit'):
        removed, added = entity_change(graph, op)
    else:
        removed, added = assertion_change(graph, op, role_map)
    removed, added = removed - added, {t for t in added if t not in graph}
    if not removed and not added:
        raise ValueError('没有可应用的改动。')
    for triple in removed:
        graph.remove(triple)
    for triple in added:
        graph.add(triple)
    return {'removed': len(removed), 'added': len(added),
            'sample': sorted(' '.join(term.n3() for term in t) for t in removed)[:20]}


def parse_source(version, payload):
    """Preserve typed lexical forms locally, without changing RDFLib's process defaults."""
    import hashlib
    from rdflib import Graph
    from rdflib.parser import create_input_source
    from rdflib.plugins.parsers.rdfxml import RDFXMLHandler, create_parser

    if (version['payload_kind'] != 'robotics_rdf' or hashlib.sha256(payload).hexdigest() != version['payload_sha256']
            or b'\x00' in payload or b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper()):
        raise ValueError('固定版本 RDF 无效。')

    class LexicalHandler(RDFXMLHandler):
        def property_element_end(self, name, qname):
            current = self.current
            if current.datatype is not None and current.data is not None and current.object is None:
                current.object = Literal(current.data, datatype=current.datatype, normalize=False)
                current.data = None
            super().property_element_end(name, qname)

    graph = Graph()
    source = create_input_source(data=payload)
    parser = create_parser(source, graph)
    parser.setContentHandler(LexicalHandler(graph))
    try:
        parser.parse(source)
    except Exception as exc:
        raise ValueError('无法读取固定版本 RDF。') from exc
    return graph


LISTED_IRIS = 5


def unrepresentable_predicates(graph):
    """Predicates RDF/XML cannot write as element names — DR-196. Only called after a failed write."""
    from rdflib import Graph
    found = []
    for predicate in sorted(set(graph.predicates()), key=str):
        probe = Graph()
        probe.add((URIRef('urn:probe:subject'), predicate, Literal('')))
        try:
            probe.serialize(format='xml')
        except ValueError:
            found.append(str(predicate))
    return found


def format_failure(found, *, saving):
    listed = '、'.join(found[:LISTED_IRIS]) + (f' 等 {len(found)} 个' if len(found) > LISTED_IRIS else '')
    if saving:
        if not found:
            return '无法保存为新版本：版本以 RDF/XML 存储，此副本含无法表示的内容。'
        return (f'无法保存为新版本：版本以 RDF/XML 存储，以下属性 IRI 无法表示：{listed}。'
                '请修改这些属性的 IRI，使最后一段以字母或下划线开头且不以 / 或 # 结尾，再保存。')
    if not found:
        return '此副本含所选格式无法表示的断言，请选择其他导出格式。'
    return f'无法导出为 OWL（RDF/XML），以下属性 IRI 无法表示：{listed}。可改用 Turtle 或 JSON-LD 导出，或修改这些属性的 IRI。'


def render_copy(graph, version, operations, fmt, *, role_map=None, saving=False):
    import json
    from .interactive_ontology_graph_view import _project, version_context

    if not isinstance(operations, list) or len(operations) > 40:
        raise ValueError('一个编辑副本最多支持 40 次操作。')
    if len(graph) > 20000:
        raise ValueError('基础编辑支持最多两万条 RDF 断言。')
    impact = {'added': 0, 'removed': 0, 'sample': []}
    for operation in operations:
        impact = apply_edit(graph, operation, role_map=role_map)
        if len(graph) > 20000:
            raise ValueError('编辑结果超过两万条 RDF 断言。')
    if fmt != 'view':
        if fmt == 'json':
            # Expanded JSON-LD keeps the lexical value even for native numeric datatypes.
            nodes = {}
            for s, p, o in graph:
                sid = s.n3() if isinstance(s, BNode) else str(s)
                node = nodes.setdefault(sid, {'@id': sid})
                if isinstance(o, Literal):
                    obj = {'@value': str(o)}
                    if o.language:
                        obj['@language'] = o.language
                    elif o.datatype:
                        obj['@type'] = str(o.datatype)
                else:
                    obj = {'@id': o.n3() if isinstance(o, BNode) else str(o)}
                node.setdefault(str(p), []).append(obj)
            content = json.dumps(list(nodes.values()), ensure_ascii=False)
        else:
            # N-Triples is a Turtle subset that preserves typed lexical forms.
            try:
                content = graph.serialize(format={'owl': 'xml', 'ttl': 'nt'}[fmt])
            except (ValueError, UnicodeError) as exc:
                found = unrepresentable_predicates(graph) if fmt == 'owl' else []
                raise ValueError(format_failure(found, saving=saving)) from exc
        return {'content': content}
    context = {**version_context(version), 'kind': 'edit-copy', 'status': 'editing'}
    view = _project(graph, context, '', role_map=role_map)
    for node in view['nodes']:
        if node['type'] == 'class' and not is_class(graph, URIRef(node['id'])):
            node['declared_class'] = False
    datatypes = {t for t in graph.subjects(RDF.type, RDFS.Datatype) if is_data_range(graph, t)}
    for prop in view['properties']:
        if prop['type'] == 'data':
            ranges = list(graph.objects(URIRef(prop['id']), RDFS.range))
            datatypes.update(t for t in ranges if is_data_range(graph, t))
            prop['value_types'] = [str(t) for t in value_types(ranges)]
    view['declared_datatypes'] = sorted(map(str, datatypes))
    return {'view': view, 'impact': impact}

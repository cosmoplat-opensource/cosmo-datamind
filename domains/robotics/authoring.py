"""Typed edits over the authoritative RDF graph — DR-059 / DR-060 / DR-061.

Projection is for display only. Writes use a bounded allowlist and preserve all
unrelated RDF statements, including unknown vocabulary and blank nodes.
"""
from copy import deepcopy
import json
import math
from urllib.parse import urlsplit
import uuid

from rdflib import Literal, OWL, RDF, RDFS, URIRef, XSD

from .graph import S, individual

EDITABLE = {'Workpiece', 'PartType', 'Workstation', 'Carrier', 'PlacementSlot',
            'PerceptionProfile', 'PlacementProfile', 'JointConfiguration', 'CartesianPose'}
FIELDS = {'label', 'alias'}
TYPE_FIELDS = {'PerceptionProfile': {'objectName', 'modelName', 'threshold', 'exposureTime'},
               'JointConfiguration': {'rawValue', 'angleUnit'},
               'CartesianPose': {'rawValue', 'lengthUnit', 'angleUnit'}}
RELATIONS = {
    'hasPartType': ('Workpiece', 'PartType', 1), 'hasCarrier': ('Workstation', 'Carrier', None),
    'hasSlot': ('Carrier', 'PlacementSlot', None), 'hasPerceptionProfile': ('PartType', 'PerceptionProfile', 1),
    'hasPlacementProfile': ('PlacementSlot', 'PlacementProfile', 1),
    'hasCaptureJoints': ('PerceptionProfile', 'JointConfiguration', 1),
    'hasTargetPose': ('PlacementProfile', 'CartesianPose', 1),
    'expressedInFrame': ('CartesianPose', 'CoordinateFrame', 1),
}


def text(value, name, limit=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{name} 必须为非空且长度合理的文字。')
    return value


def iri(value):
    text(value, 'IRI', 2048)
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https', 'urn') or not parsed.path and not parsed.netloc or any(c.isspace() for c in value):
        raise ValueError('需要完整、合法的 http/https/urn IRI。')
    return URIRef(value)


def kind(graph, entity):
    kinds = [name for name in EDITABLE if (entity, RDF.type, S[name]) in graph]
    protected = ('AtomicSkill','CompositeSkill','TaskProcedure','SkillInvocation','SkillParameter',
                 'ParameterBinding','PortContract','DataReference','SkillCondition','SkillImplementation',
                 'ImplementationConfiguration','SourceDocument')
    if len(kinds) != 1 or (entity, RDF.type, OWL.Class) in graph or any((entity,RDF.type,S[t]) in graph for t in protected):
        raise ValueError('对象不存在、类型不明确或不属于可编辑业务对象。')
    return kinds[0]


def field_predicate(name):
    return RDFS.label if name == 'label' else S[name]


def validate_value(entity_kind, name, value):
    if name not in FIELDS | TYPE_FIELDS.get(entity_kind, set()):
        raise ValueError('此字段只读或不适用于该对象。')
    if value is None:
        if name == 'label': raise ValueError('名称不能清空。')
        return
    text(value, name)
    if name in ('threshold', 'exposureTime'):
        try: number = float(value)
        except ValueError as exc: raise ValueError('参数必须为有限数字。') from exc
        if not math.isfinite(number) or (name == 'threshold' and not 0 <= number <= 1) or (name == 'exposureTime' and number <= 0):
            raise ValueError('阈值或曝光时间超出允许范围。')
    if name == 'rawValue':
        try: vector = json.loads(value)
        except ValueError as exc: raise ValueError('位姿/关节配置必须为数值数组。') from exc
        size = 6 if entity_kind == 'JointConfiguration' else 7
        try:
            valid = isinstance(vector,list) and len(vector) == size and all(
                type(x) in (int,float) and math.isfinite(x) for x in vector)
        except OverflowError:
            valid = False
        if not valid: raise ValueError('位姿/关节配置维数或数值不合法。')
    if name == 'lengthUnit' and value not in ('m', 'mm') or name == 'angleUnit' and value not in ('rad', 'deg'):
        raise ValueError('不支持的单位；未知单位请留空。')


def provenance(graph, entity, field, basis):
    record = URIRef('urn:datamind:knowledge-change:' + uuid.uuid4().hex)
    graph.add((record, RDF.type, S.KnowledgeChange))
    # Literal identity preserves history even when an unreferenced object is removed.
    for prop, value in [('entityIRI', str(entity)), ('field', field), ('basis', basis)]:
        graph.add((record, S[prop], Literal(value, datatype=XSD.string)))


def _apply(graph, operation):
    if not isinstance(operation, dict): raise ValueError('操作必须为对象。')
    verb = operation.get('op')
    fields = {'create_entity': {'entity_type', 'label'}, 'set_field': {'field', 'value'},
              'set_relation': {'relation', 'targets'}, 'delete_entity': set()}
    if verb not in fields or set(operation) != {'op', 'iri', 'basis'} | fields[verb]:
        raise ValueError('未知操作或字段；不能提交任意 RDF。')
    subject = iri(operation['iri'])
    basis = text(operation['basis'], 'basis')
    if verb == 'create_entity':
        entity_kind = operation['entity_type']
        if not isinstance(entity_kind, str) or entity_kind not in EDITABLE:
            raise ValueError('该类型不能由知识表单创建。')
        if any(graph.triples((subject, None, None))) or any(graph.triples((None, None, subject))):
            raise ValueError('IRI 已存在，不能覆盖或猜测身份。')
        individual(graph, subject, entity_kind, text(operation['label'], 'label'), 'user_declared')
        provenance(graph, subject, 'create', basis)
        return
    entity_kind = kind(graph, subject)
    if verb == 'set_field':
        name, value = operation['field'], operation['value']
        if not isinstance(name, str): raise ValueError('字段名必须为文字。')
        validate_value(entity_kind, name, value)
        predicate = field_predicate(name)
        graph.remove((subject, predicate, None))
        if value is not None:
            graph.add((subject, predicate, Literal(value, lang='zh') if name == 'label' else Literal(value, datatype=XSD.string)))
    elif verb == 'set_relation':
        name, targets = operation['relation'], operation['targets']
        if not isinstance(name, str) or name not in RELATIONS: raise ValueError('关系只读或未知。')
        source_kind, target_kind, maximum = RELATIONS[name]
        if entity_kind != source_kind or not isinstance(targets, list) or len(targets) > 128:
            raise ValueError('关系来源类型或目标集合不合法。')
        resolved = [iri(target) for target in targets]
        if len(set(resolved)) != len(resolved) or maximum is not None and len(resolved) > maximum:
            raise ValueError('关系目标重复或超过允许数量。')
        if any((target, RDF.type, S[target_kind]) not in graph for target in resolved):
            raise ValueError('关系目标不存在或类型错误。')
        graph.remove((subject, S[name], None))
        for target in resolved: graph.add((subject, S[name], target))
    else:
        name = 'delete'
        if any(graph.triples((None, None, subject))): raise ValueError('请先显式处理引用关系。')
        graph.remove((subject, None, None))
    # Confirmation does not survive an edit of that object's declared knowledge.
    if verb != 'delete_entity' and str(graph.value(subject, S.confirmationStatus)) == 'confirmed':
        graph.set((subject, S.confirmationStatus, Literal('needs_review', datatype=XSD.string)))
    provenance(graph, subject, name, basis)


def apply_operations(graph, operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 256:
        raise ValueError('一次提交需要 1～256 项受控操作。')
    result = deepcopy(graph)
    for operation in operations: _apply(result, operation)
    return result


def inspect_knowledge(graph):
    entities, issues, names = [], [], {}
    for entity in sorted({s for name in EDITABLE for s in graph.subjects(RDF.type, S[name])}, key=str):
        entity_kind = kind(graph, entity)
        label = str(graph.value(entity, RDFS.label) or '')
        names.setdefault(label, []).append(str(entity))
        fields = {name: [str(x) for x in graph.objects(entity, field_predicate(name))]
                  for name in sorted(FIELDS | TYPE_FIELDS.get(entity_kind, set()))}
        relations = {name: [str(x) for x in graph.objects(entity, S[name])]
                     for name, (source_kind, _, _) in RELATIONS.items() if source_kind == entity_kind}
        entities.append({'iri': str(entity), 'entity_type': entity_kind, 'label': label, 'fields': fields,
                         'relations': relations, 'confirmation_status': str(graph.value(entity, S.confirmationStatus) or 'unknown')})
        required = TYPE_FIELDS.get(entity_kind, set()) | set(relations)
        for name in sorted(required):
            if not list(graph.objects(entity, S[name])):
                issues.append({'code': 'unknown_value', 'iri': str(entity), 'field': name, 'message': '尚未登记，请补充有依据的信息。'})
    for label, identities in names.items():
        if label and len(identities) > 1: issues.append({'code': 'ambiguous_label', 'label': label, 'entities': identities})
    changes = [{'iri': str(graph.value(item, S.entityIRI)), 'field': str(graph.value(item, S.field)),
                'basis': str(graph.value(item, S.basis))} for item in sorted(graph.subjects(RDF.type, S.KnowledgeChange), key=str)]
    return {'entities': entities, 'provenance': changes, 'issues': issues, 'execution_readiness': 'not_verified',
            'schema': {'editable_types': sorted(EDITABLE), 'fields': {k: sorted(v | FIELDS) for k, v in TYPE_FIELDS.items()},
                       'relations': {k: {'source_type': a, 'target_type': b, 'max_count': n} for k, (a,b,n) in RELATIONS.items()}}}

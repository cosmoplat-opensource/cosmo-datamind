"""Scoped, explicitly reviewed applicability evidence — DR-061 / DR-062 / DR-065.

Hashes bind declarations to snapshots; neither a signature nor a file proves a
physical measurement. Platform revocations are additional to exported RDF.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import re

from rdflib import Graph, Literal, RDF, RDFS, URIRef, XSD
from rdflib.compare import to_isomorphic

from .authoring import iri, text
from .graph import S, ONTOLOGY

POLICY = 'robotics-governance-v1'
REVIEW_FIELDS = {S.confirmationStatus, S.reviewActor, S.reviewReason, S.reviewedAt, S.reviewedContentHash}
PROTECTED_TYPES = ('AtomicSkill', 'CompositeSkill', 'TaskProcedure', 'SkillInvocation', 'SkillParameter',
                   'ParameterBinding', 'PortContract', 'DataReference', 'SkillCondition',
                   'SkillImplementation', 'ImplementationConfiguration')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(raw):
    try:
        value = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if value.tzinfo is None: raise ValueError('missing timezone')
        return value
    except (TypeError, AttributeError, ValueError) as exc:
        raise ValueError('时间必须为带时区的 ISO 时间。') from exc


def value(graph, entity, prop):
    items = list(graph.objects(entity, S[prop]))
    if len(items) != 1 or not isinstance(items[0], Literal): return ''
    return str(items[0])


def policy(graph):
    entries = list(graph.objects(ONTOLOGY, S.evidencePolicy))
    if not entries: return None
    if len(entries) != 1 or str(entries[0]) != POLICY: raise ValueError('不支持的治理证据策略。')
    return POLICY


def mark_governed(graph):
    policy(graph)
    graph.set((ONTOLOGY, S.evidencePolicy, Literal(POLICY, datatype=XSD.string)))


def graph_hash(graph):
    return hashlib.sha256(str(to_isomorphic(graph).graph_digest()).encode()).hexdigest()


def configuration_hash(graph, implementation, part_type, slot=None):
    # The supported fixed procedure shares resources/configuration across skills.
    # Include executable declarations plus the selected business mapping closure,
    # never audit records, labels, confirmation flags or unrelated business objects.
    seeds = {implementation, part_type}
    if slot is not None: seeds.add(slot)
    for name in PROTECTED_TYPES: seeds.update(graph.subjects(RDF.type, S[name]))
    for entity in list(seeds):
        for predicate in (S.hasPartType, S.hasSlot, S.hasCarrier):
            seeds.update(graph.subjects(predicate, entity))
    pending, visited, scoped = list(seeds), set(), Graph()
    ignored = {RDFS.label, S.alias, S.confirmationStatus, S.evidenceReference, S.reviewActor,
               S.reviewReason, S.reviewedAt, S.reviewedContentHash, S.evidencePolicy}
    while pending:
        entity = pending.pop()
        if entity in visited: continue
        visited.add(entity)
        for predicate, obj in graph.predicate_objects(entity):
            if predicate in ignored: continue
            scoped.add((entity, predicate, obj))
            if predicate != RDF.type and isinstance(obj, URIRef) and any(graph.triples((obj, None, None))):
                if not any((obj, RDF.type, S[k]) in graph for k in ('ReadinessEvidence', 'KnowledgeChange')):
                    pending.append(obj)
    return graph_hash(scoped)


def content_hash(graph, entity):
    snapshot = Graph()
    for triple in graph.triples((entity, None, None)):
        if triple[1] not in REVIEW_FIELDS: snapshot.add(triple)
    return graph_hash(snapshot)


def _scope(graph, spec):
    kinds = {'grasp_compatibility': S.grasp_Subtree_Impl, 'placement_compatibility': S.Place_Subtree_Impl}
    if not isinstance(spec['kind'],str) or spec['kind'] not in kinds: raise ValueError('不支持的适配证据种类。')
    implementation, part = iri(spec['implementation']), iri(spec['part_type'])
    if implementation != kinds[spec['kind']] or (implementation, RDF.type, S.SkillImplementation) not in graph:
        raise ValueError('实现与证据类型不匹配。')
    if (part, RDF.type, S.PartType) not in graph: raise ValueError('证据需要已登记的零件类型。')
    slot = iri(spec['slot']) if spec['slot'] is not None else None
    if spec['kind'] == 'placement_compatibility' and slot is None: raise ValueError('放置证据必须指定槽位。')
    if slot is not None and (slot, RDF.type, S.PlacementSlot) not in graph: raise ValueError('目标槽位不存在。')
    return implementation, part, slot


def register_evidence(graph, evidence, *, mode, now=None):
    from .consumer import check_mode
    check_mode(graph, mode)
    now = utc_now() if now is None else now
    fields = {'iri', 'kind', 'implementation', 'part_type', 'slot', 'reference', 'source_asset_ids', 'valid_until'}
    if not isinstance(evidence, dict) or set(evidence) != fields: raise ValueError('证据字段不完整或含非受信状态/摘要。')
    subject = iri(evidence['iri'])
    if any(graph.triples((subject, None, None))) or any(graph.triples((None, None, subject))):
        raise ValueError('证据标识已存在；请新建证据，不覆盖历史。')
    implementation, part, slot = _scope(graph, evidence)
    reference = text(evidence['reference'], 'reference')
    if reference.startswith(('/', '\\', 'file:')) or re.match(r'^[A-Za-z]:[\\/]', reference):
        raise ValueError('附件需要逻辑来源 ID，不能使用服务器路径。')
    if mode == 'real' and reference.lower().startswith('demo-only:'):
        raise ValueError('真实证据不能使用模拟依据。')
    if mode == 'demo' and not reference.startswith('demo-only:'):
        raise ValueError('模拟证据需要明确 demo-only 标记。')
    if timestamp(evidence['valid_until']) <= timestamp(now): raise ValueError('证据已到期。')
    sources = evidence['source_asset_ids']
    if not isinstance(sources, list) or any(not isinstance(s,str) for s in sources) or len(sources) > 32 or len(set(sources)) != len(sources):
        raise ValueError('附件 ID 集合不合法。')
    for source in sources:
        if not isinstance(source, str) or not re.fullmatch(r'src_[0-9a-f]{32}', source): raise ValueError('附件必须为登记来源 ID。')
    source_hash = value(graph, S.BehaviorTree_Source, 'sha256')
    models_hash = value(graph, S.NodeModels_Source, 'sha256')
    if any(not re.fullmatch('[0-9a-f]{64}', h) for h in (source_hash, models_hash)):
        raise ValueError('缺少可追溯的来源摘要。')
    result = deepcopy(graph)
    mark_governed(result)
    result.add((subject, RDF.type, S.ReadinessEvidence))
    result.add((subject, S.forImplementation, implementation))
    result.add((subject, S.forPartType, part))
    if slot is not None: result.add((subject, S.forSlot, slot))
    for prop, raw in [('evidenceKind', evidence['kind']), ('evidenceReference', reference),
                      ('sourceHash', source_hash), ('modelsHash', models_hash),
                      ('configurationHash', configuration_hash(graph, implementation, part, slot)),
                      ('validUntil', evidence['valid_until']), ('confirmationStatus', 'pending')]:
        result.add((subject, S[prop], Literal(raw, datatype=XSD.string)))
    for source in sources: result.add((subject, S.evidenceSourceId, Literal(source, datatype=XSD.string)))
    return result


def evaluate_evidence(graph, evidence_iri, *, now=None, revocations=(), required_scope=None):
    now = utc_now() if now is None else now
    subject = iri(evidence_iri)
    issues = []
    def fail(code): issues.append({'code': code, 'evidence_iri': str(subject)})
    if (subject, RDF.type, S.ReadinessEvidence) not in graph:
        return {'valid': False, 'issues': [{'code': 'missing_evidence', 'evidence_iri': str(subject)}]}
    status = value(graph, subject, 'confirmationStatus')
    mode = 'demo' if str(graph.value(ONTOLOGY, S.datasetKind)) == 'demo' else 'real'
    expected = 'simulated' if mode == 'demo' else 'confirmed'
    if status != expected: fail('rejected' if status == 'rejected' else 'unreviewed')
    if not value(graph, subject, 'reviewActor') or not value(graph, subject, 'reviewReason'): fail('unreviewed')
    if value(graph, subject, 'reviewedContentHash') != content_hash(graph, subject): fail('review_changed')
    try:
        current = timestamp(now)
        if timestamp(value(graph, subject, 'validUntil')) <= current: fail('expired')
        if timestamp(value(graph, subject, 'reviewedAt')) > current: fail('unreviewed')
    except ValueError: fail('invalid_time')
    if value(graph, subject, 'sourceHash') != value(graph, S.BehaviorTree_Source, 'sha256'): fail('source_changed')
    if value(graph, subject, 'modelsHash') != value(graph, S.NodeModels_Source, 'sha256'): fail('models_changed')
    def scope_entity(prop, optional=False):
        items = list(graph.objects(subject,prop))
        if not items and optional: return None
        if len(items) != 1 or not isinstance(items[0],URIRef):
            raise ValueError('证据范围引用不明确。')
        return items[0]
    try:
        implementation = scope_entity(S.forImplementation)
        part = scope_entity(S.forPartType)
        slot = scope_entity(S.forSlot, optional=True)
    except ValueError:
        fail('scope_mismatch')
        return {'valid':False,'issues':issues}
    actual_scope = {'kind': value(graph, subject, 'evidenceKind'), 'implementation': str(implementation),
                    'part_type': str(part), 'slot': str(slot) if slot is not None else None}
    if required_scope is not None and any(actual_scope.get(k) != v for k,v in required_scope.items()):
        fail('scope_mismatch')
        return {'valid':False,'issues':issues}
    try:
        _scope(graph, actual_scope)
        if value(graph, subject, 'configurationHash') != configuration_hash(graph, implementation, part, slot): fail('scope_changed')
    except ValueError: fail('scope_changed')
    reference = value(graph, subject, 'evidenceReference')
    if not reference or mode == 'real' and reference.lower().startswith('demo-only:'): fail('invalid_reference')
    for event in revocations:
        if event.get('evidence_iri') == str(subject) and event.get('kind') in ('revoked', 'superseded'):
            fail(event['kind'])
    if value(graph, subject, 'invalidatedReason'): fail('scope_changed')
    return {'valid': not issues, 'issues': issues}


def review_evidence(graph, evidence_iri, *, decision, actor, reason, now=None):
    now = utc_now() if now is None else now
    timestamp(now)
    text(actor, 'actor', 256); text(reason, 'reason')
    if decision not in ('accepted', 'rejected'): raise ValueError('审核结论必须为 accepted/rejected。')
    subject = iri(evidence_iri)
    if (subject, RDF.type, S.ReadinessEvidence) not in graph: raise ValueError('证据不存在。')
    result = deepcopy(graph)
    mode = 'demo' if str(graph.value(ONTOLOGY, S.datasetKind)) == 'demo' else 'real'
    for prop, raw in [('reviewActor', actor), ('reviewReason', reason), ('reviewedAt', now),
                      ('reviewedContentHash', content_hash(graph, subject)),
                      ('confirmationStatus', ('simulated' if mode == 'demo' else 'confirmed') if decision == 'accepted' else 'rejected')]:
        result.set((subject, S[prop], Literal(raw, datatype=XSD.string)))
    if decision == 'accepted' and not evaluate_evidence(result, evidence_iri, now=now)['valid']:
        raise ValueError('证据来源、范围或时效已经变化；需要登记新的依据。')
    return result

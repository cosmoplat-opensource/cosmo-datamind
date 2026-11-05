"""RDF semantic differences and explicit source three-way merging — DR-059 / DR-060 / DR-061."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json

from rdflib import BNode, Graph, Literal, RDF, RDFS, XSD
from rdflib.compare import isomorphic, to_canonical_graph

from .authoring import FIELDS, TYPE_FIELDS, RELATIONS, kind, validate_value
from .evidence import configuration_hash, value
from .graph import S, ONTOLOGY


def groups(graph):
    result = defaultdict(set)
    for subject, predicate, obj in graph: result[(subject, predicate)].add(obj)
    return result


def terms(values):
    # n3 retains lexical value, language and datatype, unlike str(literal).
    return sorted(item.n3() for item in values)


def diff_graphs(base, target):
    left, right = groups(to_canonical_graph(base)), groups(to_canonical_graph(target))
    return [{'subject': str(s), 'predicate': str(p), 'before': terms(left.get((s,p), set())),
             'after': terms(right.get((s,p), set()))}
            for s,p in sorted(left.keys() | right.keys(), key=lambda k: tuple(map(str,k)))
            if left.get((s,p), set()) != right.get((s,p), set())]


def _conflict(kind_, subject, predicate, base=(), current=(), incoming=()):
    result = {'kind': kind_, 'subject': str(subject), 'predicate': str(predicate),
              'before': terms(base), 'current': terms(current), 'incoming': terms(incoming)}
    result['id'] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def _identity(graph):
    return {subject: tuple((str(prop), tuple(sorted(str(o) for o in graph.objects(subject, prop))))
                          for prop in (S.stepIndex, S.treeId, S.inProcedure, S.xmlTag, S.invokesSkill))
            for subject in graph.subjects(RDF.type, S.SkillInvocation)}


def _manual_allowed(graph, subject, predicate):
    if subject == ONTOLOGY and predicate == S.evidencePolicy: return True
    if any((subject, RDF.type, S[t]) in graph for t in ('ReadinessEvidence', 'KnowledgeChange')): return True
    try: entity_kind = kind(graph, subject)
    except ValueError: return False
    return predicate in {RDF.type, RDFS.label, S.confirmationStatus} | {
        S[name] for name in FIELDS | TYPE_FIELDS.get(entity_kind, set()) | set(RELATIONS)}


def merge_graphs(base, current, incoming, *, decisions=None):
    decisions = {} if decisions is None else decisions
    if not isinstance(decisions, dict): raise ValueError('冲突决定必须为对象。')
    def blanks(graph):
        result = Graph()
        for triple in graph:
            if any(isinstance(term,BNode) for term in triple): result.add(triple)
        return result
    if not isomorphic(blanks(base), blanks(current)):
        raise ValueError('人工编辑不能改写未识别的空白节点结构。')
    b, l, r = groups(base), groups(current), groups(incoming)
    merged = deepcopy(incoming)
    conflicts, known = [], set()

    def decide(conflict):
        known.add(conflict['id'])
        decision = decisions.get(conflict['id'])
        if decision is None: conflicts.append(conflict)
        elif not isinstance(decision, dict) or not isinstance(decision.get('choice'), str):
            raise ValueError('冲突决定格式不合法。')
        return decision

    if _identity(base) != _identity(incoming) or _identity(base) != _identity(current):
        conflict = _conflict('source_identity', S.PickPlace_Procedure, S.hasStep)
        if decide(conflict) is not None: raise ValueError('步骤身份变化不能按旧序号重放或强制保留。')
    deleted = set()
    for subject in set(base.subjects()):
        if any(current.triples((subject,None,None))) and not any(incoming.triples((subject,None,None))):
            if set(base.predicate_objects(subject)) != set(current.predicate_objects(subject)):
                conflict = _conflict('delete_modify', subject, RDF.type)
                decision = decide(conflict)
                deleted.add(subject)
                if decision is not None:
                    if decision['choice'] not in ('current','incoming'): raise ValueError('删除冲突仅允许保留或删除。')
                    if decision['choice'] == 'current':
                        kind(current, subject)
                        for triple in current.triples((subject,None,None)): merged.add(triple)
    for key in sorted(b.keys() | l.keys(), key=lambda item: tuple(map(str,item))):
        old, manual, source = b.get(key,set()), l.get(key,set()), r.get(key,set())
        if old == manual or manual == source: continue
        if any(isinstance(term, BNode) for term in (*key, *old, *manual)): continue
        subject, predicate = key
        if subject in deleted: continue
        # Read-only structure can only come from the newly validated source.
        if not _manual_allowed(current if manual else base, subject, predicate):
            raise ValueError('人工改动超出知识编辑白名单。')
        if source != old:
            conflict = _conflict('field', subject, predicate, old, manual, source)
            decision = decide(conflict)
            if decision is None or decision['choice'] == 'incoming': continue
            if decision['choice'] == 'manual':
                name = 'label' if predicate == RDFS.label else str(predicate).removeprefix(str(S))
                raw = decision.get('value')
                validate_value(kind(current,subject), name, raw)
                manual = set() if raw is None else {Literal(raw, lang='zh') if name == 'label' else Literal(raw, datatype=XSD.string)}
            elif decision['choice'] != 'current': raise ValueError('未知字段冲突决定。')
        merged.remove((subject,predicate,None))
        for obj in manual: merged.add((subject,predicate,obj))
    # Mapping references cannot silently point at a source-deleted object.
    for name, (_, target_kind, _) in RELATIONS.items():
        for subject, target in merged.subject_objects(S[name]):
            if (target,RDF.type,S[target_kind]) not in merged:
                conflict = _conflict('delete_modify', subject, S[name])
                decision = decide(conflict)
                if decision is not None:
                    if decision['choice'] != 'incoming': raise ValueError('不能保留悬空关系。')
                    merged.remove((subject,S[name],target))
    for item in list(merged.subjects(RDF.type,S.ReadinessEvidence)):
        implementation = merged.value(item,S.forImplementation,any=False)
        part, slot = merged.value(item,S.forPartType,any=False), merged.value(item,S.forSlot,any=False)
        if not value(current,item,'configurationHash'): continue
        changed = (value(merged,item,'configurationHash') != configuration_hash(merged,implementation,part,slot)
                   or value(merged,item,'sourceHash') != value(merged,S.BehaviorTree_Source,'sha256')
                   or value(merged,item,'modelsHash') != value(merged,S.NodeModels_Source,'sha256'))
        if changed:
            conflict = _conflict('evidence_scope',item,S.configurationHash)
            decision = decide(conflict)
            if decision is not None:
                if decision['choice'] != 'invalidate': raise ValueError('范围变化不能强制保留证据有效性。')
                merged.set((item,S.confirmationStatus,Literal('needs_review',datatype=XSD.string)))
                merged.set((item,S.invalidatedReason,Literal('source_or_scope_changed',datatype=XSD.string)))
    if set(decisions) - known: raise ValueError('决定不属于本次输入的冲突。')
    return {'graph': None if conflicts else merged, 'conflicts': conflicts}

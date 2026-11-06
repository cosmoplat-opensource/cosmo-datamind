"""Read-only, task-scoped missing-information projection — DR-061 / DR-066.

Ready means no known completion fields, not a compilation or execution approval.
No values are invented, no confirmation is written, and no asset state is changed.
The save service must re-check the pinned version and task; item IDs are not authority.
"""
import hashlib
import json
import re

from rdflib import Graph, Literal, RDF, RDFS, URIRef, XSD

from . import authoring, evidence
from .consumer import check_mode
from .graph import S, local_name
from .task_dialogue import ENTITY_FIELDS
from .task_model import ModelError, validate_task

LABELS = {
    'hasPartType': '零件类型', 'hasCarrier': '工位的载具关联', 'hasSlot': '载具的放置位关联',
    'hasPerceptionProfile': '感知配置', 'hasPlacementProfile': '放置配置',
    'hasCaptureJoints': '拍照关节配置', 'hasTargetPose': '放置目标位姿',
    'objectName': '识别对象名', 'modelName': '识别模型名', 'threshold': '识别阈值',
    'exposureTime': '曝光时间', 'grasp_compatibility': '抓取适配依据',
    'placement_compatibility': '放置适配依据', 'confirmationStatus': '当前任务知识确认',
}


def _names(graph, entity):
    return {str(entity), local_name(entity), *(str(value) for value in graph.objects(entity, RDFS.label)),
            *(str(value) for value in graph.objects(entity, S.alias))}


def _normalize(text):
    return ''.join(text.split())


class _Projection:
    def __init__(self, graph, task, mode, now, revocations):
        self.graph, self.task, self.mode = graph, task, mode
        self.now = now or evidence.utc_now()
        self.revocations = tuple(revocations)
        self.items = {}
        self.issues = []
        self.configurations = []
        self.expected_status = 'simulated' if mode == 'demo' else 'confirmed'

    def candidates(self, entities):
        return [{'id': str(entity), 'label': str(self.graph.value(entity, RDFS.label) or local_name(entity))}
                for entity in sorted(set(entities), key=str)]

    def add(self, kind, entity, field, reason, **extra):
        key = (kind, str(entity), field)
        identifier = 'gap_' + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]
        label = LABELS.get(field, '拍照关节值' if (entity, RDF.type, S.JointConfiguration) in self.graph else '放置位姿')
        self.items[key] = {'id': identifier, 'kind': kind, 'entity_iri': str(entity), 'field': field,
                           'label': label, 'reason': reason, **extra}

    def choose(self, field, scope=None):
        kind, label, _ = ENTITY_FIELDS[field]
        entries = sorted(set(self.graph.subjects(RDF.type, S[kind])), key=str)
        name = self.task[field]
        found = [entity for entity in entries if name and _normalize(name) in
                 {_normalize(value) for value in _names(self.graph, entity)}]
        exact = [entity for entity in found if str(entity) == name]
        if exact:
            found = exact
        elif scope is not None:
            scoped = [entity for entity in found if entity in scope]
            if scoped:
                found = scoped
        if len(found) == 1:
            return found[0]
        code = 'missing_entity' if not name else 'unknown_entity' if not found else 'ambiguous_entity'
        self.issues.append({'code': code, 'field': field, 'message': f'请明确当前任务的{label}。',
                            'candidates': self.candidates(found or entries)})
        return None

    def confirm(self, entity):
        values = list(self.graph.objects(entity, S.confirmationStatus))
        if len(values) != 1 or str(values[0]) != self.expected_status:
            self.add('confirmation', entity, 'confirmationStatus', '请核对该对象在当前任务中的知识，明确确认后继续。')

    def mapped(self, entity, field, expected_kind):
        targets = list(self.graph.objects(entity, S[field]))
        if (len(targets) == 1 and isinstance(targets[0], URIRef)
                and (targets[0], RDF.type, S[expected_kind]) in self.graph):
            if field in ('hasPerceptionProfile', 'hasPlacementProfile'):
                self.configuration(entity, field, targets[0], expected_kind)
            return targets[0]
        self.add('mapping', entity, field, '缺少唯一且类型正确的关联，请选择或新建对应配置。',
                 candidates=self.candidates(self.graph.subjects(RDF.type, S[expected_kind])))
        return None

    def configuration(self, entity, field, profile, kind):
        """Expose existing task bindings without turning them into missing fields."""
        parameters = []
        if kind == 'PerceptionProfile':
            for name in ('objectName', 'modelName', 'threshold', 'exposureTime'):
                values = list(self.graph.objects(profile, S[name]))
                parameters.append({'field': name, 'label': LABELS[name], 'source_iri': str(profile),
                                   'value': str(values[0]) if len(values) == 1 else None})
            relation, name, label = 'hasCaptureJoints', 'captureJoints', '拍照关节值'
        else:
            relation, name, label = 'hasTargetPose', 'targetPose', '放置位姿'
        targets = list(self.graph.objects(profile, S[relation]))
        target = targets[0] if len(targets) == 1 and isinstance(targets[0], URIRef) else None
        values = list(self.graph.objects(target, S.rawValue)) if target is not None else []
        parameters.append({'field': name, 'label': label, 'source_iri': str(target) if target is not None else None,
                           'value': str(values[0]) if len(values) == 1 else None})
        self.configurations.append({'field': field, 'label': LABELS[field], 'entity_iri': str(entity),
            'entity_label': str(self.graph.value(entity, RDFS.label) or local_name(entity)),
            'profile_iri': str(profile), 'profile_label': str(self.graph.value(profile, RDFS.label) or local_name(profile)),
            'target_kind': kind, 'parameters': parameters,
            'candidates': self.candidates(self.graph.subjects(RDF.type, S[kind]))})

    def parameter(self, entity, field, entity_kind):
        values = list(self.graph.objects(entity, S[field]))
        reason = None
        if len(values) != 1:
            reason = '该参数缺失或有多个值，请提供唯一值。'
        else:
            raw = values[0]
            if (not isinstance(raw, Literal) or raw.datatype not in (None, XSD.string) or raw.language
                    or str(raw).startswith('__UNRESOLVED_') or any(c in str(raw) for c in '{}\n\r\0')):
                reason = '需要实际参数值，不能使用占位符、变量引用或非字符串值。'
            else:
                try:
                    authoring.validate_value(entity_kind, field, str(raw))
                except ValueError as exc:
                    reason = str(exc)
        if reason:
            self.add('parameter', entity, field, reason)

    def profile(self, entity, kind):
        self.confirm(entity)
        # Never interpret the obsolete JSON representation as a second source of truth.
        if any(self.graph.objects(entity, S.profileValues)):
            raise ValueError('该配置仍含旧 profileValues，请先迁移为唯一结构化参数。')
        if kind == 'PerceptionProfile':
            for field in ('objectName', 'modelName', 'threshold', 'exposureTime'):
                self.parameter(entity, field, kind)
            target = self.mapped(entity, 'hasCaptureJoints', 'JointConfiguration')
            target_kind = 'JointConfiguration'
        else:
            target = self.mapped(entity, 'hasTargetPose', 'CartesianPose')
            target_kind = 'CartesianPose'
        if target is not None:
            self.parameter(target, 'rawValue', target_kind)

    def applicability(self, part_type, slot, kind, implementation):
        required = {'kind': kind, 'implementation': str(implementation), 'part_type': str(part_type)}
        if kind == 'placement_compatibility':
            required['slot'] = str(slot)
        subjects = sorted(set(self.graph.subjects(RDF.type, S.ReadinessEvidence)), key=str)
        relevant = [subject for subject in subjects if
                    (subject, S.forImplementation, implementation) in self.graph and
                    (subject, S.forPartType, part_type) in self.graph]
        reasons = []
        governed = evidence.policy(self.graph)
        for subject in relevant:
            if governed:
                outcome = evidence.evaluate_evidence(self.graph, str(subject), now=self.now,
                    revocations=self.revocations, required_scope=required)
                if outcome['valid']:
                    return
                reasons.extend(item['code'] for item in outcome['issues'])
            elif self.legacy_evidence_valid(subject, slot, kind):
                return
        entity = slot if kind == 'placement_compatibility' else part_type
        self.add('applicability', entity, kind, '当前范围缺少有效适配依据，请提供实际依据、有效期并明确确认。',
                 scope=required, issue_codes=sorted(set(reasons)) or ['missing_valid_evidence'])

    def legacy_evidence_valid(self, subject, slot, kind):
        """Respect the existing legacy OWL gate with the inspection's explicit clock."""
        value = lambda prop: evidence.value(self.graph, subject, prop)
        source_hash = evidence.value(self.graph, S.BehaviorTree_Source, 'sha256')
        if not re.fullmatch(r'[0-9a-f]{64}', source_hash):
            return False
        if kind == 'placement_compatibility' and (subject, S.forSlot, slot) not in self.graph:
            return False
        if (value('evidenceKind') != kind or value('confirmationStatus') != self.expected_status
                or value('evidenceReference').strip() in ('', 'None')
                or value('sourceHash') != source_hash):
            return False
        if any(event.get('evidence_iri') == str(subject) and event.get('kind') in ('revoked', 'superseded')
               for event in self.revocations):
            return False
        try:
            return evidence.timestamp(value('validUntil')) > evidence.timestamp(self.now)
        except ValueError:
            return False

    def run(self):
        part, station = self.choose('part'), self.choose('station')
        carriers = set(self.graph.objects(station, S.hasCarrier)) if station is not None else set()
        if self.task['carrier'] is None:
            slots = {slot for parent in carriers for slot in self.graph.objects(parent, S.hasSlot)}
            slot = self.choose('slot', slots)
            # The existing grounding allows only this relationship-based inference.
            parents = [entity for entity in carriers if (entity, S.hasSlot, slot) in self.graph
                       and (entity, RDF.type, S.Carrier) in self.graph]
            if len(parents) == 1:
                carrier = parents[0]
            else:
                carrier = self.choose('carrier', carriers)
        else:
            carrier = self.choose('carrier', carriers)
            slots = set(self.graph.objects(carrier, S.hasSlot)) if carrier is not None else set()
            slot = self.choose('slot', slots)
        if self.issues:
            return 'needs_clarification'
        for source, field, target, target_kind in (
            (station, 'hasCarrier', carrier, 'Carrier'), (carrier, 'hasSlot', slot, 'PlacementSlot'),
        ):
            if (source, S[field], target) not in self.graph:
                self.add('mapping', source, field, '当前任务选定的对象尚未建立此关联，请确认后补齐。',
                         candidates=self.candidates(self.graph.subjects(RDF.type, S[target_kind])))
        for entity in (part, station, carrier, slot):
            self.confirm(entity)
        part_type = self.mapped(part, 'hasPartType', 'PartType')
        perception = None
        if part_type is not None:
            self.confirm(part_type)
            perception = self.mapped(part_type, 'hasPerceptionProfile', 'PerceptionProfile')
            if perception is not None:
                self.profile(perception, 'PerceptionProfile')
        placement = self.mapped(slot, 'hasPlacementProfile', 'PlacementProfile')
        if placement is not None:
            self.profile(placement, 'PlacementProfile')
        # No applicability form until its dependent mappings and values are known.
        if part_type is not None and not any(item['kind'] in ('mapping', 'parameter') for item in self.items.values()):
            self.applicability(part_type, slot, 'grasp_compatibility', S.grasp_Subtree_Impl)
            self.applicability(part_type, slot, 'placement_compatibility', S.Place_Subtree_Impl)
        return 'needs_information' if self.items else 'ready'


def inspect_gaps(graph, task, *, mode, now=None, revocations=()):
    result = {'status': 'error', 'items': [], 'execution_readiness': 'not_verified', 'issues': [], 'configurations': []}
    try:
        task = validate_task(task)
        if not isinstance(graph, Graph):
            raise ValueError('需要固定版本的 RDF 图。')
        check_mode(graph, mode)
        if task['operation'] == 'unsupported' or task['unsupported_requests']:
            result['status'] = 'unsupported'
            return result
        if task['operation'] is None:
            result.update(status='needs_clarification', issues=[{
                'code': 'missing_operation', 'field': 'operation', 'message': '请明确要生成的任务类型。'}])
            return result
        projection = _Projection(graph, task, mode, now, revocations)
        result['status'] = projection.run()
        result['issues'] = projection.issues
        if result['status'] != 'needs_clarification':
            result['items'] = list(projection.items.values())
            result['configurations'] = projection.configurations
    except (ValueError, TypeError, KeyError) as exc:
        result['issues'] = [{'code': exc.code if isinstance(exc, ModelError) else 'invalid_knowledge',
                             'message': str(exc)}]
    return result

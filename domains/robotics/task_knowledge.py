"""Inspect incomplete candidates and apply only task-scoped answers — DR-061 / DR-066.

Inspection tolerates missing editable knowledge, never damaged executable semantics.
Strict consumption still uses the unmodified consumer/SHACL contract.
"""
from copy import deepcopy
import uuid

from rdflib import Literal, Namespace, RDF, RDFS, URIRef, XSD

from . import authoring, consumer, evidence
from .artifact_contract import graph_validation
from .graph import S
from .semantics import xml_from_graph, node_models_from_graph, configuration_paths
from .task_completion import inspect_gaps
from .validator import validate_tree

ORIGIN = '本地会话提交；用户明确操作，不是已认证个人签名或现场执行记录'
SH = Namespace('http://www.w3.org/ns/shacl#')


def inspect_structure(graph, mode):
    consumer.check_mode(graph, mode)
    for implementation in (S.grasp_Subtree_Impl, S.Place_Subtree_Impl):
        if (implementation, RDF.type, S.SkillImplementation) not in graph:
            raise ValueError('缺少当前模板的技能实现声明。')
    conforms, report, detail = graph_validation(graph)
    if not conforms:
        repairable = {S[name] for name in ('objectName', 'modelName', 'threshold', 'exposureTime',
            'rawValue', 'hasCaptureJoints', 'hasTargetPose', 'confirmationStatus')}
        results = list(report.subjects(RDF.type, SH.ValidationResult))
        if not results:
            raise ValueError('本体结构不完整。')
        for result in results:
            focus, path = report.value(result, SH.focusNode), report.value(result, SH.resultPath)
            if path not in repairable or not any((focus, RDF.type, S[t]) in graph for t in authoring.EDITABLE):
                raise ValueError('本体执行结构或发布契约不兼容，不能通过补参数修复。')
    template = xml_from_graph(graph)
    errors = validate_tree(template, node_models_from_graph(graph), baseline=template,
                           config_paths=configuration_paths(graph))
    if any(item['code'] != 'unresolved_parameter' for item in errors):
        raise ValueError('本体执行结构或数据流不兼容。')
    return graph


def create_entity(graph, spec, expected_kind):
    if not isinstance(spec, dict) or set(spec) != {'kind', 'label'} or spec['kind'] != expected_kind:
        raise ValueError('请选择当前缺口对应的对象类型，只填写名称。')
    identity = URIRef('urn:datamind:robotics:' + uuid.uuid4().hex)
    changed = authoring.apply_operations(graph, [{'op': 'create_entity', 'iri': str(identity),
        'entity_type': expected_kind, 'label': spec['label'], 'basis': ORIGIN}])
    # The light task form has no language selector; keep its literal exactly as entered.
    changed.set((identity, RDFS.label, Literal(spec['label'])))
    return changed, identity


def pending_answer(graph, item, *, revocations=()):
    """Project a uniquely matching saved basis; never preselect its confirmation."""
    drafts = []
    for subject in graph.subjects(RDF.type, S.ReadinessEvidence):
        if evidence.value(graph, subject, 'confirmationStatus') != 'pending':
            continue
        outcome = evidence.evaluate_evidence(graph, str(subject), revocations=revocations,
                                             required_scope=item['scope'])
        # Pending evidence intentionally lacks a review timestamp/content hash.
        # Source/scope/configuration/revocation failures still prevent reuse.
        if any(i['code'] not in {'unreviewed', 'review_changed', 'invalid_time'} for i in outcome['issues']):
            continue
        expiry = evidence.value(graph, subject, 'validUntil')
        try:
            if evidence.timestamp(expiry) <= evidence.timestamp(evidence.utc_now()):
                continue
        except ValueError:
            continue
        drafts.append({'reference': evidence.value(graph, subject, 'evidenceReference'),
            'valid_until': expiry, 'confirmed': False,
            'source_asset_ids': sorted(str(v) for v in graph.objects(subject, S.evidenceSourceId))})
    # Multiple saved alternatives require a fresh user choice, not an arbitrary default.
    return drafts[0] if len(drafts) == 1 else None


def replace_configuration(graph, task, *, field, mode, target=None, create=None, revocations=()):
    """DR-066: explicitly replace only a resolved task's current profile binding."""
    projection = inspect_gaps(graph, task, mode=mode, revocations=revocations)
    item = next((row for row in projection['configurations'] if row['field'] == field), None)
    if item is None:
        raise ValueError('请先明确任务对象；只能更换当前任务已关联的感知配置或放置配置。')
    if (target is None) == (create is None):
        raise ValueError('请选择其他已有配置或明确新建配置。')
    changed = graph
    if create is not None:
        changed, selected = create_entity(graph, create, item['target_kind'])
    else:
        selected = authoring.iri(target)
        if str(selected) == item['profile_iri']:
            raise ValueError('当前已使用该配置，请选择其他配置。')
    return authoring.apply_operations(changed, [{'op': 'set_relation', 'iri': item['entity_iri'],
        'relation': field, 'targets': [str(selected)], 'basis': ORIGIN + '；主动更换当前任务配置'}])


def apply_answers(graph, task, answers, *, mode, revocations=()):
    if not isinstance(answers, list) or not 1 <= len(answers) <= 256:
        raise ValueError('请至少填写一项当前缺少的信息。')
    projection = inspect_gaps(graph, task, mode=mode, revocations=revocations)
    permitted = {item['id']: item for item in projection['items']}
    seen = set()
    for answer in answers:
        if not isinstance(answer, dict) or not isinstance(answer.get('gap_id'), str):
            raise ValueError('缺口答案格式不正确。')
        key = answer['gap_id']
        if key not in permitted or key in seen:
            raise ValueError('该表单已失效、重复或不属于当前任务，请刷新后重试。')
        seen.add(key)
    changed, selected = deepcopy(graph), deepcopy(task)
    attachments = set()
    # Values/relations first; confirmations and evidence must refer to the resulting scope.
    order = {'mapping': 0, 'parameter': 1, 'confirmation': 2, 'applicability': 3}
    for answer in sorted(answers, key=lambda a: order[permitted[a['gap_id']]['kind']]):
        item = permitted[answer['gap_id']]
        subject, field, kind = URIRef(item['entity_iri']), item['field'], item['kind']
        keys = set(answer) - {'gap_id'}
        if kind == 'mapping':
            if keys not in ({'target'}, {'create'}):
                raise ValueError('关联仅接受已有对象选择或明确新建。')
            expected = authoring.RELATIONS[field][1]
            if 'create' in answer:
                changed, target = create_entity(changed, answer['create'], expected)
            else:
                target = authoring.iri(answer['target'])
            targets = {target}
            if authoring.RELATIONS[field][2] is None:
                targets.update(changed.objects(subject, S[field]))
            changed = authoring.apply_operations(changed, [{'op': 'set_relation', 'iri': str(subject),
                'relation': field, 'targets': sorted(map(str, targets)), 'basis': ORIGIN}])
            if field in ('hasCarrier', 'hasSlot'):
                selected['carrier' if field == 'hasCarrier' else 'slot'] = str(target)
        elif kind == 'parameter':
            if keys != {'value'} or not isinstance(answer['value'], str) or not answer['value'].strip():
                raise ValueError('请填写实际参数，不能留空或提供控制字段。')
            if any(c in answer['value'] for c in '{}\n\r\0') or answer['value'].startswith('__UNRESOLVED_'):
                raise ValueError('请填写实际参数，不能填写变量或占位符。')
            changed = authoring.apply_operations(changed, [{'op': 'set_field', 'iri': str(subject),
                'field': field, 'value': answer['value'], 'basis': ORIGIN}])
            # A pose/joint edit changes its profile's declared configuration as well.
            for relation in ('hasCaptureJoints', 'hasTargetPose'):
                for parent in changed.subjects(S[relation], subject):
                    changed.set((parent, S.confirmationStatus, Literal('needs_review', datatype=XSD.string)))
        elif kind == 'confirmation':
            if keys != {'confirmed'} or type(answer['confirmed']) is not bool:
                raise ValueError('知识确认必须是明确的勾选。')
            current = inspect_gaps(changed, selected, mode=mode, revocations=revocations)
            if not any(g['id'] == item['id'] for g in current['items']):
                # An upstream mapping changed the scope: do not confirm its former dependency.
                continue
            if answer['confirmed']:
                changed.set((subject, S.confirmationStatus, Literal(
                    'simulated' if mode == 'demo' else 'confirmed', datatype=XSD.string)))
                authoring.provenance(changed, subject, 'confirmation', ORIGIN + '；用户已核对当前任务知识')
        else:
            if not {'reference', 'valid_until', 'confirmed'} <= keys or keys - {
                'reference', 'valid_until', 'confirmed', 'source_asset_ids'} or type(answer['confirmed']) is not bool:
                raise ValueError('适配需要依据、有效期及明确确认，范围和摘要由系统计算。')
            latest = inspect_gaps(changed, selected, mode=mode, revocations=revocations)
            current = next((g for g in latest['items'] if g['id'] == item['id']), None)
            if current is None or current.get('scope') != item.get('scope'):
                raise ValueError('适配范围已变化，请重新核对当前范围。')
            scope = current['scope']
            identity = 'urn:datamind:robotics-evidence:' + uuid.uuid4().hex
            ids = answer.get('source_asset_ids', [])
            spec = {'iri': identity, 'kind': scope['kind'], 'implementation': scope['implementation'],
                'part_type': scope['part_type'], 'slot': scope.get('slot'), 'reference': answer['reference'],
                'valid_until': answer['valid_until'], 'source_asset_ids': ids}
            changed = evidence.register_evidence(changed, spec, mode=mode)
            attachments.update(ids)
            if answer['confirmed']:
                changed = evidence.review_evidence(changed, identity, decision='accepted',
                    actor='local_session', reason=ORIGIN + '；用户明确确认上述实际依据适用当前范围')
    evidence.mark_governed(changed)
    return changed, selected, sorted(attachments)

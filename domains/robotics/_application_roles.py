"""Explicit confirmed role semantics, independent of labels — DR-072."""
from copy import deepcopy
from rdflib import RDF, RDFS, OWL, XSD
from rdflib.collection import Collection
from .graph import schema as vocabulary, S
from .modeling.common import checked_tbox, digest

CLASSES = {'Skill','AtomicSkill','CompositeSkill','TaskProcedure','SkillImplementation','PortContract',
           'SkillParameter','SkillInvocation','ParameterBinding','DataReference','ImplementationConfiguration'}
RELATIONS = {'hasStep','invokesSkill','hasInput','hasOutput','implementedBy','hasPortContract','mapsToPort',
             'hasBinding','bindsParameter','sourceParameter','sourceData','targetData','parameterData',
             'hasFixedConfiguration','hasFixedBinding'}
DATA = {'parameterName','direction','cppType','bindingDirection','literalValue','scope','dataKey','stepIndex'}
TASK_CLASSES = {'Workpiece','PartType','Workstation','Carrier','PlacementSlot','PerceptionProfile',
                'PlacementProfile','JointConfiguration','CartesianPose'}
TASK_RELATIONS = {'hasPartType','hasCarrier','hasSlot','hasPerceptionProfile','hasPlacementProfile',
                  'hasCaptureJoints','hasTargetPose'}


def issue(code, message, *, expected='满足经确认的应用角色或执行契约', **extra):
    return {'code':code, 'layer':code.split('.')[0], 'message':message,
            'expected':expected, 'observed':deepcopy(extra), **extra}


def role_requirement(kind, role, standard):
    """Named contract semantics for diagnosis and editable TBox repair suggestions."""
    result = {'kind': kind, 'role': role, 'label': str(standard.value(S[role], RDFS.label) or role)}
    def members(predicate):
        values = set()
        for term in standard.objects(S[role], predicate):
            head = standard.value(term, OWL.unionOf)
            values.update(Collection(standard, head) if head else [term])
        return sorted('owl:Thing' if t == OWL.Thing else str(t).rsplit('#', 1)[-1] for t in values)
    if kind == 'classes':
        result['parent_ids'] = members(RDFS.subClassOf)
    else:
        result['domain_ids'] = members(RDFS.domain)
        if kind == 'object_properties':
            result['range_ids'] = members(RDFS.range)
        else:
            datatype = standard.value(S[role], RDFS.range)
            result['datatype'] = 'xsd:' + str(datatype).rsplit('#', 1)[-1] if datatype else ''
    return result


def resolve_roles(*, tbox, role_map, required_for='execution'):
    issues=[]; roles={k:{} for k in ('classes','object_properties','data_properties')}
    standard = vocabulary()
    kind_names = {'classes': '类别', 'object_properties': '关系', 'data_properties': '数据属性'}
    def conflict(**extra):
        kind, role = extra.get('kind'), extra.get('role')
        if kind in kind_names and isinstance(role, str):
            expected = role_requirement(kind, role, standard)
            message = f"{kind_names[kind]}角色 {role}（{expected['label']}）的对应或语义与确认骨架不一致，请核对类别层次、关系方向及属性类型。"
            issues.append(issue('schema.role_conflict', message, expected=expected, **extra))
        else:
            issues.append(issue('schema.role_conflict','角色映射或语义与确认骨架不一致。',**extra))
    try:
        tbox=checked_tbox(tbox)
        if (required_for not in ('execution','task') or not isinstance(role_map,dict)
            or role_map.get('version')!='1' or role_map.get('confirmed') is not True
            or role_map.get('tbox_sha256')!=digest(tbox)):
            raise ValueError('unconfirmed')
        terms={k:{tbox['namespace']+r['id']:r for r in tbox[k]} for k in roles}
        seen=set()
        for binding in role_map['bindings']:
            kind,role,iri=binding['kind'],binding['role'],binding['iri']
            if kind not in roles or iri not in terms[kind] or role in roles[kind] or (kind,iri) in seen:
                conflict(role=role,kind=kind); continue
            roles[kind][role]=iri; seen.add((kind,iri))
        reverse={iri[len(tbox['namespace']):]:role for kind in roles for role,iri in roles[kind].items()}
        classes={r['id']:r for r in tbox['classes']}
        def parents(name):
            pending=[name]; result=set()
            while pending:
                node=pending.pop()
                if node in result: continue
                result.add(node); pending.extend(classes.get(node,{}).get('parent_ids',[]))
            return {reverse.get(n,n) for n in result}
        for role,iri in roles['classes'].items():
            expected={str(v).split('#')[-1] for v in standard.objects(S[role],RDFS.subClassOf)}
            if not expected <= parents(iri[len(tbox['namespace']):]): conflict(role=role,kind='classes')
        for kind,predicate in [('object_properties',OWL.ObjectProperty),('data_properties',OWL.DatatypeProperty)]:
            for role,iri in roles[kind].items():
                if (S[role],RDF.type,predicate) not in standard: continue
                row=terms[kind][iri]
                for field,prop in [('domain_ids',RDFS.domain),('range_ids',RDFS.range)]:
                    if kind=='data_properties' and field=='range_ids':
                        if str(standard.value(S[role],prop))!=str(XSD[row['datatype'].split(':')[-1]]): conflict(role=role,kind=kind)
                        continue
                    expected=set()
                    for term in standard.objects(S[role],prop):
                        head=standard.value(term,OWL.unionOf)
                        expected.update(Collection(standard,head) if head else [term])
                    names={str(t).split('#')[-1] for t in expected}
                    if 'Thing' not in names and names and (not row[field] or any(not (parents(n)&names) for n in row[field])):
                        conflict(role=role,kind=kind)
        required={'classes':CLASSES | (TASK_CLASSES if required_for=='task' else set()),
                  'object_properties':RELATIONS | (TASK_RELATIONS if required_for=='task' else set()),'data_properties':DATA}
        for kind,values in required.items():
            for role in sorted(values-set(roles[kind])):
                expected = role_requirement(kind, role, standard)
                issues.append(issue('schema.missing_role',
                    f"缺少{kind_names[kind]}角色对应：{role}（{expected['label']}）。请核对已有术语；没有对应项时修订本体骨架。",
                    expected=expected, kind=kind, role=role))
    except (ValueError,KeyError,TypeError): conflict()
    return {'status':'blocked' if issues else 'passed','issues':issues,'roles':deepcopy(roles)}

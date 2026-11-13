"""Read-only robotics diagnostics and registered explicit repair navigation — DR-072."""
import json
from urllib.parse import urlencode

from .models import require, ContractError


def row(code,status='passed',issues=None):
    return [{'check_code':code,'status':status,'applicable':True,'issues':issues or []}]


def issue(code,layer,message,*,entity=None,evidence=None,actions=None,expected=None,observed=None):
    return {'code':code,'layer':layer,'severity':'blocking','message':message,
        'expected':expected if expected is not None else '满足当前行为树应用角色及执行要求',
        'observed':observed,'entity_refs':entity or [],'evidence_refs':evidence or [],'repair_actions':actions or []}


def action(action_id,label,target):
    return {'action_id':action_id,'label':label,'kind':'navigate','availability':'available','target':target}


def schema(data):
    info=data['environment'].get('robotics',{});doc=info.get('design')
    origin=info.get('application_origin')
    if origin and data['task']:
        from domains.robotics.application_adaptation import resolve_roles
        problems=resolve_roles(tbox=origin['tbox'],role_map=origin['role_map'],required_for='task')['issues']
        target={'resource_kind':'design_session','resource_id':origin['session_ref']['id'],'expected_revision':info['design_revision']}
        return row('schema.payload','missing' if problems else 'passed',
            [issue(p['code'],'schema',p['message'],entity=[origin['tbox']['namespace']+p.get('role','')],
                expected=p.get('expected'),observed=p.get('observed'),
                evidence=[origin['role_map']['tbox_sha256']],actions=[action('robotics.revise_tbox','修订本体骨架',target)]) for p in problems])
    if not doc:return row('schema.payload')
    from domain_runs.robotics_application_service import proposal, diagnosis
    tbox=doc['state']['schema_confirmation']['tbox']; problems=diagnosis(tbox,proposal(tbox))
    target={'resource_kind':'design_session','resource_id':doc['session_ref']['id'],'expected_revision':info['design_revision']}
    ref={k:data['snapshots'][0][k] for k in ('ontology_id','version_id')}
    mapping_target={'resource_kind':'ontology_version','resource_id':ref['version_id'],'expected_revision':0,
        **ref,'mode':data['context']['mode']}
    problems=[issue(p['code'],'schema',p['message'],entity=[tbox['namespace']+p.get('role','')],
        expected=p.get('expected'),observed=p.get('observed'),
        evidence=[doc['state']['schema_confirmation']['sha256']],
        actions=[action('robotics.revise_tbox','修订本体骨架',target),
                 action('robotics.adapt_execution','核对角色对应',mapping_target)]) for p in problems]
    return row('schema.payload','missing' if problems else 'passed',problems)


def execution(data):
    from domains.robotics.task_knowledge import inspect_structure
    from rdflib import Graph
    try:
        inspect_structure(Graph().parse(data=data['snapshots'][0]['payload'],format='xml'),data['context']['mode'])
        return row('execution.consumer')
    except ValueError:
        info=data['environment'].get('robotics',{});doc=info.get('design')
        problems=[issue('execution.consumer_incompatible','execution','当前本体未满足现有行为树消费者要求。')]
        if doc:
            ref={k:data['snapshots'][0][k] for k in ('ontology_id','version_id')}
            target={'resource_kind':'ontology_version','resource_id':ref['version_id'],'expected_revision':0,
                **ref,'mode':data['context']['mode']}
            individual=next(iter(doc['abox']['individuals']),
                {'id':'','source_refs':[doc['state']['schema_confirmation']['sha256']]})
            problems.append(issue('execution.missing_metadata','execution','通用设计尚缺行为树执行字段，请核对角色并适配执行结构。',
                entity=[doc['state']['schema_confirmation']['tbox']['namespace']+'individual/'+individual['id']],
                evidence=individual['source_refs'],actions=[action('robotics.adapt_execution','补齐执行结构',target)]))
        return row('execution.consumer','unsupported',problems)


def task(data):
    from rdflib import Graph
    from domains.robotics.task_completion import inspect_gaps
    graph=Graph().parse(data=data['snapshots'][0]['payload'],format='xml')
    result=inspect_gaps(graph,data['task']['content'],mode=data['context']['mode'],
        revocations=data['environment'].get('revocations',[[]])[0])
    gaps=[g for g in result['items'] if not (g['kind']=='applicability' and data['context']['generation_policy']=='offline')]
    target={'resource_kind':'robotics_task','resource_id':data['task']['session_id'],'expected_revision':data['task']['revision']}
    problems=[issue('task.missing_'+('profile' if g['kind']=='mapping' else 'value'),'task',g.get('reason') or '请补齐任务信息。',
        entity=[g['entity_iri']],evidence=[],actions=[action('robotics.complete_task','补齐任务信息',target)]) for g in gaps]
    if result['status'] not in ('ready','needs_information') and not problems:
        problems=[issue('task.invalid_knowledge','task','当前任务知识不兼容，请核对对象。')]
    return row('task.configuration','missing' if problems else 'passed',problems)


def repair_stage(issues, state):
    """Use every gap, including old report entity refs; never skip class confirmation."""
    if not state.get('class_confirmation') or not issues:
        return 'classes'
    vocabulary = state.get('vocabulary', {})
    known = {state['namespace'] + term['id']: kind
        for kind in ('classes', 'object_properties', 'data_properties') for term in vocabulary.get(kind, [])}
    for problem in issues:
        expected, observed = problem.get('expected'), problem.get('observed')
        kind = expected.get('kind') if isinstance(expected, dict) else None
        if kind is None and isinstance(observed, dict):
            kind = observed.get('kind')
        if kind is None:
            matches = {known[ref] for ref in problem.get('entity_refs', []) if ref in known}
            kind = matches.pop() if len(matches) == 1 else None
        if kind not in ('object_properties', 'data_properties'):
            return 'classes'
    return 'properties'


def repair(command,conn,root):
    require(command['input']=={})
    kind=command['action_id'];target=command['target']; context=command['return_context']
    if kind=='robotics.revise_tbox':
        from domain_runs.robotics_design_service import DesignService
        record = DesignService(root, read_only=True)._get(conn, target['resource_id'])
        report = json.loads(conn.execute('SELECT report FROM application_checks WHERE check_id=?',
                                        (command['check_id'],)).fetchone()[0])
        problems = [i for i in report['issues'] if i['layer'] == 'schema' and any(
            a['action_id'] == kind and a['target']['resource_id'] == target['resource_id'] for a in i['repair_actions'])]
        phase = {'session_id': target['resource_id'], 'expected_revision': target['expected_revision'],
                 'current_stage': record['state']['stage'], 'stage': repair_stage(problems, record['state'])}
        href='#build?'+urlencode({'recipe':'robotics','session':target['resource_id'],'check':command['check_id']})
        return {'kind':'navigate','target':target,'href':href,'return_context':context,'design_repair':phase}
    elif kind=='robotics.complete_task':
        ref=context['asset_refs'][0]
        href='#robotics-task?'+urlencode({'asset':ref['ontology_id'],'version':ref['version_id'],'session':target['resource_id']})
    elif kind=='robotics.adapt_execution':
        from domain_runs.robotics_application_service import ApplicationService, ApplicationError
        from .models import digest
        task_ref=None if context['task_ref'] is None else {'session_id':context['task_ref']['session_id']}
        try:
            state=ApplicationService(root,read_only=True)._prepare(conn,ontology_id=target['ontology_id'],version_id=target['version_id'],
                mode=target['mode'],task_ref=task_ref,request_id='repair-'+digest(command['request_id']),automatic=True)
        except ApplicationError as exc:
            raise ContractError('repair_unavailable',str(exc)) from None
        except ValueError:
            raise ContractError('repair_unavailable','本体来源或匹配规则无效，请刷新检查并核对固定版本。') from None
        target={'resource_kind':'robotics_adaptation','resource_id':state['adaptation_id'],'expected_revision':state['revision']}
        href='#robotics-task?'+urlencode({'adaptation':state['adaptation_id']})
    else:raise ContractError('unknown_action','修复动作不存在。')
    return {'kind':'navigate','target':target,'href':href,'return_context':context}


def resource_revision(root,target):
    from ontology_assets.storage import Storage
    with Storage(root,read_only=True).transaction() as conn:
        kind=target['resource_kind'];id_=target['resource_id']
        if kind=='ontology_version':
            require(conn.execute('SELECT version_id FROM versions WHERE version_id=?',(id_,)).fetchone() is not None,'not_found')
            return 0
        if kind in ('design_session','robotics_task','robotics_adaptation'):
            table,key={'design_session':('design_sessions','session_id'),'robotics_task':('robotics_task_sessions','session_id'),
                       'robotics_adaptation':('robotics_adaptations','adaptation_id')}[kind]
            row_=conn.execute(f'SELECT record FROM {table} WHERE {key}=?',(id_,)).fetchone()
            require(row_ is not None,'not_found');data=json.loads(row_[0])
            return data['state']['revision'] if kind=='design_session' else data['revision']
    raise ContractError('repair_unavailable','该资源暂不可修复。')

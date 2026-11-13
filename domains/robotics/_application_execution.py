"""Source-located execution extraction, scoped static configuration tracing — DR-072."""
from copy import deepcopy
from .modeling.common import digest
from .modeling.evidence import source_digests
from .modeling._extractor import _xml_roots
from .profiles.ur_pick_place import COMPOSITES
from .source_validation import validate_sources, SourceValidationError
from .xml_contract import VAR
from ._application_roles import resolve_roles, issue


def extract_execution(*, confirmation, evidence_bundle, role_map):
    problems=[]
    def fail(code,message,*nodes):
        refs=sorted({refs_by_node[n] for n in nodes if n in refs_by_node})
        problems.append(issue(code,message,source_refs=refs))
    def output(): return {'status':'blocked','issues':problems,'execution':None}
    try:
        tbox=confirmation['tbox']
        if (confirmation['sha256']!=digest(tbox) or not confirmation.get('vocabulary_sha256')
            or confirmation['source_sha256']!=source_digests(evidence_bundle)): raise ValueError('snapshot')
        checked=resolve_roles(tbox=tbox,role_map=role_map)
        if checked['status']!='passed': return {**checked,'execution':None,'status':'blocked'}
        roots,refs_by_node=_xml_roots(evidence_bundle['evidence'])
        # XML comments/PIs are provenance, not executable calls.
        for document_root in roots:
            for parent in list(document_root.iter()):
                for child in list(parent):
                    if not isinstance(child.tag,str): parent.remove(child)
        documents={d['id']:d['role'] for d in evidence_bundle['documents']}
        by_id={e['id']:e for e in evidence_bundle['evidence']}
        roots_by_role={documents[by_id[refs_by_node[r]]['locations'][0]['document_id']]:r for r in roots}
        root,models=roots_by_role['behavior_tree'],roots_by_role['node_models']
        if set(root.attrib)!={'BTCPP_format','main_tree_to_execute'}:
            raise ValueError('root facts changed')
    except (ValueError,KeyError,TypeError):
        return {'status':'blocked','issues':[issue('execution.source_integrity','来源或确认快照无效。')],'execution':None}
    trees=root.findall('BehaviorTree'); ids=[t.get('ID') for t in trees]
    if not all(ids) or len(ids)!=len(set(ids)) or root.get('main_tree_to_execute') not in ids:
        fail('execution.missing_metadata','流程标识或主流程不唯一。',root,*trees); return output()
    for tree in trees:
        if len(tree)!=1 or tree[0].tag!='Sequence' or any(len(n) for n in tree[0]):
            fail('execution.unsupported_template','仅支持单层 Sequence。',tree)
        for n in tree.iter('SubTree'):
            if n.get('ID') not in COMPOSITES: fail('execution.unsupported_template','子树调用不在固定应用范围。',n)
    if problems: return output()
    public=[]
    # Public fields are the fixed application interface, never inferred from labels.
    for skill,(_name,inputs,_) in COMPOSITES.items():
        for parameter in inputs:
            if parameter=='obj_in_robot': continue
            sites=[(tree,i,n) for tree in trees for i,n in enumerate(tree[0],1) if n.tag=='SubTree' and n.get('ID')==skill]
            if len(sites)!=1:
                fail('execution.binding_ambiguous','公开参数需要唯一调用位置。',root); continue
            tree,index,node=sites[0]; raw=node.get(parameter,''); owner=tree.get('ID'); port=parameter
            match=VAR.fullmatch(raw)
            if match:
                setters=[(i,n) for i,n in enumerate(tree[0],1) if n.tag=='SetBlackboard' and n.get('output_key')==match.group(1)]
                if len(setters)!=1 or setters[0][0]>=index or VAR.fullmatch(setters[0][1].get('value','')):
                    fail('execution.binding_ambiguous','公开配置缺少唯一前序静态赋值。',node,*[n for _,n in setters]); continue
                index,node=setters[0]; raw=node.get('value',''); port='value'
            public.append({'skill':skill,'parameter':parameter,'value':raw,
                'binding_id':f'{owner}/{index}/{port}','invocation_id':f'{owner}/{index}',
                'source_refs':[refs_by_node[node]],'confirmation_status':'source_extracted','unit_status':'unconfirmed'})
    if problems: return output()
    try: validate_sources(root,models)
    except SourceValidationError as exc:
        codes={r['code'] for r in exc.issues}
        code='execution.type_conflict' if codes & {'model_conflict','invalid_port'} else 'execution.missing_order'
        fail(code,'接口契约或固定调用顺序不一致。',root,models)
        return output()
    model_map={m.get('ID'):m for m in models.findall('TreeNodesModel/*')}
    ports=[]
    for name in sorted({n.tag for t in trees for n in t[0] if n.tag not in ('SubTree','SetBlackboard')}):
        model=model_map[name]
        for p in model:
            row={'owner':name,'parameter':p.get('name'),'direction':p.tag.removesuffix('_port'),
                 'cpp_type':p.get('type'),'node_kind':model.tag,'default_present':'default' in p.attrib,'source_refs':[refs_by_node[p]]}
            if row['default_present']: row['declared_default']=p.get('default')
            ports.append(row)
    for key in ('value','output_key'):
        ports.append({'owner':'SetBlackboard','parameter':key,'direction':'input','cpp_type':'Any' if key=='value' else 'std::string',
                      'default_present':False,'source_refs':[refs_by_node[root]]})
    directions={(p['owner'],p['parameter']):p['direction'] for p in ports}
    execution={'root_attributes':dict(root.attrib),'trees':[],'ports':ports,'public_configs':public,'data_references':[]}
    for order,tree in enumerate(trees,1):
        tid=tree.get('ID'); current={'tree_id':tid,'tree_order':order,'calls':[]}; keys=set()
        for index,node in enumerate(tree[0],1):
            skill=node.get('ID') if node.tag=='SubTree' else node.tag
            call={'id':f'{tid}/{index}','step_index':index,'xml_tag':node.tag,'xml_name':node.get('name'),
                  'skill_id':skill,'source_refs':[refs_by_node[node]],'bindings':[]}
            for key,raw in node.attrib.items():
                if key in ('ID','name'): continue
                if node.tag=='SubTree': direction='input' if key in COMPOSITES[skill][1] else 'output'
                else: direction=directions[skill,key]
                match=VAR.fullmatch(raw); name=match.group(1) if match else raw
                if node.tag=='SetBlackboard' and key=='output_key': kind='local_output'; keys.add(raw)
                elif direction=='output': kind='local_output'; keys.add(name)
                elif match:
                    kind='parent_parameter' if tid in COMPOSITES and name in COMPOSITES[tid][1] else 'local_input'; keys.add(name)
                else: kind='literal'
                call['bindings'].append({'id':f'{tid}/{index}/{key}','parameter':key,'direction':direction,
                    'kind':kind,'value':name,'raw':raw,'scope':tid,'source_refs':[refs_by_node[node]]})
            current['calls'].append(call)
        if tid in COMPOSITES: keys.update(COMPOSITES[tid][1]+COMPOSITES[tid][2])
        execution['data_references'].extend({'id':f'{tid}/{k}','scope':tid,'key':k} for k in sorted(keys))
        execution['trees'].append(current)
    return {'status':'extracted','issues':[],'execution':deepcopy(execution)}

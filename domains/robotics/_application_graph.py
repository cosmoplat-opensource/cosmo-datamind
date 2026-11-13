"""Render canonical execution identities without legacy business examples — DR-072."""
import json
from rdflib import RDF, RDFS, URIRef
from .graph import S, ONTOLOGY, schema, individual, literal, parameter, reference, invocation
from .profiles.ur_pick_place import COMPOSITES, owner, semantic_type, add_conditions
from .modeling._extractor import _identifier
from .artifact_contract import ARTIFACT_CONTRACT, trusted_shapes
from .publisher import _normalize_port_metadata
from ._application_rdf import parse_rdf


def render(execution, design, role_map, mode):
    g=schema(); aliases={}
    def add(iri,cls,label,source_id=None):
        individual(g,iri,cls,label)
        if source_id: aliases[source_id]=iri
        return iri
    for doc in design['evidence']['documents']:
        iri=S.BehaviorTree_Source if doc['role']=='behavior_tree' else S.NodeModels_Source
        add(iri,'SourceDocument',doc['role']); literal(g,iri,'sha256',doc['sha256'])
    literal(g,S.BehaviorTree_Source,'rootAttributes',json.dumps(execution['root_attributes'],ensure_ascii=False))
    literal(g,ONTOLOGY,'datasetKind',mode)
    for port in execution['ports']:
        name,key=port['owner'],port['parameter']; impl=S[name+'_BT_Impl']; skill=S[name+'_Skill']
        if (skill,RDF.type,S.AtomicSkill) not in g:
            add(skill,'AtomicSkill',name,_identifier('skill',name)); g.add((skill,RDF.type,S.Skill))
            add(impl,'SkillImplementation',name,_identifier('implementation',name)); literal(g,impl,'xmlTag',name)
            literal(g,impl,'nodeKind',port.get('node_kind','Action'))
            g.add((skill,S.implementedBy,impl))
        pc=add(S[f'Port_{name}__{key}'],'PortContract',name+'.'+key,_identifier('port',name,key))
        p=add(parameter(name+'_Skill',key),'SkillParameter',name+'.'+key,_identifier('parameter',_identifier('skill',name),key))
        g.add((impl,S.hasPortContract,pc)); g.add((p,S.mapsToPort,pc))
        g.add((skill,S.hasInput if port['direction']=='input' else S.hasOutput,p))
        for prop,value in [('parameterName',key),('direction',port['direction']),('cppType',port['cpp_type']),
                           ('defaultPresent',port['default_present'])]: literal(g,pc,prop,value)
        if port['default_present']: literal(g,pc,'declaredDefault',port['declared_default'])
        literal(g,p,'semanticType',semantic_type(name,key,port['cpp_type']))
    for tree in execution['trees']:
        tid=tree['tree_id']; proc=owner(tid)
        add(proc,'CompositeSkill' if tid in COMPOSITES else 'TaskProcedure',tid,
            _identifier('skill',tid) if tid in COMPOSITES else _identifier('procedure',tid))
        literal(g,proc,'treeId',tid); literal(g,proc,'treeOrder',tree['tree_order'])
        if tid in COMPOSITES:
            g.add((proc,RDF.type,S.Skill)); name,inputs,outputs=COMPOSITES[tid]
            impl=add(S[tid+'_Subtree_Impl'],'SkillImplementation',tid,_identifier('subtree_implementation',tid))
            g.add((proc,S.implementedBy,impl)); literal(g,impl,'treeId',tid)
            fixed=add(S[tid+'_FixedConfig'],'ImplementationConfiguration',tid,_identifier('fixed',tid))
            g.add((impl,S.hasFixedConfiguration,fixed))
            for key in inputs+outputs:
                p=add(parameter(name,key),'SkillParameter',tid+'.'+key,_identifier('parameter',_identifier('skill',tid),key))
                direction='input' if key in inputs else 'output'
                g.add((proc,S.hasInput if direction=='input' else S.hasOutput,p))
                for prop,value in [('parameterName',key),('direction',direction),('semanticType','JointConfiguration' if key=='position' else 'RawInterfaceValue'),
                                   ('isPublicConfig',direction=='input' and key!='obj_in_robot')]:literal(g,p,prop,value)
                g.add((p,S.parameterData,reference(tid,key)))
                if direction=='output': g.add((p,S.exportsData,reference(tid,key)))
                contracts={port['cpp_type'] for call in tree['calls'] for binding in call['bindings']
                    for port in execution['ports'] if port['owner']==call['skill_id'] and port['parameter']==binding['parameter']
                    and binding['value']==key and binding['kind']==('parent_parameter' if direction=='input' else 'local_output')}
                if len(contracts)!=1: raise ValueError('复合技能端口类型不唯一。')
                pc=add(S[f'Port_{tid}__{key}'],'PortContract',tid+'.'+key,_identifier('port',tid,key))
                g.add((impl,S.hasPortContract,pc))
                for prop,value in [('parameterName',key),('direction',direction),('cppType',next(iter(contracts)))]:literal(g,pc,prop,value)

        for call in tree['calls']:
            index=call['step_index']; node=invocation(tid,index); tag=call['skill_id']
            add(node,'SkillInvocation',call['xml_name'] or call['id'],_identifier('call',tid,index))
            g.add((proc,S.hasStep,node)); g.add((node,S.inProcedure,proc))
            skill_name=COMPOSITES[tag][0] if call['xml_tag']=='SubTree' else tag+'_Skill'
            g.add((node,S.invokesSkill,S[skill_name]))
            for prop,value in [('stepIndex',index),('xmlTag',call['xml_tag']),('sourcePath',f'/root/BehaviorTree[@ID="{tid}"]/Sequence/*[{index}]')]:literal(g,node,prop,value)
            if call['xml_name'] is not None: literal(g,node,'xmlName',call['xml_name'])
            for row in call['bindings']:
                key=row['parameter']; binding=S[f'{tid}_{index}__{key}']
                add(binding,'ParameterBinding',row['id'],_identifier('binding',tid,index,key))
                g.add((node,S.hasBinding,binding)); g.add((binding,S.bindsParameter,parameter(skill_name,key)))
                literal(g,binding,'bindingDirection',row['direction']); literal(g,binding,'rawBinding',row['raw'])
                if call['xml_tag']=='SetBlackboard' and key=='output_key':
                    literal(g,binding,'literalValue',row['value']); g.add((node,S.producesData,reference(tid,row['value'])))
                elif row['kind']=='literal':
                    literal(g,binding,'literalValue',row['value'])
                    if tid in COMPOSITES: g.add((S[tid+'_FixedConfig'],S.hasFixedBinding,binding))
                elif row['kind']=='parent_parameter':g.add((binding,S.sourceParameter,parameter(COMPOSITES[tid][0],row['value'])))
                else:
                    prop=S.targetData if row['kind']=='local_output' else S.sourceData
                    g.add((binding,prop,reference(tid,row['value'])))
    for row in execution['data_references']:
        node=add(reference(row['scope'],row['key']),'DataReference',row['id'],_identifier('data',row['scope'],row['key']))
        literal(g,node,'scope',row['scope']); literal(g,node,'dataKey',row['key'])
    # Preserve non-execution knowledge and extension triples. Current mapped values are rendered once.
    original=parse_rdf(design['ontology']); ns=design['tbox']['namespace']; instance_ns=ns+'individual/'
    identities=[]
    for row in design['abox']['individuals']:
        src=URIRef(instance_ns+row['id'])
        if row['id'] in aliases:
            identities.append({'source_iri':str(src),'target_iri':str(aliases[row['id']]),
                               'role':row['class_ids'][0],'source_refs':row['source_refs']})
    mapped={URIRef(i['source_iri']):URIRef(i['target_iri']) for i in identities}
    terms={URIRef(r['iri']):S[r['role']] for r in role_map['bindings'] if list(g.objects(S[r['role']],RDF.type))}
    for subj,pred,obj in original:
        if subj in mapped and (pred in terms or pred in (RDF.type,RDFS.label)): continue
        # Non-execution annotations/extensions follow the identity mapping instead of disappearing.
        g.add((mapped.get(subj,subj),terms.get(pred,pred),mapped.get(obj,terms.get(obj,obj))))
    # Source-backed configuration only; no Part_A_001/Station_B/etc. are synthesized.
    values={r['parameter']:r['value'] for r in execution['public_configs']}
    profiles=list(g.subjects(RDF.type,S.PerceptionProfile))
    if profiles:
        joints=add(S.CaptureJoints_Current,'JointConfiguration','源文件拍照关节配置')
        literal(g,joints,'rawValue',values['position']); literal(g,joints,'unitStatus','unconfirmed')
        for profile in profiles:
            literal(g,profile,'confirmationStatus','source_extracted');g.add((profile,S.hasCaptureJoints,joints))
    placement=add(S.Placement_Current,'PlacementProfile','源文件放置配置')
    pose=add(S.PlacePose_Current,'CartesianPose','源文件放置原始位姿')
    literal(g,pose,'rawValue',values['place_pose']); literal(g,pose,'unitStatus','unconfirmed')
    literal(g,pose,'frameStatus','requires_driver_confirmation');g.add((placement,S.hasTargetPose,pose))
    add_conditions(g); _normalize_port_metadata(g)
    literal(g,ONTOLOGY,'artifactContract',ARTIFACT_CONTRACT);literal(g,ONTOLOGY,'validationShapes',trusted_shapes())
    return g,identities

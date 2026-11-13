/* Explicit application role review and immutable adaptation — DR-072. */
(function(global){
  'use strict';
  const esc=value=>global.RoboticsAssets.escape(value),prefix='/api/robotics/application-adaptations';
  const kinds={classes:'类别',object_properties:'关系',data_properties:'数据属性'};
  function issueText(issue){
    if(issue.code!=='schema.missing_role')return issue.message;
    const term=issue.expected&&typeof issue.expected==='object'?issue.expected:null;
    const role=term?.role||issue.role||(issue.entity_refs||[])[0]?.split(/[\/#]/).pop();
    if(!role||issue.message?.includes(role))return issue.message;
    return `缺少${kinds[term?.kind]||'应用'}角色对应：${role}${term?.label?'（'+term.label+'）':''}。请核对已有术语，没有对应项时修订本体骨架。`;
  }
  function requirementText(term){
    if(!term||typeof term!=='object')return '';
    return [term.parent_ids?.length?'父类：'+term.parent_ids.join('、'):'',
      term.domain_ids?.length?'定义域：'+term.domain_ids.join('、'):'',
      term.range_ids?.length?'值域：'+term.range_ids.join('、'):'',
      term.datatype?'数据类型：'+term.datatype:''].filter(Boolean).join('；');
  }
  function renderIssue(issue){
    const details=requirementText(issue.expected);
    return `<article class="rc-issue"><p>${esc(issueText(issue))}</p>${details?`<small>${esc(details)}</small>`:''}</article>`;
  }
  async function request(url,body,signal){
    const response=await global.fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal}:{signal});
    const data=await response.json();if(!response.ok)throw Error(data.error?.issues?.map(i=>i.message).join('；')||data.error?.message||'请求未完成');return data;
  }
  function mount(host,params){
    const abort=new AbortController();let dead=false,timer=null,state=null,draft=null,dirty=false,busy=false;
    const endpoint=prefix+'/'+encodeURIComponent(params.get('adaptation'));
    const names={awaiting_role_confirmation:'核对角色对应',roles_confirmed:'角色已确认',needs_tbox_revision:'请补充角色对应',queued:'正在准备应用版本',running:'正在提取执行结构',completed:'应用版本已保存',failed:'适配未完成',interrupted:'适配已中断'};
    const dispose=()=>{dead=true;abort.abort();clearTimeout(timer);};
    function error(e){if(!dead&&e.name!=='AbortError'){const notice=host.querySelector('[data-adaptation-error]');if(notice)notice.textContent=e.message;else host.textContent=e.message;}}
    async function operation(name,extra={}){
      if(busy)return;busy=true;
      const controls=[...host.querySelectorAll('button,select')].map(el=>[el,el.disabled]);controls.forEach(([el])=>{el.disabled=true;});
      try{
        const reply=await request(endpoint+'/'+name,{expected_revision:state.revision,request_id:global.crypto.randomUUID(),...extra},abort.signal);
        if(dead)return;
        if(name==='resume-task'){global.location.hash=global.RoboticsTask.link(reply.ontology_id,reply.version_id,reply.mode)+'&session='+encodeURIComponent(reply.session_id);return;}
        state=reply;draft=null;dirty=false;render();
      }catch(e){error(e);}finally{busy=false;controls.forEach(([el,disabled])=>{if(el.isConnected)el.disabled=disabled;});}
    }
    function complete(){return Object.entries(state.role_proposal.required_roles||{}).every(([kind,roles])=>roles.every(role=>draft.some(b=>b.kind===kind&&b.role===role&&b.iri)));}
    function later(){clearTimeout(timer);if(!dead&&['queued','running'].includes(state?.status))timer=global.setTimeout(load,300);}
    function render(){
      if(dead)return;
      draft=[...(state.role_map?.bindings||state.role_proposal.bindings)];
      for(const [kind,roles] of Object.entries(state.role_proposal.required_roles||{}))for(const role of roles)if(!draft.some(b=>b.kind===kind&&b.role===role))draft.push({kind,role,iri:''});
      const active=['queued','running'].includes(state.status),done=state.status==='completed',missing=state.status==='needs_tbox_revision',retry=['failed','interrupted'].includes(state.status)&&!!state.role_map;
      const groups=[['classes','类别'],['object_properties','关系'],['data_properties','属性']];
      const options=state.role_proposal.term_options||state.role_proposal.bindings.map(b=>({...b,id:b.role,label:b.role}));
      const unresolved=state.matching?.unresolved||[];
      const review=draft.filter(b=>unresolved.length?unresolved.some(i=>i.kind===b.kind&&i.role===b.role):(state.role_proposal.required_roles?.[b.kind]||[]).includes(b.role));
      const rows=groups.map(([kind,title])=>{const entries=review.filter(b=>b.kind===kind);return entries.length?`<details class="ra-section" open><summary>${title}对应</summary><div class="ra-role-list">${entries.map(b=>`<label style="display:grid;grid-template-columns:minmax(140px,1fr) 2fr;gap:16px;margin:12px 0"><span>${esc(b.role)}</span><select aria-label="对应术语：${esc(b.role)}" data-role-kind="${kind}" data-role="${esc(b.role)}"><option value="" ${!b.iri?'selected':''}>请选择对应术语</option>${options.filter(c=>c.kind===kind).map(c=>`<option value="${esc(c.iri)}" ${c.iri===b.iri?'selected':''}>${esc(c.label||c.id)}（${esc(c.id)}）</option>`).join('')}</select></label>`).join('')}</div></details>`:'';}).join('');
      const work=active?`<div class="rd-progress" role="status"><span class="rd-progress-spinner" aria-hidden="true"></span><span>${state.matching?.method==='automatic'?'自动匹配已通过，正在生成应用版本，请稍候…':'正在生成应用版本，请稍候…'}</span></div>`:retry?'<button type="button" data-submit>重试生成</button>':
        (missing?`<a class="ra-button" href="#build?recipe=robotics&session=${encodeURIComponent(state.origin_session.id)}&adaptation=${encodeURIComponent(state.adaptation_id)}">修订本体骨架</a><div data-rebase></div>`:'')+
        `<p>${state.matching?.reason==='vocabulary_review_required'?'该设计的词表版本需要人工核对。':'请为无法确定的应用角色选择对应的已有本体术语。'}</p>${rows}<div class="rb-actions"><button type="button" data-confirm-generate ${!complete()?'disabled':''}>确认并生成应用版本</button></div>`;
      host.innerHTML=`<section class="card ra-section" data-testid="robotics-application-adaptation" data-adaptation-id="${esc(state.adaptation_id)}" data-status="${esc(state.status)}"><header class="rt-heading"><div><span class="rt-eyebrow">行为树应用准备</span><h2>${esc(names[state.status]||state.status)}</h2><p>按应用要求匹配并校验角色，适配结果保存为独立的应用版本。</p></div></header><p class="rt-safety">执行未验证</p><div role="alert" data-adaptation-error></div>${state.issues.map(renderIssue).join('')}
      ${done?`<p>${state.matching?.method==='automatic'?'已自动匹配并完成执行结构适配。':'执行结构已适配。'}继续准备任务时，可补齐零件、载具、槽位与配置关联。</p><a class="ra-button" href="${esc(global.RoboticsAssets.versionLink(state.output_asset_ref.ontology_id,state.output_asset_ref.version_id))}">查看应用版本</a>${state.task_ref?'<button type="button" data-resume>切换并返回原任务</button>':''}`:work}</section>`;
      host.querySelector('[data-confirm-generate]')?.addEventListener('click',()=>operation('submit',{bindings:draft.filter(b=>b.iri)}));
      host.querySelector('[data-submit]')?.addEventListener('click',()=>operation('submit'));
      host.querySelector('[data-resume]')?.addEventListener('click',()=>operation('resume-task',{expected_task_revision:state.task_revision}));
      if(missing)host.querySelector('a[href^="#build?"]')?.addEventListener('click',async event=>{
        event.preventDefault();if(busy)return;busy=true;
        const destination=event.currentTarget.getAttribute('href');
        try{
          const sid=state.origin_session.id,design=await request('/api/robotics/design-sessions/'+encodeURIComponent(sid),null,abort.signal);
          if(dead)return;
          const propertyOnly=state.issues.length&&state.issues.every(i=>['object_properties','data_properties'].includes(i.expected?.kind||i.kind));
          const stage=propertyOnly&&design.class_confirmation?'properties':'classes';
          const reply={href:destination,target:{resource_kind:'design_session',resource_id:sid},
            design_repair:{session_id:sid,expected_revision:design.revision,current_stage:design.stage,stage}};
          const href=await global.RoboticsDesign.beginRepair(reply,global.crypto.randomUUID());
          if(!dead)global.location.hash=href;
        }catch(e){error(e);}finally{busy=false;}
      });
      host.querySelectorAll('[data-role]').forEach(select=>select.onchange=()=>{
        draft=draft.map(b=>b.role===select.dataset.role&&b.kind===select.dataset.roleKind?{...b,iri:select.value}:b);dirty=true;
        host.querySelector('[data-confirm-generate]').disabled=!complete();
      });
      if(missing)request('/api/ontology-assets?domain=robotics',null,abort.signal).then(catalog=>{
        if(dead)return;const asset=catalog.items.find(a=>a.ontology_id===state.asset_ref.ontology_id),box=host.querySelector('[data-rebase]');
        if(!box)return;
        box.innerHTML=`<label>已修订的本体版本<select aria-label="已修订的本体版本">${(asset?.versions||[]).filter(v=>v.metadata?.design_contract&&v.version_id!==state.asset_ref.version_id).map(v=>`<option value="${esc(v.version_id)}">版本 ${esc(v.version_number)}</option>`).join('')}</select></label><button type="button">使用修订后的本体</button>`;
        box.querySelector('button').onclick=()=>{const vid=box.querySelector('select').value;if(vid)operation('rebase',{asset_ref:{ontology_id:state.asset_ref.ontology_id,version_id:vid}});};
      }).catch(error);
      later();
    }
    async function load(){try{const saved=await request(endpoint,null,abort.signal);if(dead||dirty)return;if(state&&JSON.stringify(saved)===JSON.stringify(state)){later();return;}state=saved;render();}catch(e){error(e);}}
    host.innerHTML='<p role="status">正在读取应用准备记录…</p>';load();return {dispose};
  }
  global.RoboticsApplication={mount,issueText,requirementText,renderIssue};
})(window);

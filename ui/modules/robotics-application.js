/* Explicit application role review and immutable adaptation — DR-154 / DR-166. */
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
    const controller=new AbortController(),cancel=()=>controller.abort();let timedOut=false;
    signal?.addEventListener('abort',cancel,{once:true});if(signal?.aborted)cancel();
    const timer=global.setTimeout(()=>{timedOut=true;cancel();},30000);
    try{
      const response=await global.fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal}:{signal:controller.signal});
      let data;try{data=await response.json();}catch(_){throw Error('无法读取服务响应，请稍后重试。');}
      if(!response.ok)throw Object.assign(Error(data.error?.issues?.map(i=>i.message).join('；')||data.error?.message||'请求未完成'),{code:data.error?.code});return data;
    }catch(e){if(timedOut)throw Error('准备请求超时，请稍后重试。');throw e;}
    finally{global.clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
  }
  function mount(host,params){
    const abort=new AbortController();let dead=false,timer=null,state=null,draft=null,dirty=false,busy=false,advancing=false,advanceFailed=false,failure='',failureCode='';
    const identities=new Map();
    let continueRequested=params.get('continue')==='1'||!!params.get('revised');
    const endpoint=prefix+'/'+encodeURIComponent(params.get('adaptation'));
    const names={awaiting_role_confirmation:'核对角色对应',roles_confirmed:'角色已确认',needs_tbox_revision:'请补充角色对应',queued:'正在准备应用版本',running:'正在提取执行结构',completed:'应用版本已保存',failed:'适配未完成',interrupted:'适配已中断'};
    const dispose=()=>{dead=true;abort.abort();clearTimeout(timer);};
    function error(e){if(!dead&&e.name!=='AbortError'){failureCode=e.code||'';failure=failureCode==='session_conflict'?'原任务已更新，请查看当前任务后重新检查。':e.message;const notice=host.querySelector('[data-adaptation-error]');if(notice)notice.textContent=failure;else host.textContent=failure;}}
    async function operation(name,extra={}){
      if(busy||dead)return false;busy=true;failure='';failureCode='';
      const controls=[...host.querySelectorAll('button,select')].map(el=>[el,el.disabled]);controls.forEach(([el])=>{el.disabled=true;});
      try{
        const payload={expected_revision:state.revision,...extra},key=JSON.stringify([name,payload]);
        if(!identities.has(key))identities.set(key,name==='resume-task'?'resume-'+state.adaptation_id+'-'+state.task_revision:global.dmRequestId());
        const reply=await request(endpoint+'/'+name,{...payload,request_id:identities.get(key)},abort.signal);
        if(dead)return false;
        if(name==='resume-task'){global.location.replace(global.RoboticsTask.link(reply.ontology_id,reply.version_id,reply.mode)+'&session='+encodeURIComponent(reply.session_id));return true;}
        if(name==='submit')continueRequested=true;
        state=reply;draft=null;dirty=false;render();
        return true;
      }catch(e){error(e);return false;}finally{busy=false;controls.forEach(([el,disabled])=>{if(el.isConnected)el.disabled=disabled;});}
    }
    async function continueTask(){
      if(dead||busy||advancing||advanceFailed||!continueRequested||state?.status!=='completed'||state.write_frozen||!state.output_asset_ref)return;
      advancing=true;
      if(state.task_ref){
        if(!await operation('resume-task',{expected_task_revision:state.task_revision})){advanceFailed=true;advancing=false;if(!dead)render();}
      }else{
        const ref=state.output_asset_ref;
        global.location.replace(global.RoboticsTask.link(ref.ontology_id,ref.version_id,state.mode)+'&assess=1&adapted='+encodeURIComponent(state.adaptation_id)+'&entry='+encodeURIComponent('adapted-'+state.adaptation_id));
      }
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
      const conflict=failureCode==='session_conflict';
      host.innerHTML=`<section class="card ra-section" data-testid="robotics-application-adaptation" data-adaptation-id="${esc(state.adaptation_id)}" data-status="${esc(state.status)}"><header class="rt-heading"><div><span class="rt-eyebrow">行为树应用准备</span><h2>${esc(names[state.status]||state.status)}</h2><p>按应用要求匹配并校验角色，适配结果保存为独立的应用版本。</p></div></header><p class="rt-safety">执行未验证</p><div role="alert" data-adaptation-error>${esc(failure)}</div>${conflict&&state.task_ref?`<a class="ra-button" href="#robotics-task?session=${encodeURIComponent(state.task_ref.session_id)}">查看当前任务</a>`:failure?'<button type="button" data-retry-adaptation>重新读取准备状态</button>':''}${state.issues.map(renderIssue).join('')}
      ${done?`<p>${continueRequested&&!advanceFailed?'执行结构已适配，正在继续准备任务…':'执行结构已适配，可继续准备任务。'}</p><a class="ra-button" href="${esc(global.RoboticsAssets.versionLink(state.output_asset_ref.ontology_id,state.output_asset_ref.version_id))}">查看应用版本</a>${!conflict&&(advanceFailed||!continueRequested)?'<button type="button" data-continue>继续准备任务</button>':''}`:work}</section>`;
      host.querySelector('[data-confirm-generate]')?.addEventListener('click',()=>operation('submit',{bindings:draft.filter(b=>b.iri)}));
      host.querySelector('[data-submit]')?.addEventListener('click',()=>operation('submit'));
      host.querySelector('[data-continue]')?.addEventListener('click',()=>{continueRequested=true;advanceFailed=false;continueTask();});
      host.querySelector('[data-retry-adaptation]')?.addEventListener('click',load);
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
          const href=await global.RoboticsDesign.beginRepair(reply,global.dmRequestId());
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
      if(done&&!failure)Promise.resolve().then(continueTask);
    }
    async function load(){try{
      const saved=await request(endpoint,null,abort.signal);if(dead||dirty)return;
      const revised=params.get('revised');
      const consumed=()=>{params.delete('revised');params.set('continue','1');global.history.replaceState(null,'','#robotics-task?'+params.toString());};
      if(revised&&saved.asset_ref.version_id!==revised){
        state=saved;const accepted=await operation('rebase',{asset_ref:{ontology_id:state.asset_ref.ontology_id,version_id:revised}});
        if(!dead){if(accepted)consumed();render();}return;
      }
      if(revised)consumed();
      if(state&&JSON.stringify(saved)===JSON.stringify(state)&&!failure){later();return;}
      state=saved;failure='';failureCode='';render();
    }catch(e){error(e);}}
    host.innerHTML='<p role="status">正在读取应用准备记录…</p>';load();return {dispose};
  }
  global.RoboticsApplication={mount,issueText,requirementText,renderIssue};
})(window);

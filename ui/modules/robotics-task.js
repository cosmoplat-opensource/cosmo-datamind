/* Persistent task controller and minimal task-page composition — DR-063 / DR-064 / DR-066. */
(function(global){
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // DR-066: normalize legacy display names, including restored sessions, without
  // rewriting immutable OWL versions or treating a name as binding status.
  const legacyLabels = new Map([
    ['源文件当前感知参数；尚未绑定业务零件类型', '源文件感知配置'],
    ['源文件当前放置目标；尚未绑定业务槽位', '源文件放置配置']
  ]);
  const displayLabel = value => legacyLabels.get(value) ?? value;
  const prefix = '/api/robotics/tasks';
  async function json(fetcher,url,options){
    const response=await fetcher(url,options);let body;
    try {body=await response.json();} catch(_){throw new Error(`请求失败（HTTP ${response.status}），请稍后重试。`);}
    if(!response.ok) throw Object.assign(new Error(body.error?.message || `请求失败（HTTP ${response.status}）`),{code:body.error?.code});
    return body;
  }
  function createController(options={}){
    const fetcher=options.fetch || global.fetch.bind(global), emit=options.onChange || (()=>{}),
      requestId=options.requestId || (()=>global.crypto.randomUUID());
    let state={session:null,answers:{},noteDrafts:{},notice:null,error:null,busy:false}, generation=0,dead=false,lastRequest=null;
    const snapshot=()=>copy(state), notify=()=>{if(!dead)emit(snapshot());};
    function savedAnswers(){for(const gap of state.session?.gaps||[])
      if(gap.saved_answer && !state.answers[gap.id])state.answers[gap.id]=copy(gap.saved_answer);}
    function identity(payload){const key=JSON.stringify(payload);if(lastRequest?.key!==key)lastRequest={key,id:requestId()};return lastRequest.id;}
    async function load(ref,sid){
      const token=++generation;state={session:null,answers:{},noteDrafts:{},notice:null,error:null,busy:true};notify();
      try{
        const result=sid?await json(fetcher,prefix+'/'+encodeURIComponent(sid)):
          await json(fetcher,prefix,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...ref,request_id:requestId()})});
        if(dead || token!==generation)return;
        if(sid && result.session_id!==sid || !sid && (result.ontology_id!==ref.ontology_id || result.version_id!==ref.version_id))throw new Error('返回的任务身份不匹配。');
        state.session=result;savedAnswers();
      }catch(e){if(dead || token!==generation)return;state.error={message:e.message,code:e.code};}
      if(dead || token!==generation)return;state.busy=false;notify();return state.session;
    }
    async function submit(operation,args){
      if(dead || state.busy || !state.session)return;
      if(operation==='completion'){
        const missing=[];
        for(const gap of state.session.gaps||[]){
          const answer=state.answers[gap.id];
          if(gap.kind!=='applicability' || !answer)continue;
          const fields=[];
          if(!String(answer.reference||'').trim())fields.push('适配依据');
          if(!answer.valid_until)fields.push('有效期');
          if(fields.length)missing.push(`${gap.label}：请填写${fields.join('和')}`);
        }
        if(missing.length){
          state.error={code:'invalid_answer',operation,message:missing.join('；')+'。勾选确认不能代替依据和有效期。'};
          notify();return state.session;
        }
      }
      const token=generation, original=copy(state.session), answers=copy(state.answers), notes=copy(state.noteDrafts);
      const payload={...args,expected_revision:original.revision};
      payload.request_id=identity({session_id:original.session_id,operation,...payload});
      state.busy=true;state.error=null;state.notice=null;
      if(operation==='messages'){state.session.candidate=null;state.session.tree=null;state.answers={};state.noteDrafts={};}
      notify();
      try{
        const result=await json(fetcher,prefix+'/'+encodeURIComponent(original.session_id)+'/'+operation,
          {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        if(dead || token!==generation)return;
        if(result.session_id!==original.session_id || result.ontology_id!==original.ontology_id)throw new Error('返回的任务身份不匹配。');
        state.session=result;
        if(result.version_id!==original.version_id){
          state.noteDrafts={};
          state.notice=Number.isInteger(original.version_number)&&Number.isInteger(result.version_number)?
            `已从版本 ${original.version_number} 更新到版本 ${result.version_number}`:'已保存新版本';
        }
        if(operation==='notes'){
          for(const [kind,text] of Object.entries(notes))if(state.noteDrafts[kind]===text)delete state.noteDrafts[kind];
          state.notice='记录已保存；适配状态不受备注影响';
        }
        if(operation==='configurations')state.answers={};
        if(operation==='completion')for(const [id,answer] of Object.entries(answers))
          if(JSON.stringify(state.answers[id])===JSON.stringify(answer))delete state.answers[id];
        savedAnswers();
        lastRequest=null;
      }catch(e){if(dead || token!==generation)return;state.error={message:e.message,code:e.code,operation};}
      if(dead || token!==generation)return;state.busy=false;notify();return state.session;
    }
    return {snapshot,open:ref=>load(ref),restore:sid=>load(null,sid),
      setAnswer(id,answer){if(answer==null)delete state.answers[id];else state.answers[id]=copy(answer);},
      send:text=>submit('messages',{text}),
      save:()=>submit('completion',{answers:Object.entries(state.answers).map(([gap_id,answer])=>({gap_id,...answer}))}),
      choose:choice=>submit('choices',choice),
      configure:choice=>submit('configurations',choice),
      generate:()=>submit('generate',{}),
      setNote(kind,text){state.noteDrafts[kind]=text;},
      saveNotes:()=>submit('notes',{notes:Object.entries(state.noteDrafts).filter(([,text])=>text.trim()).map(([kind,text])=>({kind,text}))}),
      dismissError(){state.error=null;notify();},
      dispose(){dead=true;generation++;}
    };
  }
  let active=null;
  function link(oid,vid,mode='real'){return '#robotics-task?asset='+encodeURIComponent(oid)+'&version='+encodeURIComponent(vid)+'&mode='+encodeURIComponent(mode);}
  function renderTree(node){return `<li data-node-tag="${esc(node.tag)}"><details><summary>${esc(node.attributes?.ID || node.attributes?.name || node.tag)}</summary><small>${esc(Object.entries(node.attributes||{}).filter(([k])=>!['ID','name'].includes(k)).map(([k,v])=>k+'='+v).join(' · '))}</small></details>${node.children?.length?'<ul>'+node.children.map(renderTree).join('')+'</ul>':''}</li>`;}
  function page(name){
    if(active){active.controller?.dispose();clearTimeout(active.timer);active=null;}
    if(name!=='robotics-task')return;
    const host=global.document.getElementById('rt_task');if(!host)return;
    const params=new URLSearchParams(global.location.hash.split('?')[1]||'');
    const view={controller:null,text:'',creates:{},configEdit:null,capabilities:null,timer:null,initial:true};active=view;
    function render(state){
      if(active!==view)return;
      const s=state.session, answer=id=>state.answers[id]||{};
      host.setAttribute('data-testid','robotics-task');
      if(!s){host.innerHTML=state.error?`<p role="alert">${esc(state.error.message)}</p>`:'<p>正在准备任务…</p>';return;}
      for(const [attr,value] of Object.entries({'session-id':s?.session_id,'version-id':s?.version_id,'ontology-id':s?.ontology_id,mode:s?.mode,status:s?.status}))host.setAttribute('data-'+attr,value||'');
      // Replace, not hash navigation: keep this controller and any in-progress input alive.
      const route=link(s.ontology_id,s.version_id,s.mode)+'&session='+encodeURIComponent(s.session_id);
      global.history.replaceState(null,'',route);
      const model=view.capabilities?.model || s.model_status || {}, enabled=view.capabilities?.enabled!==false;
      const error=state.error || s.error, busy=state.busy || s.status==='running';
      if(view.configEdit && (view.configEdit.session!==s.session_id || view.configEdit.revision!==s.revision))view.configEdit=null;
      const editing=view.configEdit;
      const gaps=editing?[]:s.gaps||[], confirmations=gaps.filter(g=>g.kind==='confirmation');
      const offline=s.generation_policy==='offline', notesError=error?.operation==='notes';
      const noteValue=kind=>state.noteDrafts[kind]??(s.adaptation_notes||[]).filter(n=>n.kind===kind&&n.version_id===s.version_id&&JSON.stringify(n.task)===JSON.stringify(s.task)).at(-1)?.text??'';
      const saveError=error?.operation==='completion' && gaps.length;
      const configError=error?.operation==='configurations' && editing;
      const alert=error?`<p class="rt-error" role="alert" tabindex="-1">${saveError?'保存未成功：':''}${esc(error.message)}</p>`:'';
      const stillCurrent=()=>{const current=view.controller.snapshot().session;return active===view && current?.session_id===s.session_id && current?.revision===s.revision;};
      host.innerHTML=`<header class="rt-heading"><div><span class="rt-eyebrow">机器人任务 · ${s.mode==='demo'?'独立演示':'行为树生成'}</span><h3>${esc(s.asset_name||'行为树生成')}</h3><p data-testid="task-current-version">当前：<strong>${Number.isInteger(s.version_number)?'版本 '+s.version_number:'编号加载中'}</strong></p></div><button type="button" class="ghost" data-new>新任务</button></header>
        ${s.status==='idle'?'<div class="rt-welcome"><div class="rt-welcome-mark" aria-hidden="true">◇</div><h1>从一句任务，到行为树</h1><p>描述要操作的零件和目标位置，已有知识直接复用。<br>有需要补充的地方，我们会在下方提示。</p><div class="rt-flow"><span>描述任务</span><i>→</i><span>补齐信息</span><i>→</i><span>查看结果</span></div></div>':''}
        <div class="dq-inputbar rt-composer"><label for="rt_text">任务描述</label><textarea id="rt_text" placeholder="例如：把零部件A放到工位B的载具B01、放置位B01">${esc(view.text)}</textarea>
          <div class="rt-compose-tools"><p data-testid="task-model-status" class="rt-model">${!enabled?'机器人功能未启用':model.configured?'任务解析模型：'+esc(model.model):'模型未配置，请到引擎设置完成配置。'} <a href="#enginecfg">模型设置</a></p><button type="button" class="dq-send" data-send ${busy||!enabled||!model.configured?'disabled':''}>生成行为树</button></div>
        </div><div class="rt-status-row"><span role="status" class="rt-status">${busy?'处理中…':s.status==='generated'?'行为树已生成':s.status==='needs_information'?'请补齐当前任务需要的信息':s.status==='needs_clarification'?'请明确任务中的对象或目标':''}</span><p class="muted rt-safety">生成离线候选行为树，需经设备侧验证后使用。</p></div>
        ${state.notice?`<p class="rt-notice" role="status">${esc(state.notice)}</p>`:''}
        ${!saveError&&!configError&&!notesError?alert:''}
        ${offline&&s.task?`<p class="rt-adaptation-status">${s.mode==='demo'?'演示数据 · ':''}${s.task_readiness==='evidence_confirmed'?'当前适配记录有效':'任务适配未验证'} · 执行未验证</p>`:''}
        ${offline&&s.task&&s.status==='needs_information'&&!s.candidate&&!gaps.length&&!editing&&!busy&&!s.issues?.length?'<button type="button" class="dq-send" data-generate>继续生成行为树</button>':''}
        ${s.status==='unsupported'?'<p role="alert">当前仅支持固定抓移放流程，请调整任务描述。</p>':''}
        ${s.status==='needs_clarification'?(s.issues||[]).map(i=>`<div class="rt-choice dq-card"><span class="rt-eyebrow">确认任务目标</span><p>${esc(i.message)}</p>${i.field&&['part','station','carrier','slot'].includes(i.field)?`<label>选择对象<select data-choice="${esc(i.field)}"><option value="">请选择</option>${(i.candidates||[]).map(c=>`<option value="${esc(c.id)}">${esc(displayLabel(c.label))}</option>`).join('')}</select></label><button class="ghost" data-create-choice="${esc(i.field)}">新建所需对象</button>`:''}</div>`).join(''):''}
        ${(s.configurations||[]).length?`<section class="rt-configurations dq-card"><div class="rt-section-heading"><div><h3>本次使用的配置</h3><p>来自当前本体版本。已有配置直接复用，需要时可更换。</p></div><span class="rb-soft-tag">已关联</span></div><div class="rt-config-grid">${s.configurations.map(c=>{
          const edit=editing?.field===c.field?editing:null;
          const candidates=(c.candidates||[]).filter(p=>p.id!==c.profile_iri);
          return `<article class="rt-config-card" data-config="${esc(c.field)}"><div class="rt-config-title"><h4>${esc(c.label)}</h4><button type="button" class="ghost" aria-label="更换${esc(c.label)}" data-change-config="${esc(c.field)}" ${busy||!enabled?'disabled':''}>更换配置</button></div><p class="rt-config-owner">${esc(displayLabel(c.entity_label))}</p><p class="rt-config-name">${esc(displayLabel(c.profile_label))}</p><details class="rt-config-params"><summary>查看参数（${c.parameters.length} 项）</summary><dl>${c.parameters.map(p=>`<div><dt>${esc(p.label)}</dt><dd>${p.value==null?'<span class="muted">待补齐</span>':esc(p.value)}</dd></div>`).join('')}</dl></details>${edit?`<div class="rt-config-editor">${edit.create?`<label for="rt_config_name">配置名称</label><input id="rt_config_name" data-config-name value="${esc(edit.label)}" placeholder="填写易于识别的配置名称" ${busy?'disabled':''}><button type="button" class="ghost" data-config-existing ${busy?'disabled':''}>选择已有配置</button>`:`<label for="rt_config_target">选择配置</label><select id="rt_config_target" data-config-target ${busy?'disabled':''}><option value="">请选择其他配置</option>${candidates.map(p=>`<option value="${esc(p.id)}" ${edit.target===p.id?'selected':''}>${esc(displayLabel(p.label))}</option>`).join('')}</select>${!candidates.length?'<p class="muted">暂无其他配置，可新建配置。</p>':''}<button type="button" class="ghost" data-config-create ${busy?'disabled':''}>新建配置</button>`}<p class="rt-config-note">保存为新的本体版本，后续任务可复用；将重新检查缺项和适配依据。${(Object.keys(state.answers).length||Object.keys(state.noteDrafts).length)?'尚未保存的补齐内容和备注将清空，请按新版本填写。':''}</p>${configError?alert:''}<div class="rb-actions"><button type="button" class="dq-send" data-config-save ${busy||!enabled||!(edit.create?edit.label.trim():edit.target)?'disabled':''}>保存配置更换</button><button type="button" class="ghost" data-config-cancel ${busy?'disabled':''}>取消</button></div></div>`:''}</article>`;
        }).join('')}</div></section>`:''}
        ${gaps.length?'<section class="rt-completion dq-card"><div class="rt-section-heading"><div><h3>补齐生成信息</h3><p class="muted">已有有效信息直接复用，只需完成下列项目。</p></div><span class="rb-soft-tag">按需补齐</span></div><div class="rt-gap-grid">':''}
        ${gaps.filter(g=>g.kind!=='confirmation').map(g=>{
          const a=answer(g.id), id='rt_'+g.id;let control='';
          if(g.kind==='mapping')control=`<label for="${id}">关联对象</label><select id="${id}" data-target="${esc(g.id)}"><option value="">请选择已有对象</option>${(g.candidates||[]).map(c=>`<option value="${esc(c.id)}" ${a.target===c.id?'selected':''}>${esc(displayLabel(c.label))}</option>`).join('')}</select><button type="button" class="ghost" data-create="${esc(g.id)}">新建关联对象</button>${view.creates[g.id]?`<label for="${id}_name">对象名称</label><input id="${id}_name" data-create-name="${esc(g.id)}" value="${esc(a.create?.label||'')}">`:''}`;
          else if(g.kind==='parameter')control=`<label for="${id}">${esc(g.label)}</label><input id="${id}" data-value="${esc(g.id)}" value="${esc(a.value||'')}" placeholder="${g.field==='rawValue'?'按原设备单位填写数值数组':g.field==='exposureTime'?'填写大于 0 的数值':g.field==='threshold'?'填写 0 到 1 之间的数值':'填写实际值'}">`;
          else control=`<p class="muted">${esc((g.scope_labels||[]).map(displayLabel).join(' → ')) || (g.field==='grasp_compatibility'?'当前零件类型与抓取实现':'当前零件类型、目标放置位与放置实现')}</p><label for="${id}_ref">适配依据</label><textarea id="${id}_ref" data-reference="${esc(g.id)}" placeholder="填写实际依据说明或报告引用">${esc(a.reference||'')}</textarea><label for="${id}_expiry">有效期</label><input id="${id}_expiry" type="date" data-expiry="${esc(g.id)}" value="${esc((a.valid_until||'').slice(0,10))}"><label for="${id}_file">适配资料（可选）</label><input id="${id}_file" type="file" data-attachment="${esc(g.id)}"><small>${a.source_asset_ids?.length?'已上传 '+a.source_asset_ids.length+' 份资料':''}</small><label class="rt-check"><input type="checkbox" data-evidence-confirm="${esc(g.id)}" ${a.confirmed?'checked':''}>我确认以上依据适用于当前范围</label>`;
          return `<div class="rt-gap" data-testid="task-gap" data-gap-id="${esc(g.id)}" data-kind="${g.kind}"><h4>${esc(g.label)}</h4><p>${esc(displayLabel(g.entity_label)||'')}</p><p class="muted">${esc(g.reason)}</p>${control}</div>`;
        }).join('')}
        ${gaps.length?'</div>':''}
        ${confirmations.length?`<div class="rt-confirm"><details><summary>核对当前任务知识（${confirmations.length} 项）</summary>${confirmations.map(g=>`<p>${esc(displayLabel(g.entity_label))} ${esc(Object.values(g.values||{}).join(' · '))}</p>`).join('')}</details><label class="rt-check"><input type="checkbox" data-confirm ${confirmations.every(g=>answer(g.id).confirmed)?'checked':''}>我已核对当前任务知识</label></div>`:''}
        ${gaps.length?`${saveError?alert:''}<button type="button" class="dq-send" data-save ${busy||!enabled?'disabled':''}>保存并继续</button></section>`:''}
        ${s.candidate&&s.tree&&!busy&&!editing?`<section class="rt-result dq-card"><div class="rt-section-heading"><div><span class="rt-eyebrow">生成结果</span><h3>行为树预览</h3></div><a class="dq-send rt-download" data-download href="${prefix}/${encodeURIComponent(s.session_id)}/candidate?revision=${s.revision}">下载行为树 XML</a></div><div class="rt-tree" data-testid="task-tree" data-run-id="${esc(s.run_id)}"><ul>${renderTree(s.tree)}</ul></div><details class="rt-report"><summary>参数来源与验证报告</summary><pre>${esc(JSON.stringify(s.result,null,2))}</pre></details></section>`:''}
        ${offline&&s.configurations?.length&&!editing?`<details class="rt-notes dq-card" ${notesError?'open':''}><summary>适配记录（可选）</summary><p class="muted">可记录当前配置的调试事项。无需有效期，不影响离线生成，也不代表已完成适配验证。</p><div class="rt-config-grid">${[['grasp_compatibility','抓取备注','hasPerceptionProfile'],['placement_compatibility','放置备注','hasPlacementProfile']].filter(([, ,field])=>s.configurations.some(c=>c.field===field)).map(([kind,label])=>`<div><label for="rt_note_${kind}">${label}</label><textarea id="rt_note_${kind}" data-note-kind="${kind}" placeholder="选填，例如后续需要核查的事项" ${busy?'disabled':''}>${esc(noteValue(kind))}</textarea></div>`).join('')}</div>${notesError?alert:''}<button type="button" class="ghost" data-save-notes ${busy||!Object.values(state.noteDrafts).some(t=>t.trim())?'disabled':''}>保存记录</button></details>`:''}
        ${s.messages.length?`<details class="rt-history"><summary>任务对话记录</summary><div class="rt-conversation">${s.messages.map(m=>`<div class="${m.role==='user'?'rt-message-user':'rt-message-system'}"><span>${m.role==='user'?'你':'机器人助手'}</span><p>${esc(m.content)}</p></div>`).join('')}</div></details>`:''}
        <div class="rb-actions rt-secondary"><a href="#library">返回本体库</a><a href="/api/ontology-assets/${encodeURIComponent(s.ontology_id)}/versions/${encodeURIComponent(s.version_id)}/payload">下载当前 OWL</a>${s.mode==='real'?'<button type="button" class="ghost" data-demo>打开独立演示</button>':''}</div>`;
      host.querySelector('#rt_text').oninput=e=>{view.text=e.target.value;};
      host.querySelector('[data-send]').onclick=()=>view.controller.send(view.text);
      host.querySelector('[data-new]').onclick=()=>{view.text='';view.creates={};view.controller.open({ontology_id:s.ontology_id,version_id:s.version_id,mode:s.mode});};
      host.querySelector('[data-save]')?.addEventListener('click',()=>view.controller.save());
      host.querySelector('[data-generate]')?.addEventListener('click',()=>view.controller.generate());
      host.querySelectorAll('[data-note-kind]').forEach(el=>el.oninput=()=>{view.controller.setNote(el.dataset.noteKind,el.value);host.querySelector('[data-save-notes]').disabled=busy||!Object.values(view.controller.snapshot().noteDrafts).some(t=>t.trim());});
      host.querySelector('[data-save-notes]')?.addEventListener('click',()=>view.controller.saveNotes());
      host.querySelectorAll('[data-change-config]').forEach(el=>el.onclick=()=>{view.configEdit={field:el.dataset.changeConfig,session:s.session_id,revision:s.revision,target:'',create:false,label:''};view.controller.dismissError();});
      host.querySelector('[data-config-cancel]')?.addEventListener('click',()=>{view.configEdit=null;view.controller.dismissError();});
      host.querySelector('[data-config-create]')?.addEventListener('click',()=>{editing.create=true;view.controller.dismissError();});
      host.querySelector('[data-config-existing]')?.addEventListener('click',()=>{editing.create=false;view.controller.dismissError();});
      const configSave=host.querySelector('[data-config-save]');
      host.querySelector('[data-config-target]')?.addEventListener('change',e=>{editing.target=e.target.value;configSave.disabled=busy||!enabled||!editing.target;});
      host.querySelector('[data-config-name]')?.addEventListener('input',e=>{editing.label=e.target.value;configSave.disabled=busy||!enabled||!editing.label.trim();});
      configSave?.addEventListener('click',()=>{const c=s.configurations.find(c=>c.field===editing.field);view.controller.configure({field:c.field,...(editing.create?{create:{kind:c.target_kind,label:editing.label.trim()}}:{target:editing.target})});});
      const set=(id,changes)=>view.controller.setAnswer(id,{...(view.controller.snapshot().answers[id]||{}),...changes});
      host.querySelectorAll('[data-target]').forEach(el=>el.onchange=()=>{view.controller.setAnswer(el.dataset.target,{target:el.value});});
      host.querySelectorAll('[data-value]').forEach(el=>el.oninput=()=>set(el.dataset.value,{value:el.value}));
      host.querySelectorAll('[data-create]').forEach(el=>el.onclick=()=>{const id=el.dataset.create,g=gaps.find(g=>g.id===id);view.creates[id]=true;view.controller.setAnswer(id,{create:{kind:g.target_kind,label:''}});render(view.controller.snapshot());});
      host.querySelectorAll('[data-create-name]').forEach(el=>el.oninput=()=>{const g=gaps.find(g=>g.id===el.dataset.createName);set(g.id,{create:{kind:g.target_kind,label:el.value}});});
      host.querySelector('[data-confirm]')?.addEventListener('change',e=>confirmations.forEach(g=>view.controller.setAnswer(g.id,e.target.checked?{confirmed:true}:null)));
      for(const [attr,key,convert] of [['reference','reference',v=>v],['expiry','valid_until',v=>v?v+'T23:59:59+00:00':'']])
        host.querySelectorAll('[data-'+attr+']').forEach(el=>el.oninput=()=>set(el.dataset[attr],{[key]:convert(el.value),confirmed:!!(view.controller.snapshot().answers[el.dataset[attr]]||{}).confirmed}));
      host.querySelectorAll('[data-evidence-confirm]').forEach(el=>el.onchange=()=>set(el.dataset.evidenceConfirm,{confirmed:el.checked}));
      host.querySelectorAll('[data-attachment]').forEach(el=>el.onchange=async()=>{if(!el.files[0])return;const form=new FormData();form.append('file',el.files[0]);try{const file=await json(global.fetch.bind(global),'/api/ontology-assets/'+encodeURIComponent(s.ontology_id)+'/evidence-sources',{method:'POST',body:form});if(!stillCurrent())return;set(el.dataset.attachment,{source_asset_ids:[file.source_asset_id]});render(view.controller.snapshot());}catch(e){if(stillCurrent())showError(e.message);}});
      host.querySelectorAll('[data-choice]').forEach(el=>el.onchange=()=>{if(el.value)view.controller.choose({field:el.dataset.choice,target:el.value});});
      host.querySelectorAll('[data-create-choice]').forEach(el=>el.onclick=()=>{const field=el.dataset.createChoice,label=global.prompt('请输入当前任务所需对象的名称');if(label)view.controller.choose({field,create:{kind:({part:'Workpiece',station:'Workstation',carrier:'Carrier',slot:'PlacementSlot'})[field],label}});});
      host.querySelector('[data-demo]')?.addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;try{const result=await json(global.fetch.bind(global),'/api/robotics/task-demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:global.crypto.randomUUID()})});if(stillCurrent()){view.text='';view.creates={};await view.controller.restore(result.session_id);}}catch(e){if(stillCurrent())showError(e.message);}finally{if(button.isConnected)button.disabled=false;}});
      host.querySelector('[data-download]')?.addEventListener('click',async e=>{e.preventDefault();try{const response=await global.fetch(e.currentTarget.href);if(!response.ok){const problem=await response.json();throw new Error(problem.error?.message||'下载失败');}const blob=await response.blob();if(!stillCurrent())return;const url=URL.createObjectURL(blob),a=global.document.createElement('a');a.href=url;a.download='behavior-tree.xml';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){if(stillCurrent())showError(e.message);}});
      if(error){
        const notice=host.querySelector('.rt-error');
        notice?.focus({preventScroll:true});
        notice?.scrollIntoView({block:'center'});
      }
      clearTimeout(view.timer);
      if(s.status==='running'&&!state.busy)view.timer=setTimeout(()=>{if(active===view)view.controller.restore(s.session_id);},1000);
    }
    function showError(message){const el=global.document.createElement('p');el.setAttribute('role','alert');el.textContent=message;host.prepend(el);}
    view.controller=createController({onChange:render});
    (async()=>{try{view.capabilities=await json(global.fetch.bind(global),'/api/robotics/task-capabilities');if(active!==view)return;
      if(params.get('session'))await view.controller.restore(params.get('session'));
      else await view.controller.open({ontology_id:params.get('asset'),version_id:params.get('version'),mode:params.get('mode')||'real'});
    }catch(e){showError(e.message);}})();
  }
  function importButton(host){
    if(host.querySelector('[data-rt-import]'))return;
    const wrap=global.document.createElement('div');wrap.className='rt-import';
    wrap.innerHTML='<button type="button" data-rt-import>导入机器人 OWL</button><form hidden><label>本体名称<input name="name" required></label><label>OWL 文件<input name="file" type="file" accept=".owl" required></label><button>导入</button><p role="alert" hidden></p></form>';
    host.prepend(wrap);wrap.querySelector('button').onclick=()=>{wrap.querySelector('form').hidden=false;};
    wrap.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.currentTarget);form.set('mode','real');form.set('request_id',global.crypto.randomUUID());try{const r=await json(global.fetch.bind(global),'/api/robotics/imports',{method:'POST',body:form});global.location.hash=link(r.ontology_id,r.version_id,r.mode);}catch(err){const p=wrap.querySelector('[role=alert]');p.hidden=false;p.textContent=err.message;}};
  }
  global.RoboticsTask={createController,page,link,importButton};
})(window);

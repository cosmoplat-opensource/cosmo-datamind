/* Persistent task controller and minimal task-page composition — DR-145 / DR-146 / DR-148 / DR-155 / DR-166 / DR-167 / DR-168 / DR-169 / DR-170. */
(function(global){
  'use strict';
  const copy = value => JSON.parse(JSON.stringify(value));
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // DR-148: normalize legacy display names, including restored sessions, without
  // rewriting immutable OWL versions or treating a name as binding status.
  const legacyLabels = new Map([
    ['源文件当前感知参数；尚未绑定业务零件类型', '源文件感知配置'],
    ['源文件当前放置目标；尚未绑定业务槽位', '源文件放置配置']
  ]);
  const displayLabel = value => legacyLabels.get(value) ?? value;
  function knowledgeConfirmations(gaps){
    return gaps.some(g=>['mapping','parameter'].includes(g.kind))?[]:gaps.filter(g=>g.kind==='confirmation');
  }
  function remainingInformation(gaps){
    const items=gaps.filter(g=>g.kind!=='confirmation');
    const labels=items.map(g=>g.label);
    if(knowledgeConfirmations(gaps).length)labels.push('勾选“我已核对当前任务知识”');
    return [...new Set(labels)].join('、');
  }
  function completionFeedback(gaps,answers){
    if(!answers.length){
      if(gaps.length&&gaps.every(g=>g.kind==='confirmation'))return '请先勾选“我已核对当前任务知识”，再保存并继续。';
      return '尚未填写本次补齐内容，请完成：'+(remainingInformation(gaps)||'当前任务信息')+'。可分步填写并保存。';
    }
    const missing=[];
    for(const {gap_id,...answer} of answers){
      const gap=gaps.find(g=>g.id===gap_id);if(!gap)continue;
      let hint='';
      if(gap.kind==='mapping'){
        if(answer.create&&!String(answer.create.label||'').trim())hint='请填写新建对象的名称';
        else if(!answer.create&&!String(answer.target||'').trim())hint='请选择已有对象或新建关联对象';
      }else if(gap.kind==='parameter'&&!String(answer.value||'').trim())hint='请填写实际参数';
      else if(gap.kind==='applicability'){
        const fields=[];
        if(!String(answer.reference||'').trim())fields.push('适配依据');
        if(!answer.valid_until)fields.push('有效期');
        if(fields.length)hint='请填写'+fields.join('和')+'，勾选确认不能代替依据和有效期';
      }
      if(hint)missing.push(gap.label+'：'+hint);
    }
    if(answers.every(a=>gaps.find(g=>g.id===a.gap_id)?.kind==='confirmation'&&a.confirmed===false))
      missing.push('请先勾选“我已核对当前任务知识”，再保存并继续');
    return missing.length?missing.join('；')+'。':null;
  }
  const prefix = '/api/robotics/tasks';
  async function json(fetcher,url,options){
    const response=await fetcher(url,options);let body;
    try {body=await response.json();} catch(_){throw new Error(`请求失败（HTTP ${response.status}），请稍后重试。`);}
    if(!response.ok) throw Object.assign(new Error(body.error?.message || `请求失败（HTTP ${response.status}）`),{code:body.error?.code});
    return body;
  }
  function createController(options={}){
    const fetcher=options.fetch || global.fetch.bind(global), emit=options.onChange || (()=>{}),
      requestId=options.requestId || (()=>global.dmRequestId());
    let state={session:null,answers:{},noteDrafts:{},notice:null,error:null,busy:false}, generation=0,dead=false,lastRequest=null;
    const snapshot=()=>copy(state), notify=()=>{if(!dead)emit(snapshot());};
    function savedAnswers(){for(const gap of state.session?.gaps||[])
      if(gap.saved_answer && !state.answers[gap.id])state.answers[gap.id]=copy(gap.saved_answer);}
    function identity(payload){const key=JSON.stringify(payload);if(lastRequest?.key!==key)lastRequest={key,id:requestId()};return lastRequest.id;}
    async function load(ref,sid,admission){
      const token=++generation;state={session:null,answers:{},noteDrafts:{},notice:null,error:null,busy:true};notify();
      try{
        const result=sid?await json(fetcher,prefix+'/'+encodeURIComponent(sid)):
          await json(fetcher,prefix,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...ref,request_id:admission || requestId()})});
        if(dead || token!==generation)return;
        if(sid&&result.session_id===sid&&ref&&result.ontology_id===ref.ontology_id&&result.mode===ref.mode&&result.version_id!==ref.version_id)
          throw Object.assign(new Error('该任务已使用其它本体版本，请查看当前任务后继续。'),{code:'task_scope_changed'});
        if((sid && result.session_id!==sid) || (ref && (result.ontology_id!==ref.ontology_id || result.version_id!==ref.version_id || result.mode!==ref.mode)))throw new Error('返回的任务身份不匹配。');
        state.session=result;savedAnswers();
      }catch(e){if(dead || token!==generation)return;state.error={message:e.message,code:e.code};}
      if(dead || token!==generation)return;state.busy=false;notify();return state.session;
    }
    async function submit(operation,args){
      if(dead || state.busy || !state.session)return;
      if(operation==='completion'){
        const message=completionFeedback(state.session.gaps||[],args.answers);
        if(message){state.error={code:'invalid_answer',operation,message};notify();return state.session;}
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
        if(operation==='completion'&&result.gaps?.length)
          state.notice=(state.notice?state.notice+'；':'')+'已保存本次填写内容；还需完成：'+remainingInformation(result.gaps)+'。';
        lastRequest=null;
      }catch(e){if(dead || token!==generation)return;state.error={message:e.message,code:e.code,operation};}
      if(dead || token!==generation)return;state.busy=false;notify();return state.session;
    }
    return {snapshot,open:(ref,admission)=>load(ref,null,admission),restore:(sid,ref)=>load(ref,sid),
      setAnswer(id,answer){if(answer==null)delete state.answers[id];else state.answers[id]=copy(answer);},
      send:text=>submit('messages',{text}),
      save:()=>{
        const gaps=state.session?.gaps||[],visible=new Set(knowledgeConfirmations(gaps).map(g=>g.id));
        return submit('completion',{answers:Object.entries(state.answers)
          .filter(([id])=>!gaps.some(g=>g.id===id&&g.kind==='confirmation')||visible.has(id))
          .map(([gap_id,answer])=>({gap_id,...answer}))});
      },
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
  // DR-155: drafts belong to a fixed session/revision, never another ontology version.
  function draftKey(sid){return 'robotics-task-draft:'+sid;}
  function readDraft(sid){try{return JSON.parse(global.sessionStorage.getItem(draftKey(sid))||'null');}catch(_){return null;}}
  function saveDraft(view){
    const state=view?.controller?.snapshot?.(),s=state?.session;if(!s)return;
    try{global.sessionStorage.setItem(draftKey(s.session_id),JSON.stringify({version:s.version_id,revision:s.revision,
      text:view.text,answers:state.answers,notes:state.noteDrafts,creates:view.creates,configEdit:view.configEdit}));}catch(_){}
  }
  global.addEventListener?.('beforeunload',()=>saveDraft(active));
  function replaceRoute(params){global.history.replaceState(null,'','#robotics-task?'+params.toString());}
  function checkMarkup(response,{inline=false}={}){
    const r=response.report,current=response.freshness==='current',seen=new Set();
    const choices=r.issues.flatMap(i=>(i.repair_actions||[]).filter(a=>a.availability==='available').map(a=>{
      if(seen.has(a.action_id))return '';seen.add(a.action_id);
      return {action:a,issue:i};
    })).filter(Boolean);
    const repairs=choices.filter(c=>['robotics.revise_tbox','robotics.adapt_execution'].includes(c.action.action_id));
    const button=(c,label)=>`<button type="button" class="ra-button" data-action-id="${esc(c.action.action_id)}" data-issue-id="${esc(c.issue.issue_id)}" ${!current?'disabled':''}>${esc(label||c.action.label)}</button>`;
    const primary=choices.filter(c=>!repairs.includes(c)).map(c=>button(c)).join('')+(repairs.length?button(repairs[0],'完善本体'):'');
    const refresh=(!inline||!current||r.issues.length)?'<button type="button" class="ghost" data-recheck>重新检查</button>':'';
    return `<section class="${inline?'rt-preparation':'card ra-section'}" data-testid="application-check" data-check-id="${esc(r.check_id)}"><h${inline?'4':'2'}>行为树应用检查</h${inline?'4':'2'}><p role="status">${!current?'检查已过期，请重新检查后继续':r.preparation_status==='available'?'可准备任务':'还需补齐以下内容'}</p>${r.issues.map(i=>global.RoboticsApplication.renderIssue(i)).join('')}${repairs.length?'<p class="rc-next-step">点击“完善本体”，系统会引导你补充缺失定义或确认已有内容，完成后继续检查。</p>':''}<div class="rc-actions">${primary}${refresh}</div></section>`;
  }
  async function followRepair(view,response,issue,action,requestId){
    const abort=new AbortController();let timedOut=false;
    const timer=global.setTimeout(()=>{timedOut=true;abort.abort();},30000);
    const fetcher=(url,options={})=>global.fetch(url,{...options,signal:abort.signal});
    try{
      const reply=await json(fetcher,'/api/application-checks/'+encodeURIComponent(response.report.check_id)+'/repairs',
        {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({issue_id:issue.issue_id,action_id:action.action_id,input:{},expected_revision:action.target.expected_revision,request_id:requestId})});
      if(active!==view)return;
      if(reply.kind==='navigate'&&reply.href.startsWith('#')){
        // DR-167: only this accepted navigation may request a fresh diagnostic on Back.
        const r=response.report;
        global.history.replaceState({...global.history.state,applicationCheckReturn:{check:r.check_id,refs:r.asset_refs,mode:r.context.mode,task:r.task_ref?.session_id||null}},'',global.location.hash);
        const href=await global.RoboticsDesign.beginRepair(reply,requestId,fetcher);
        if(active===view)global.location.hash=href+(reply.target?.resource_kind==='robotics_adaptation'?'&continue=1':'');
      }else throw Error('未能打开完善页面，请重新检查后重试。');
    }catch(e){if(timedOut)throw Error('完善请求超时，请重试或重新检查。');throw e;}
    finally{global.clearTimeout(timer);}
  }
  function bindRepairs(host,view,response){
    const r=response.report;
    host.querySelectorAll('[data-action-id]').forEach(button=>button.onclick=async()=>{
      if(button.disabled||view.repairBusy)return;view.repairBusy=true;
      let stale=false;
      const controls=[...host.querySelectorAll('[data-action-id],[data-recheck]')].map(el=>[el,el.disabled]);
      controls.forEach(([el])=>{el.disabled=true;});
      try{
        const issue=r.issues.find(i=>i.issue_id===button.dataset.issueId),action=issue.repair_actions.find(a=>a.action_id===button.dataset.actionId);
        const requestId=button.dataset.requestId||(button.dataset.requestId=global.dmRequestId());
        await followRepair(view,response,issue,action,requestId);
      }catch(e){if(active===view){
        stale=['stale_result','revision_conflict','session_conflict'].includes(e.code);
        if(stale)response.freshness='stale';
        const notice=global.document.createElement('p');notice.setAttribute('role','alert');
        notice.textContent=stale?'本体或任务已变化，请重新检查后继续。':e.message;host.append(notice);
      }}
      finally{view.repairBusy=false;if(active===view)controls.forEach(([el,disabled])=>{if(el.isConnected)el.disabled=stale&&el.matches('[data-action-id]')?true:disabled;});}
    });
  }
  function page(name){
    if(active){saveDraft(active);active.controller?.dispose();clearTimeout(active.timer);active=null;}
    if(name!=='robotics-task')return;
    const host=global.document.getElementById('rt_task');if(!host)return;
    const params=new URLSearchParams(global.location.hash.split('?')[1]||'');
    const returning=global.history.state?.applicationCheckReturn;
    if(returning&&returning.check===params.get('check')&&returning.mode===(params.get('mode')||'real')&&
       returning.refs?.length===1&&returning.refs[0].ontology_id===params.get('asset')&&returning.refs[0].version_id===params.get('version')&&
       (!returning.task||returning.task===(params.get('session')||params.get('task')))){
      const previous={...global.history.state};delete previous.applicationCheckReturn;
      global.history.replaceState(previous,'',global.location.hash);
      if(returning.task)params.set('task',returning.task);
      params.delete('session');params.delete('check');params.delete('prepare');params.set('assess','1');params.set('readonly','1');params.set('entry',global.dmRequestId());
    }
    if(params.get('adaptation')){active={controller:global.RoboticsApplication.mount(host,params)};return;}
    if(params.get('history')||params.get('run')||!['session','asset','version'].some(key=>params.has(key)))return;
    if(!params.get('session') && params.get('asset') && params.get('version') && !params.get('prepare')){
      const view={};active=view;
      // Persist admission before sending: refresh and lost responses replay the same operation.
      if(!params.get('entry'))params.set('entry',global.dmRequestId());
      replaceRoute(params);
      const evaluate=async()=>{
        host.innerHTML='<p role="status">正在检查行为树应用要求…</p>';
        try{
          let response;
          if(params.get('check'))response=await json(global.fetch.bind(global),'/api/application-checks/'+encodeURIComponent(params.get('check')));
          else response=await json(global.fetch.bind(global),'/api/application-checks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({entrypoint:'robotics.generate_behavior_tree',asset_refs:[{ontology_id:params.get('asset'),version_id:params.get('version')}],context:{mode:params.get('mode')||'real',...(params.get('task')?{task_ref:{session_id:params.get('task')}}:{})},request_id:params.get('entry')})});
          if(active!==view)return;
          const r=response.report;
          if(r.entrypoint!=='robotics.generate_behavior_tree' || r.context.mode!==(params.get('mode')||'real') || (params.get('task') && r.task_ref && r.task_ref.session_id!==params.get('task')) || r.asset_refs.length!==1 || r.asset_refs[0].ontology_id!==params.get('asset') || r.asset_refs[0].version_id!==params.get('version'))throw Error('检查报告与指定的本体版本不匹配。');
          params.set('check',r.check_id);params.delete('assess');replaceRoute(params);
          if(response.freshness==='current' && r.preparation_status==='available' && !params.get('readonly')){
            if(params.get('task') || r.task_ref?.session_id)params.set('session',params.get('task') || r.task_ref.session_id);
            mountTask(host,params,response);return;
          }
          // DR-166: the server still owns matching/validation; never automate schema edits or stale checks.
          if(response.freshness==='current'&&!params.get('readonly')&&!params.get('adapted')&&
             r.issues.length&&r.issues.every(i=>i.layer==='execution')){
            const issue=r.issues.find(i=>i.repair_actions?.some(a=>a.action_id==='robotics.adapt_execution'&&a.availability==='available'));
            const action=issue?.repair_actions.find(a=>a.action_id==='robotics.adapt_execution'&&a.availability==='available');
            if(action){host.innerHTML='<p role="status">正在匹配并校验执行结构，请稍候…</p>';await followRepair(view,response,issue,action,'auto-'+params.get('entry'));return;}
          }
          host.innerHTML=checkMarkup(response);bindRepairs(host,view,response);
          host.querySelector('[data-recheck]').onclick=()=>{
            params.delete('check');params.delete('readonly');params.set('assess','1');params.set('entry',global.dmRequestId());replaceRoute(params);evaluate();
          };
        }catch(e){if(active===view){
          const changedTask=params.get('task')&&['invalid_request','not_found','stale_result'].includes(e.code);
          host.innerHTML=`<p role="alert">${esc(changedTask?'无法按原任务上下文重新检查，请查看当前任务或重新选择本体。':e.message)}</p>${changedTask?`<div class="rc-actions"><a class="ra-button" href="#robotics-task?session=${encodeURIComponent(params.get('task'))}">查看当前任务</a><a href="#library">选择其它本体</a></div>`:'<button type="button" class="ra-button" data-retry>重试检查</button>'}`;
          host.querySelector('[data-retry]')?.addEventListener('click',evaluate);
        }}
      };
      evaluate();return;
    }
    mountTask(host,params);
  }
  function mountTask(host,params,checked=null){
    const view={controller:null,text:'',creates:{},configEdit:null,capabilities:null,timer:null,initial:true,checked};active=view;
    host.innerHTML='<p role="status">正在准备任务…</p>';
    function render(state){
      if(active!==view)return;
      const s=state.session, answer=id=>state.answers[id]||{};
      host.setAttribute('data-testid','robotics-task');
      if(!s){host.innerHTML=state.error?`<p role="alert">${esc(state.error.message)}</p>${state.error.code==='task_scope_changed'?`<a class="ra-button" href="#robotics-task?session=${encodeURIComponent(params.get('session'))}">查看当前任务</a>`:'<button type="button" data-retry-task>重试准备任务</button>'}`:'<p role="status">正在准备任务…</p>';host.querySelector('[data-retry-task]')?.addEventListener('click',loadTask);return;}
      if(view.initial){
        view.initial=false;const draft=readDraft(s.session_id);
        if(draft?.version===s.version_id && draft.revision===s.revision){
          view.text=draft.text||'';view.creates=draft.creates||{};view.configEdit=draft.configEdit;
          for(const [id,answer] of Object.entries(draft.answers||{}))view.controller.setAnswer(id,answer);
          for(const [kind,text] of Object.entries(draft.notes||{}))view.controller.setNote(kind,text);
          Object.assign(state,view.controller.snapshot());
        }
      }
      const checked=view.checked,checkRef=checked?.report?.asset_refs?.[0];
      const matching=checkRef?.ontology_id===s.ontology_id && checkRef?.version_id===s.version_id && checked.report.context.mode===s.mode;
      const displayCheck=matching?{...checked,freshness:checked.report.task_ref && (checked.report.task_ref.session_id!==s.session_id || checked.report.task_ref.revision!==s.revision)?'stale':checked.freshness}:null;
      if(matching)host.setAttribute('data-check-id',checked.report.check_id);else host.removeAttribute('data-check-id');
      for(const [attr,value] of Object.entries({'session-id':s?.session_id,'version-id':s?.version_id,'ontology-id':s?.ontology_id,mode:s?.mode,status:s?.status}))host.setAttribute('data-'+attr,value||'');
      // Replace, not hash navigation: keep this controller and any in-progress input alive.
      const route=link(s.ontology_id,s.version_id,s.mode)+'&session='+encodeURIComponent(s.session_id)+(matching?'&check='+encodeURIComponent(checked.report.check_id):'');
      global.history.replaceState(null,'',route);
      global.RoboticsTaskApp?.changed(s);
      const model=view.capabilities?.model || s.model_status || {}, enabled=view.capabilities?.enabled!==false;
      const originalError=state.error || s.error;
      const legacyGenerationError=(originalError?.code==='generation_error'&&originalError.message==='信息已保存，但本体仍未通过严格生成校验，请检查后继续。')||(originalError?.code==='ontology_validation_failed'&&originalError.message.includes('请点击“完善本体”'));
      const error=legacyGenerationError?{...originalError,message:'信息已保存，但行为树未能生成。请按当前配置重试，系统将重新检查最终选中的配置。'}:originalError;
      const busy=state.busy || s.status==='running';
      if(view.configEdit && (view.configEdit.session!==s.session_id || view.configEdit.revision!==s.revision))view.configEdit=null;
      const editing=view.configEdit;
      const gaps=editing?[]:s.gaps||[];
      // DR-166: mappings can expose previously unseen profiles; confirm only the final visible scope.
      const confirmations=knowledgeConfirmations(gaps);
      const offline=s.generation_policy==='offline', notesError=error?.operation==='notes';
      const noteValue=kind=>state.noteDrafts[kind]??(s.adaptation_notes||[]).filter(n=>n.kind===kind&&n.version_id===s.version_id&&JSON.stringify(n.task)===JSON.stringify(s.task)).at(-1)?.text??'';
      const saveError=error?.operation==='completion' && gaps.length;
      const configError=error?.operation==='configurations' && editing;
      const alert=error?`<p class="rt-error" role="alert" tabindex="-1">${saveError?'保存未成功：':''}${esc(error.message)}</p>${error.code==='application_not_ready'?`<a class="ra-button" href="${esc(link(s.ontology_id,s.version_id,s.mode)+'&assess=1&task='+encodeURIComponent(s.session_id))}">检查当前任务的应用要求</a>`:''}${!state.error&&s.status==='error'&&s.task&&['ontology_validation_failed','generation_error'].includes(error.code)?`<button type="button" class="ra-button" data-retry-generation ${busy?'disabled':''}>按当前配置重试生成</button>`:''}`:'';
      const stillCurrent=()=>{const current=view.controller.snapshot().session;return active===view && current?.session_id===s.session_id && current?.revision===s.revision;};
      host.innerHTML=`<header class="rt-heading"><div><span class="rt-eyebrow">机器人任务 · ${s.mode==='demo'?'独立演示':'行为树生成'}</span><h3>${esc(s.asset_name||'行为树生成')}</h3><p data-testid="task-current-version">当前：<strong>${Number.isInteger(s.version_number)?'版本 '+s.version_number:'编号加载中'}</strong></p></div><button type="button" class="ghost" data-new>新任务</button></header>
        ${matching?checkMarkup(displayCheck,{inline:true}):''}
        ${s.status==='idle'?'<div class="rt-welcome"><div class="rt-welcome-mark" aria-hidden="true">◇</div><h1>从一句任务，到行为树</h1><p>描述要操作的零件和目标位置，已有知识直接复用。<br>有需要补充的地方，我们会在下方提示。</p><div class="rt-flow"><span>描述任务</span><i>→</i><span>补齐信息</span><i>→</i><span>查看结果</span></div></div>':''}
        <div class="dq-inputbar rt-composer"><label for="rt_text">任务描述</label><textarea id="rt_text" placeholder="例如：把零部件A放到工位B的载具B01、放置位B01">${esc(view.text)}</textarea>
          <div class="rt-compose-tools"><p data-testid="task-model-status" class="rt-model">${!enabled?'机器人功能未启用':model.configured?'任务解析模型：'+esc(model.model):'模型未配置，请到引擎设置完成配置。'} <a href="#enginecfg">模型设置</a></p><button type="button" class="dq-send" data-send ${busy||!enabled||!model.configured?'disabled':''}>生成行为树</button></div>
        </div><div class="rt-status-row"><span role="status" class="rt-status">${busy?'处理中…':s.status==='generated'?'行为树已生成':s.status==='needs_information'?'请补齐当前任务需要的信息':s.status==='needs_clarification'?'请明确任务中的对象或目标':''}</span></div>
        ${state.notice?`<p class="rt-notice" role="status">${esc(state.notice)}</p>`:''}
        ${!saveError&&!configError&&!notesError?alert:''}
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
      bindRepairs(host,view,view.checked || {report:{issues:[]}});
      host.querySelector('[data-recheck]')?.addEventListener('click',()=>{
        if(stillCurrent())global.location.hash=link(s.ontology_id,s.version_id,s.mode)+'&assess=1&task='+encodeURIComponent(s.session_id);
      });
      host.querySelector('#rt_text').oninput=e=>{view.text=e.target.value;saveDraft(view);};
      host.querySelector('[data-send]').onclick=()=>view.controller.send(view.text);
      host.querySelector('[data-new]').onclick=()=>{view.text='';view.creates={};view.controller.open({ontology_id:s.ontology_id,version_id:s.version_id,mode:s.mode});};
      host.querySelector('[data-save]')?.addEventListener('click',()=>view.controller.save());
      host.querySelector('[data-generate]')?.addEventListener('click',()=>view.controller.generate());
      host.querySelector('[data-retry-generation]')?.addEventListener('click',()=>view.controller.generate());
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
      host.querySelector('[data-demo]')?.addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;try{const result=await json(global.fetch.bind(global),'/api/robotics/task-demo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:global.dmRequestId()})});if(stillCurrent()){view.text='';view.creates={};await view.controller.restore(result.session_id);}}catch(e){if(stillCurrent())showError(e.message);}finally{if(button.isConnected)button.disabled=false;}});
      host.querySelector('[data-download]')?.addEventListener('click',async e=>{e.preventDefault();try{const response=await global.fetch(e.currentTarget.href);if(!response.ok){const problem=await response.json();throw new Error(problem.error?.message||'下载失败');}const blob=await response.blob();if(!stillCurrent())return;const url=URL.createObjectURL(blob),a=global.document.createElement('a');a.href=url;a.download='behavior-tree.xml';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){if(stillCurrent())showError(e.message);}});
      if(error){
        const notice=host.querySelector('.rt-error');
        notice?.focus({preventScroll:true});
        notice?.scrollIntoView({block:'center'});
      }
      if(s.write_frozen){const warning=global.document.createElement('p');warning.setAttribute('role','status');warning.textContent=s.lifecycle?.state==='deleted'?'本体已移到回收站。当前任务保留为历史，恢复后请重新检查本体再继续。':'本体已恢复，请重新检查并明确新建任务。';host.prepend(warning);host.querySelectorAll('button,input,textarea,select').forEach(el=>{el.disabled=true;});}
      clearTimeout(view.timer);
      if(s.status==='running'&&!state.busy)view.timer=setTimeout(()=>{if(active===view)view.controller.restore(s.session_id);},1000);
    }
    function showError(message){const el=global.document.createElement('p');el.setAttribute('role','alert');el.textContent=message;host.prepend(el);}
    view.controller=createController({onChange:render});
    async function loadTask(){
      if(active!==view)return;
      if(params.get('session'))await view.controller.restore(params.get('session'),params.get('asset') && params.get('version')?{ontology_id:params.get('asset'),version_id:params.get('version'),mode:params.get('mode')||'real'}:null);
      else await view.controller.open({ontology_id:params.get('asset'),version_id:params.get('version'),mode:params.get('mode')||'real'},params.get('entry'));
    }
    async function prepareTask(){
      host.innerHTML='<p role="status">正在准备任务…</p>';
      try{
        view.capabilities=await json(global.fetch.bind(global),'/api/robotics/task-capabilities');if(active!==view)return;
        if(!view.checked && params.get('check')){
          view.checked=await json(global.fetch.bind(global),'/api/application-checks/'+encodeURIComponent(params.get('check')));if(active!==view)return;
        }
        await loadTask();
      }catch(e){if(active===view){
        host.innerHTML=`<p role="alert">${esc(e.message)}</p><button type="button" data-retry-task>重试准备任务</button>`;
        host.querySelector('[data-retry-task]').onclick=prepareTask;
      }}
    }
    prepareTask();
  }
  function importButton(host){
    if(host.querySelector('[data-rt-import]'))return;
    const wrap=global.document.createElement('div');wrap.className='rt-import';
    wrap.innerHTML='<button type="button" data-rt-import>导入机器人 OWL</button><form hidden><label>本体名称<input name="name" required></label><label>OWL 文件<input name="file" type="file" accept=".owl" required></label><button>导入</button><p role="alert" hidden></p></form>';
    host.prepend(wrap);wrap.querySelector('button').onclick=()=>{wrap.querySelector('form').hidden=false;};
    wrap.querySelector('form').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.currentTarget);form.set('mode','real');form.set('request_id',global.dmRequestId());try{const r=await json(global.fetch.bind(global),'/api/robotics/imports',{method:'POST',body:form});global.location.hash=link(r.ontology_id,r.version_id,r.mode);}catch(err){const p=wrap.querySelector('[role=alert]');p.hidden=false;p.textContent=err.message;}};
  }
  global.RoboticsTask={createController,page,link,importButton,displayLabel,renderTree};
})(window);

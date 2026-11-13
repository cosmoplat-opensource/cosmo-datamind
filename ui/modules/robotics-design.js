/* Persistent design interaction and immutable history — DR-070. */
(function(g){'use strict';
const clone=x=>JSON.parse(JSON.stringify(x)),prefix='/api/robotics/design-sessions/',active=s=>['queued','running'].includes(s?.status);
async function read(fetch,url,options){const r=await fetch(url,options);let b;try{b=await r.json();}catch(_){throw Error('响应不是 JSON（HTTP '+r.status+'）');}if(!r.ok)throw Object.assign(Error(b.error?.message||b.error||'请求失败'),{code:b.error?.code,status:r.status});return b;}
function createController(o={}){
 const fetch=o.fetch||g.fetch.bind(g),emit=o.onChange||(()=>{}),schedule=o.schedule||g.setTimeout.bind(g),cancel=o.cancelSchedule||g.clearTimeout.bind(g),newId=o.requestId||(()=>g.crypto.randomUUID());
 let s={session:null,messageText:'',selectedRevision:null,busy:false,error:null},dead=false,epoch=0,id=null,timer=null,flight=null,identity=null;
 const notify=()=>{if(!dead)emit(clone(s));},clear=()=>{if(timer!==null)cancel(timer);timer=null;};
 function later(){clear();if(!dead&&active(s.session))timer=schedule(()=>{timer=null;return poll();},1000);}
 async function poll(){if(dead||!id)return;if(flight)return flight;clear();const token=epoch,sid=id;
  const p=(async()=>{try{const row=await read(fetch,prefix+encodeURIComponent(sid));if(dead||token!==epoch)return;if(row.session_id!==sid)throw Error('会话身份不匹配');s.session=row;s.error=null;}catch(e){if(!dead&&token===epoch)s.error={message:e.message,code:e.code,status:e.status};}finally{if(!dead&&token===epoch){flight=null;notify();later();}}})();flight=p;return p;}
 async function openSession(sid){if(dead)return;epoch++;if(id!==sid){s.messageText='';identity=null;}id=sid;flight=null;clear();s.busy=false;s.session=null;s.selectedRevision=null;s.error=null;notify();await poll();}
 async function act(op,extra={}){if(dead||s.busy||!s.session)return;const original=clone(s.session),text=s.messageText,token=epoch;
  const body={request_id:'',expected_revision:original.revision,...extra},fingerprint=JSON.stringify([id,op,body]);
  if(identity?.fingerprint!==fingerprint)identity={fingerprint,id:newId()};body.request_id=identity.id;s.busy=true;s.error=null;notify();
  try{const row=await read(fetch,prefix+encodeURIComponent(id)+'/'+op,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   if(dead||token!==epoch)return;if(row.session_id!==id)throw Error('会话身份不匹配');s.session=row;s.selectedRevision=null;identity=null;if(op==='messages'&&s.messageText===text)s.messageText='';
  }catch(e){if(!dead&&token===epoch)s.error={message:e.message,code:e.code,status:e.status};}finally{if(!dead&&token===epoch){s.busy=false;notify();later();}}}
 return {snapshot:()=>clone(s),openSession,poll,setMessage(text){if(dead)return;s.messageText=text;notify();},
 sendMessage(){if(!s.messageText.trim()||active(s.session))return;return act('messages',{text:s.messageText,expected_stage:s.session.stage});},
 confirmClasses(){return act('confirm-classes',{snapshot_sha256:s.session?.snapshot_sha256});},editClasses(){return act('edit-classes');},editProperties(){return act('edit-properties');},
 confirmSchema(){return act('confirm-schema',{snapshot_sha256:s.session?.snapshot_sha256});},retry(){if(!['failed','interrupted'].includes(s.session?.status))return;return act('retry',{failed_run_id:s.session.run_id});},
 async openRevision(revision){const token=epoch;try{const row=await read(fetch,prefix+encodeURIComponent(id)+'/revisions/'+revision);if(!dead&&token===epoch){s.selectedRevision=row;notify();}}catch(e){if(!dead&&token===epoch){s.error={message:e.message};notify();}}},
 dispose(){dead=true;epoch++;clear();}};
}
async function beginRepair(reply,requestId,fetch=g.fetch.bind(g)){
 const phase=reply.design_repair;
 if(!phase)return reply.href;
 if(reply.target?.resource_kind!=='design_session'||phase.session_id!==reply.target.resource_id||!['classes','properties'].includes(phase.stage))throw Error('修订目标不符合当前设计。');
 if(phase.current_stage!==phase.stage){
  await read(fetch,prefix+encodeURIComponent(phase.session_id)+'/edit-'+phase.stage,{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({request_id:'repair-stage-'+requestId,expected_revision:phase.expected_revision})});
 }
 return reply.href;
}
function presentation(result,esc){
 if(!result)return '';
 const groups=[['classes','类别'],['object_properties','对象关系'],['data_properties','数据属性']];
 return groups.filter(([key])=>result[key]?.length).map(([key,title])=>`<h4>${title}（${result[key].length}）</h4><div class="rd-table"><table><thead><tr><th>名称</th><th>定义</th><th>关联范围</th></tr></thead><tbody>${result[key].map(row=>`<tr><td><b>${esc(row.label||row.id)}</b><small>${esc(row.id)}</small></td><td>${esc(row.definition||'')}</td><td>${esc((row.parent_ids||row.domain_ids||row.class_ids||[]).join('、'))}</td></tr>`).join('')}</tbody></table></div>`).join('')+
 (result.competency_questions?.length?'<h4>本体需要回答的问题</h4><ul>'+result.competency_questions.map(q=>`<li>${esc(q.question)}</li>`).join('')+'</ul>':'')+
 (result.clarifications?.length?'<h4>待明确项</h4><ul>'+result.clarifications.map(q=>`<li>${esc(typeof q==='string'?q:q.question||q.message||q.reason||JSON.stringify(q))}</li>`).join('')+'</ul>':'');
}
function repairContext(response,sid,adaptation=false){
 const report=adaptation?response:response.report;
 if(!report||(!adaptation&&(report.application_id!=='robotics.pick_place'||report.entrypoint!=='robotics.generate_behavior_tree')))throw Error('检查报告不属于当前设计的行为树应用。');
 const issues=(report.issues||[]).filter(i=>i.layer==='schema'||i.code?.startsWith('schema.'));
 const matches=adaptation?report.origin_session?.id===sid:issues.some(i=>(i.repair_actions||[]).some(a=>a.action_id==='robotics.revise_tbox'&&a.target?.resource_id===sid));
 if(!matches)throw Error('检查报告不属于当前设计，请从对应本体的应用检查重新进入。');
 const items=adaptation?issues:issues.filter(i=>(i.repair_actions||[]).some(a=>a.action_id==='robotics.revise_tbox'&&a.target?.resource_id===sid));
 if(!items.length)throw Error('检查报告中没有当前设计的骨架缺项。');
 return {items,freshness:adaptation?'current':response.freshness,checkId:report.check_id,
   revision:adaptation?report.origin_session?.revision:items[0].repair_actions.find(a=>a.action_id==='robotics.revise_tbox'&&a.target?.resource_id===sid)?.target.expected_revision};
}
function repairSuggestion(context,stage){
 const groups={classes:[],object_properties:[],data_properties:[],other:[]};
 for(const item of context.items){
  const term=item.expected&&typeof item.expected==='object'?item.expected:null;
  const role=term?.role||item.role||(item.entity_refs||[])[0]?.split(/[\/#]/).pop();
  const kind=Object.hasOwn(groups,term?.kind)?term.kind:'other';
  if(role&&!groups[kind].includes(role))groups[kind].push(role);
 }
 const parts=keys=>keys.filter(key=>groups[key].length).map(key=>({classes:'类别',object_properties:'关系',data_properties:'数据属性',other:'术语'})[key]+' '+groups[key].join('、')).join('；');
 const basis='沿用受控词表和 XML 证据，不编造实例。';
 if(stage==='properties')return '根据此前应用检查，请核对并补充'+(parts(['object_properties','data_properties','other'])||'关系与数据属性')+'，保持已确认类别不变。'+basis;
 const classes=parts(['classes','other']);
 return '根据此前应用检查，本轮仅修订类别'+(classes?'：请核对并补充'+classes:'，请核对现有类别')+'，保留其它类别及层次。关系与属性在类别确认后处理。'+basis;
}
function mount(host,sid,options={}){const esc=g.RoboticsAssets.escape;let c,history=[],context=null,contextError='',dead=false,editorPhase=null;
 const contextual=!!(options.checkId||options.adaptationId),abort=new AbortController();
 const input=document.createElement('textarea');input.setAttribute('aria-label','建模要求');input.maxLength=20000;
 const editor=document.createElement('details'),body=document.createElement('div'),hint=document.createElement('div'),heading=document.createElement('div'),progress=document.createElement('div'),actions=document.createElement('div');
 editor.className='rd-editor';progress.className='rd-progress';progress.hidden=true;progress.dataset.testid='design-progress';progress.setAttribute('role','status');progress.setAttribute('aria-live','polite');progress.setAttribute('aria-atomic','true');
 actions.className='rd-confirm-bar';actions.dataset.testid='design-confirmation';actions.hidden=true;
 host.replaceChildren(heading,progress,actions,hint,editor,body);editor.append(input);const summary=document.createElement('summary'),label=document.createElement('label');label.textContent='建模要求';label.htmlFor='rd_message';input.id='rd_message';editor.prepend(summary,label);const send=document.createElement('button');send.textContent=contextual?'确认并发送建模要求':'发送建模要求';editor.append(send);
 const startProperties=document.createElement('button');startProperties.textContent='开始关系与属性设计';editor.append(startProperties);
 input.oninput=()=>c.setMessage(input.value);send.onclick=()=>c.sendMessage();
 function complete(){if(!context)return;c.setMessage(repairSuggestion(context,c.snapshot().session?.stage));input.focus();input.setSelectionRange(input.value.length,input.value.length);}
 input.onkeydown=e=>{
  if(e.key!=='Tab'||e.shiftKey||e.ctrlKey||e.altKey||e.metaKey||e.isComposing||!context||c.snapshot().session?.status==='awaiting_confirmation'||active(c.snapshot().session))return;
  const suggestion=repairSuggestion(context,c.snapshot().session?.stage),text=input.value;
  if(text.length<suggestion.length&&suggestion.startsWith(text)&&input.selectionStart===text.length&&input.selectionEnd===text.length){e.preventDefault();complete();}
 };
 startProperties.onclick=()=>{if(!c.snapshot().messageText.trim())c.setMessage('请基于已确认的类别、建模要求和上传的 XML，设计对象关系与数据属性；只采用来源支持的信息，不明确的内容列为待明确项。');return c.sendMessage();};
 function render(s){const row=s.session;if(!row){heading.textContent=s.error?.message||'正在读取设计…';body.replaceChildren();progress.hidden=true;actions.hidden=true;editor.hidden=true;return;}
 host.dataset.testid='robotics-design';host.dataset.sessionId=sid;host.dataset.stage=row.stage;host.dataset.revision=row.revision;host.dataset.status=row.status;
 const needsProperties=row.stage==='properties'&&row.status==='awaiting_input';
 const selected=s.selectedRevision,result=selected?.state?.result||row.result,ref=row.output_asset_ref,waiting=active(row),failed=['failed','interrupted'].includes(row.status);
 const awaiting=row.status==='awaiting_confirmation',suggesting=context&&!awaiting&&row.stage!=='abox';
 host.setAttribute('aria-busy',String(waiting||s.busy));
 progress.hidden=!(waiting||s.busy);
 if(!progress.hidden){
  const message=waiting?(row.stage==='abox'?'正在构建本体实例，请稍候…':row.status==='queued'?'已提交，等待模型处理，请稍候…':'大模型正在生成设计，请稍候…'):'正在提交操作，请稍候…';
  const content=`<span class="rd-progress-spinner" aria-hidden="true"></span><span>${message}</span>`;
  if(progress.innerHTML!==content)progress.innerHTML=content;
 }
 const primary=awaiting&&!selected?(row.stage==='classes'?['confirmClasses','确认类别']:row.stage==='properties'?['confirmSchema','确认骨架并构建实例']:null):null;
 actions.hidden=!primary;
 actions.innerHTML=primary?`<span>本轮设计已完成</span><button class="rd-primary-confirm" data-op="${primary[0]}" ${s.busy?'disabled':''}>${primary[1]}</button>`:'';
 hint.innerHTML=contextError?`<p role="alert" data-testid="schema-repair-context-error">${esc(contextError)}</p>`:'';
 const classNotes=!selected&&row.stage==='properties'?(row.class_confirmation?.clarifications||[]):[];
 const displayResult=classNotes.length&&result?{...result,clarifications:(result.clarifications||[]).filter(note=>!classNotes.includes(note))}:result;
 heading.innerHTML=`<h3>模型辅助构建 · ${esc(row.model?.model||'')}</h3><p>${esc(({classes:'类别设计',properties:'关系与属性设计',abox:'实例构建'})[row.stage]||row.stage)} · 修订 ${row.revision} · ${esc(({queued:'排队中',running:'运行中',needs_description:'等待建模要求',awaiting_input:'等待输入',awaiting_confirmation:'等待确认',completed:'已完成',failed:'失败',interrupted:'已中断'})[row.status]||row.status)} · 执行未验证</p>${(s.error||row.error)?`<p role="alert">${esc(s.error?.message||row.error?.message||'设计失败')}</p>`:''}`;
 body.innerHTML=`<label>设计修订<select aria-label="设计修订"><option value="">当前设计</option>${history.map(r=>`<option value="${r.revision}" ${selected?.revision===r.revision?'selected':''}>修订 ${r.revision}</option>`).join('')}</select></label>
 ${needsProperties&&!selected?`${context?'':'<section role="status" class="rd-next-step"><h4>类别已确认，下一步设计关系与属性</h4><p>确认类别只固定类别范围。请在下方补充关系与属性设计要求，或直接点击“开始关系与属性设计”，按已确认类别和 XML 继续。</p></section>'}<details><summary>查看已确认的类别</summary>${presentation({classes:row.class_confirmation?.classes||result?.classes||[]},esc)}</details>`:result?`<p>${esc(result.reply||'')}</p>${presentation(displayResult,esc)}`:''}
 ${classNotes.length&&!needsProperties?`<details><summary>类别阶段的历史待明确项（类别已确认）</summary>${presentation({clarifications:classNotes},esc)}</details>`:''}
 ${selected?`<section data-testid="robotics-design-diff"><h4>与当前设计的差异</h4><details open><summary>历史设计</summary>${presentation(selected.state.result,esc)}</details><details open><summary>当前设计</summary>${presentation(row.result,esc)}</details></section>`:''}
 <div>${!waiting&&row.revision?'<button data-op="editClasses">返回类别修改</button>':''}${failed?'<button data-op="retry">重试设计</button>':''}</div>
 ${ref?`<p>设计候选已保存 · 执行未验证</p><a href="${esc(g.RoboticsAssets.versionLink(ref.ontology_id,ref.version_id))}">查看本体候选</a>`:''}`;
 body.querySelector('select').onchange=e=>{if(e.target.value)c.openRevision(Number(e.target.value));else{c.openSession(sid);}};
 host.querySelectorAll('[data-op]').forEach(b=>{b.disabled=s.busy;b.onclick=()=>c[b.dataset.op]();});
 const phase=[row.stage,row.status,row.revision].join(':');
 if(phase!==editorPhase){editor.open=!awaiting||!!s.messageText.trim();editorPhase=phase;}
 summary.hidden=!awaiting;summary.textContent='继续提修改意见';
 editor.hidden=waiting||row.stage==='abox';startProperties.hidden=contextual||!needsProperties||!!s.messageText.trim();startProperties.disabled=s.busy;send.hidden=!contextual&&needsProperties&&!s.messageText.trim();send.disabled=s.busy||row.stage==='abox'||(contextual&&!s.messageText.trim());input.placeholder=awaiting?'可继续输入修改意见':suggesting?repairSuggestion(context,row.stage)+'（Tab 填入）':contextual&&!context&&!contextError?'正在读取修订建议…':needsProperties?'可选：填写希望补充的关系、参数或关注点；留空可按已有要求继续。':'填写本轮建模要求或修改意见'; if(input.value!==s.messageText)input.value=s.messageText;
 read(g.fetch.bind(g),prefix+sid+'/revisions').then(r=>{history=r.items;const select=body.querySelector('select');if(select&&!selected)select.innerHTML='<option value="">当前设计</option>'+history.map(r=>`<option value="${r.revision}">修订 ${r.revision}</option>`).join('');}).catch(()=>{});
 }
 c=createController({onChange:render});c.openSession(sid);
 if(contextual){const url=options.checkId?'/api/application-checks/'+encodeURIComponent(options.checkId):'/api/robotics/application-adaptations/'+encodeURIComponent(options.adaptationId);
  read(g.fetch.bind(g),url,{signal:abort.signal}).then(response=>{if(dead)return;context=repairContext(response,sid,!options.checkId);render(c.snapshot());}).catch(e=>{if(dead||e.name==='AbortError')return;contextError=e.message;render(c.snapshot());});}
 return {...c,dispose(){dead=true;abort.abort();c.dispose();}};
}
g.RoboticsDesign={createController,mount,repairContext,repairSuggestion,beginRepair};})(window);

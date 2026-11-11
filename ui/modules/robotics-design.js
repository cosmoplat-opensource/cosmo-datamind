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
 confirmClasses(){return act('confirm-classes',{snapshot_sha256:s.session?.snapshot_sha256});},editClasses(){return act('edit-classes');},
 confirmSchema(){return act('confirm-schema',{snapshot_sha256:s.session?.snapshot_sha256});},retry(){if(!['failed','interrupted'].includes(s.session?.status))return;return act('retry',{failed_run_id:s.session.run_id});},
 async openRevision(revision){const token=epoch;try{const row=await read(fetch,prefix+encodeURIComponent(id)+'/revisions/'+revision);if(!dead&&token===epoch){s.selectedRevision=row;notify();}}catch(e){if(!dead&&token===epoch){s.error={message:e.message};notify();}}},
 dispose(){dead=true;epoch++;clear();}};
}
function presentation(result,esc){
 if(!result)return '';
 const groups=[['classes','类别'],['object_properties','对象关系'],['data_properties','数据属性']];
 return groups.filter(([key])=>result[key]?.length).map(([key,title])=>`<h4>${title}（${result[key].length}）</h4><div class="rd-table"><table><thead><tr><th>名称</th><th>定义</th><th>关联范围</th></tr></thead><tbody>${result[key].map(row=>`<tr><td><b>${esc(row.label||row.id)}</b><small>${esc(row.id)}</small></td><td>${esc(row.definition||'')}</td><td>${esc((row.parent_ids||row.domain_ids||row.class_ids||[]).join('、'))}</td></tr>`).join('')}</tbody></table></div>`).join('')+
 (result.competency_questions?.length?'<h4>本体需要回答的问题</h4><ul>'+result.competency_questions.map(q=>`<li>${esc(q.question)}</li>`).join('')+'</ul>':'')+
 (result.clarifications?.length?'<h4>待明确项</h4><ul>'+result.clarifications.map(q=>`<li>${esc(typeof q==='string'?q:q.question||q.message||q.reason||JSON.stringify(q))}</li>`).join('')+'</ul>':'');
}
function mount(host,sid){const esc=g.RoboticsAssets.escape;let c,history=[];const input=document.createElement('textarea');input.setAttribute('aria-label','建模要求');input.maxLength=20000;
 const editor=document.createElement('div'),body=document.createElement('div');host.replaceChildren(body,editor);editor.append(input);const label=document.createElement('label');label.textContent='建模要求';label.htmlFor='rd_message';input.id='rd_message';editor.prepend(label);const send=document.createElement('button');send.textContent='发送建模要求';editor.append(send);
 const startProperties=document.createElement('button');startProperties.textContent='开始关系与属性设计';editor.append(startProperties);
 input.oninput=()=>c.setMessage(input.value);send.onclick=()=>c.sendMessage();
 startProperties.onclick=()=>{if(!c.snapshot().messageText.trim())c.setMessage('请基于已确认的类别、建模要求和上传的 XML，设计对象关系与数据属性；只采用来源支持的信息，不明确的内容列为待明确项。');return c.sendMessage();};
 function render(s){const row=s.session;if(!row){body.textContent=s.error?.message||'正在读取设计…';return;}
 host.dataset.testid='robotics-design';host.dataset.sessionId=sid;host.dataset.stage=row.stage;host.dataset.revision=row.revision;host.dataset.status=row.status;
 const needsProperties=row.stage==='properties'&&row.status==='awaiting_input';
 const selected=s.selectedRevision,result=selected?.state?.result||row.result,ref=row.output_asset_ref,waiting=active(row),failed=['failed','interrupted'].includes(row.status);
 const classNotes=!selected&&row.stage==='properties'?(row.class_confirmation?.clarifications||[]):[];
 const displayResult=classNotes.length&&result?{...result,clarifications:(result.clarifications||[]).filter(note=>!classNotes.includes(note))}:result;
 body.innerHTML=`<h3>模型辅助构建 · ${esc(row.model?.model||'')}</h3><p>${esc(({classes:'类别设计',properties:'关系与属性设计',abox:'实例构建'})[row.stage]||row.stage)} · 修订 ${row.revision} · ${esc(({queued:'排队中',running:'运行中',needs_description:'等待建模要求',awaiting_input:'等待输入',awaiting_confirmation:'等待确认',completed:'已完成',failed:'失败',interrupted:'已中断'})[row.status]||row.status)} · 执行未验证</p>
 ${(s.error||row.error)?`<p role="alert">${esc(s.error?.message||row.error?.message||'设计失败')}</p>`:''}
 <label>设计修订<select aria-label="设计修订"><option value="">当前设计</option>${history.map(r=>`<option value="${r.revision}" ${selected?.revision===r.revision?'selected':''}>修订 ${r.revision}</option>`).join('')}</select></label>
 ${needsProperties&&!selected?`<section role="status" class="rd-next-step"><h4>类别已确认，下一步设计关系与属性</h4><p>确认类别只固定类别范围。请在下方补充关系与属性设计要求，或直接点击“开始关系与属性设计”，按已确认类别和 XML 继续。</p></section><details><summary>查看已确认的类别</summary>${presentation({classes:row.class_confirmation?.classes||result?.classes||[]},esc)}</details>`:result?`<p>${esc(result.reply||'')}</p>${presentation(displayResult,esc)}`:''}
 ${classNotes.length&&!needsProperties?`<details><summary>类别阶段的历史待明确项（类别已确认）</summary>${presentation({clarifications:classNotes},esc)}</details>`:''}
 ${waiting?'<p role="status">正在后台处理，请等待本轮结果；页面会自动更新。</p>':''}
 ${selected?`<section data-testid="robotics-design-diff"><h4>与当前设计的差异</h4><details open><summary>历史设计</summary>${presentation(selected.state.result,esc)}</details><details open><summary>当前设计</summary>${presentation(row.result,esc)}</details></section>`:''}
 <div>${!waiting&&row.stage==='classes'&&row.status==='awaiting_confirmation'?'<button data-op="confirmClasses">确认类别</button>':''}
 ${!waiting&&row.stage==='properties'&&row.status==='awaiting_confirmation'?'<button data-op="confirmSchema">确认骨架并构建实例</button>':''}
 ${!waiting&&row.revision?'<button data-op="editClasses">返回类别修改</button>':''}${failed?'<button data-op="retry">重试设计</button>':''}</div>
 ${ref?`<p>设计候选已保存 · 执行未验证</p><a href="${esc(g.RoboticsAssets.versionLink(ref.ontology_id,ref.version_id))}">查看本体候选</a>`:''}`;
 body.querySelector('select').onchange=e=>{if(e.target.value)c.openRevision(Number(e.target.value));else{c.openSession(sid);}};
 body.querySelectorAll('[data-op]').forEach(b=>{b.disabled=s.busy;b.onclick=()=>c[b.dataset.op]();});
 editor.hidden=waiting||row.stage==='abox';startProperties.hidden=!needsProperties||!!s.messageText.trim();startProperties.disabled=s.busy;send.hidden=needsProperties&&!s.messageText.trim();send.disabled=s.busy;input.placeholder=needsProperties?'可选：填写希望补充的关系、参数或关注点；留空可按已有要求继续。':'填写本轮建模要求或修改意见'; if(input.value!==s.messageText)input.value=s.messageText;
 read(g.fetch.bind(g),prefix+sid+'/revisions').then(r=>{history=r.items;const select=body.querySelector('select');if(select&&!selected)select.innerHTML='<option value="">当前设计</option>'+history.map(r=>`<option value="${r.revision}">修订 ${r.revision}</option>`).join('');}).catch(()=>{});
 }
 c=createController({onChange:render});c.openSession(sid);return c;
}
g.RoboticsDesign={createController,mount};})(window);

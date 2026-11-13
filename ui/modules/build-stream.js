/* Build streams must end with a confirmed result; transport EOF is never success. */
function bcTerminal(message) { const e = new Error(message); e.terminal = true; return e; }

/* 构建作业由服务端持有(DR-101/103)。作业号记在本机,连接断开或刷新页面后凭它接着看。 */
const BC_JOB_KEY = 'bc_job';
let BC_JOB = '';
function bcJobRemember(job) { try { localStorage.setItem(BC_JOB_KEY, JSON.stringify(job)); } catch (_) {} }
function bcJobForget() { BC_JOB = ''; try { localStorage.setItem(BC_JOB_KEY, ''); } catch (_) {} }
function bcJobSaved() {
  try { const j = JSON.parse(localStorage.getItem(BC_JOB_KEY) || 'null'); return j && /^bj_[0-9a-f]{10}$/.test(j.id || '') ? j : null; }
  catch (_) { return null; }
}
function bcJobFetch(url, options) { return fetch(url, window.appRequestOptions ? appRequestOptions(url, options) : options); }
/* 从第 after 条事件起跟随服务端作业,直到给出 done(返回它)或以失败结束(抛出 terminal 错误)。
   网络错误不是 terminal:作业仍在服务端,调用方保留作业号以便之后接续。 */
async function bcFollowJob(id, onEvent, after = 0) {
  for (;;) {
    const r = await bcJobFetch(`/api/build/jobs/${id}?after=${after}&wait=20`, {});
    if (r.status === 404) throw bcTerminal('构建作业记录已不存在，未确认完成；请查看已构建本体');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    for (const ev of d.events || []) {
      if (ev.type === 'done') {
        if (typeof ev.graph_key !== 'string' || !ev.graph_key) throw bcTerminal('构建结果缺少图谱标识，未确认完成');
        return ev;
      }
      if (ev.type === 'error') throw bcTerminal(ev.error || '构建失败');
      onEvent(ev);
    }
    after = d.next;
    if (d.state !== 'running') throw bcTerminal(d.error || '构建未完成');
    onEvent({type:'tick', elapsed:d.elapsed || 0});
  }
}
async function bcCancelJob() {
  if (!BC_JOB) return;
  try {
    const r = await bcJobFetch(`/api/build/jobs/${BC_JOB}/cancel`, {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    toast(r.ok ? '已请求取消：当前步骤完成后停止' : '取消未成功：作业可能已经结束', r.ok);
  } catch (_) { toast('取消请求未送达，请稍后重试', false); }
}
async function bcReadBuildStream(response, onEvent, idleMs = 120000) {
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(`HTTP ${response.status}${detail && detail.error ? ': ' + String(detail.error).slice(0, 500) : ''}`);
  }
  if (!response.body || !/text\/event-stream/i.test(response.headers.get('Content-Type') || '')) {
    throw new Error('服务端未返回构建事件流，请检查服务状态');
  }
  const reader = response.body.getReader(), decoder = new TextDecoder();
  let buffer = '', timer;
  const frame = text => {
    const data = text.split(/\r?\n/).filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).replace(/^ /, '')).join('\n');
    if (!data) return null; // SSE comments/heartbeats contain no JSON payload.
    let event;
    try { event = JSON.parse(data); } catch (_) { throw new Error('构建事件格式无效，未确认完成'); }
    if (!event || typeof event !== 'object') throw new Error('构建事件格式无效，未确认完成');
    if (event.type === 'error') throw bcTerminal(event.error || '构建失败'); // the build itself ended: do not retry or follow
    if (event.type === 'done') {
      if (typeof event.graph_key !== 'string' || !event.graph_key) throw new Error('构建结果缺少图谱标识，未确认完成');
      return event;
    }
    onEvent(event);
    return null;
  };
  try {
    while (true) {
      const part = await Promise.race([reader.read(), new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('构建连接长时间无响应，未确认完成')), idleMs);
      })]);
      clearTimeout(timer);
      buffer += part.done ? decoder.decode() : decoder.decode(part.value, {stream:true});
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const text = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (text.length > 1048576) throw new Error('构建事件过大，已停止接收');
        const done = frame(text);
        if (done) return done;
      }
      if (buffer.length > 1048576) throw new Error('构建事件过大，已停止接收');
      if (part.done) throw new Error('连接中断：未收到完成结果。请先查看已构建本体，确认结果后再重试');
    }
  } finally {
    clearTimeout(timer);
    // Stop reading after a terminal event or a malformed stream; do not retain a locked reader.
    void reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
const BC_IC={true:'<span class="ic" style="color:#22c55e">✓</span>',false:'<span class="ic" style="color:#e0563f">✕</span>'};
let BC_BUSY=false;
/* opts(可选，供分轮构建调用):{q, tables, name}。给了 q 就不读、不清空输入框;
   成功返回 done 事件，失败或未执行返回 null——调用方据此决定是否继续下一轮。 */
async function bcInquire(opts){
 opts=opts&&typeof opts==='object'&&!(opts instanceof Event)?opts:{};
 if(BC_BUSY)return null;
 if(BC.contextLoading){toast('正在读取底本上下文，请稍候');return null;}
 if(BC.contextError){toast('底本上下文未加载，请重新选择本体或改为新建');return null;}
 const auto=!!opts.q;
 // 按计划逐轮构建期间，两轮之间 BC_BUSY 会短暂为 false；手动发送不能插进计划中间
 if(!auto&&window.bcPlanRunning&&bcPlanRunning()){toast('正在按计划逐轮构建，请等待完成或先停止');return null;}
 const q=(opts.q||$('#bc_q').value).trim();if(!q){toast('请描述要构建的本体');return null;}
 const cqs=$('#bc_cq').value.split(/\n+/).map(x=>x.trim()).filter(Boolean);
 const references=window.bcRefsPayload?bcRefsPayload():{version:1,industry:{id:$('#bc_ref_industry').value,mode:$('#bc_ref_industry_mode').value},ontology_standard:{id:$('#bc_ref_standard').value,mode:$('#bc_ref_standard_mode').value}};
 const request={q,source:BC.src,name:auto?(opts.name||''):$('#bc_name').value,skills:[...BC.skills],cqs,references,base_graph:BC.base||''};
 const workload=Array.isArray(opts.workload)?opts.workload:($('#bc_workload')?.value||'').split(/\n+/).map(x=>x.trim()).filter(Boolean);
 if(workload.length)request.workload=workload;
 if(Array.isArray(opts.tables)&&opts.tables.length)request.tables=opts.tables;
 BC_BUSY=true;$('#bc_send').disabled=true;$('#bc_send').textContent='构建中…';if(!auto)$('#bc_q').value='';
 if(!BC.welcomed||$('#bc_log').querySelector('.bc-welcome'))$('#bc_log').innerHTML='';
 const ui=bcTraceStart(q,BC.base?BC.baseName:'',window.bcRefsText?bcRefsText(references):'—');
 const {trace}=ui;
 const controller=new AbortController();let headerTimer=setTimeout(()=>controller.abort(),120000);
 try{
  const response=await bcJobFetch('/api/build/inquire',{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},signal:controller.signal,body:JSON.stringify(request)});
  clearTimeout(headerTimer);headerTimer=null;
  const jobId=response.ok?(response.headers.get('X-Build-Job')||''):'';
  if(jobId){BC_JOB=jobId;bcJobRemember({id:jobId,q,base:BC.base?BC.baseName:'',src:BC.src,ts:Date.now()});ui.cancellable(true);}
  let done;
  try{done=await bcReadBuildStream(response,ui.on);}
  catch(e){
   // 连接断了但构建在服务端继续:改为按作业号接续,而不是报失败
   if(!jobId||e.terminal)throw e;
   const after=ui.seen();                             // 先记下已收到的服务端事件数,下面这条提示是本地的,不计入
   ui.on({type:'status',text:'连接中断，正在从服务端接续…'});
   done=await bcFollowJob(jobId,ui.on,after);
  }
  if(!request.base_graph&&$('#bc_name').value===request.name)$('#bc_name').value='';
  bcJobForget();
  return bcSettle(ui,q,done);
 }catch(e){
  const error=e&&e.name==='AbortError'?'连接长时间无响应，未确认完成':String(e&&e.message||e);
  const pending=BC_JOB&&!(e&&e.terminal);            // 作业仍可能在服务端执行:留着作业号,刷新页面可接续
  if(!pending)bcJobForget();
  ui.fail(error,pending?'构建可能仍在服务端进行；刷新页面可接着查看进度。':'本轮诉求已保留；请先检查已构建本体，再决定是否重试。');
  if(!auto&&!$('#bc_q').value.trim())$('#bc_q').value=q;
  bcBuilt();
  return null;
 }finally{
  clearTimeout(headerTimer);ui.cancellable(false);BC_BUSY=false;$('#bc_send').disabled=false;ui.log.setAttribute('aria-busy','false');bcHintSync();
 }
}
/* 构建过程的显示:一条用户诉求 + 步骤、状态、过程输出。bcInquire 与 bcResume 共用。 */
function bcTraceStart(q,baseName,refsText,resumed){
 const log=$('#bc_log');log.setAttribute('aria-busy','true');
 log.insertAdjacentHTML('beforeend',`<div class="bc-user">${esc(q)}</div>`);
 const trace=document.createElement('div');trace.className='bc-trace';
 trace.innerHTML=(baseName?`<div style="margin:0 0 7px;padding:5px 9px;border-left:3px solid var(--acc);background:#f2f7ff;border-radius:0 5px 5px 0;font-size:11.5px;color:#24406e">继续构建 · 本轮结果将并入「<b>${esc(baseName)}</b>」</div>`:'')
  +`<div style="margin:0 0 7px;font-size:11.5px;color:var(--sub)">${resumed?'接续上次未看完的构建':'构建参照 · '+esc(refsText||'—')}</div>`
  +'<div class="bc-steps"></div><div class="bc-status" role="status" aria-live="polite"><span class="bc-spin"></span> 多智能体引擎接入…</div>'
  +'<details class="bc-proc" style="display:none"><summary><span class="ar">▶</span>过程输出 <span class="muted pn">0 行</span></summary><div class="bc-proclog"></div></details>';
 log.appendChild(trace);log.scrollTop=1e9;
 const stepsEl=trace.querySelector('.bc-steps'),statusEl=trace.querySelector('.bc-status'),
       procEl=trace.querySelector('.bc-proc'),procLog=trace.querySelector('.bc-proclog'),procN=trace.querySelector('.pn');
 const steps=[];let status='',elapsed=0,nlog=0;
 const draw=()=>{stepsEl.innerHTML=steps.map(s=>`<div class="st">${BC_IC[!!s.ok]}<span class="tx">${esc(s.info||s.step)}</span><span class="tm">${esc(s.ts||'')}</span></div>`).join('');
  statusEl.style.display=status?'flex':'none';
  statusEl.innerHTML=status?`<span class="bc-spin"></span> ${esc(status)}${elapsed?`<span class="el">已用 ${esc(Math.round(elapsed))}s</span>`:''}`:'';
  log.scrollTop=1e9;};
 const pushLog=(ev)=>{nlog++;procEl.style.display='block';procN.textContent=nlog+' 行';
  const raw=String(ev.text||'').startsWith('│');
  procLog.insertAdjacentHTML('beforeend',`<div class="${raw?'raw':''}"><span class="lt">${esc(ev.ts||'')}</span>${esc(ev.text||'')}</div>`);
  // Keep long sessions responsive while retaining the most recent process output.
  if(procLog.children.length>1000)procLog.firstElementChild.remove();
  if(procEl.open)procLog.scrollTop=1e9;};
 let seen=0,cancelEl=null;
 const on=ev=>{
  if(ev.type!=='tick')seen++;
  if(ev.type==='step'){steps.push(ev);status='';elapsed=0;draw();}
  else if(ev.type==='status'){status=ev.text;elapsed=0;draw();}
  else if(ev.type==='log')pushLog(ev);
  else if(ev.type==='tick'){elapsed=ev.elapsed||0;draw();}
 };
 return {trace,log,on,seen:()=>seen,
  cancellable(show){
   if(show&&!cancelEl){cancelEl=document.createElement('button');cancelEl.className='bc-cancel';cancelEl.textContent='取消构建';
    cancelEl.style.cssText='margin:6px 0 0;font-size:11.5px;padding:3px 10px;border:1px solid var(--line);border-radius:4px;background:#fff;color:var(--sub);cursor:pointer';
    cancelEl.onclick=bcCancelJob;trace.appendChild(cancelEl);}
   if(cancelEl)cancelEl.style.display=show?'':'none';},
  finish(){status='';draw();if(nlog)procN.textContent=nlog+' 行 · 可展开回看（保留最近 1000 行）';},
  fail(error,hint){status='';draw();
   trace.insertAdjacentHTML('beforeend',`<div class="bc-failure" role="alert">${esc(error)}<div>${esc(hint)}</div></div>`);}};
}
function bcSettle(ui,q,done){
 ui.finish();
 bcRenderResult(ui.log,done);
 if(!BC_CONV)BC_CONV={id:'b'+Math.random().toString(36).slice(2,9),title:'',turns:[],ts:0};
 BC_CONV.turns.push({q,done});if(!BC_CONV.title)BC_CONV.title=q.slice(0,40);BC_CONV.ts=Date.now();bcSaveConv();
 BC.turns=BC_CONV.turns.length;$('#bc_conv_n').textContent='· 第 '+BC.turns+' 次构建';
 loaded.graph=0;bcBuilt();ui.log.scrollTop=1e9;
 return done;
}
/* 进入构建页时调用:本机记着一个没看完的作业就接着看——仍在运行则跟到结束,已结束则给出结果或失败原因。 */
async function bcResume(){
 const job=bcJobSaved();
 if(!job||BC_BUSY)return null;
 if(job.src&&window.BC&&BC.src&&job.src!==BC.src)return null;      // 另一个数据来源下发起的作业,不在这里接续
 BC_BUSY=true;BC_JOB=job.id;$('#bc_send').disabled=true;$('#bc_send').textContent='构建中…';
 if($('#bc_log').querySelector('.bc-welcome'))$('#bc_log').innerHTML='';
 const q=String(job.q||'(上次的构建)');
 const ui=bcTraceStart(q,job.base||'','',true);ui.cancellable(true);
 try{
  const done=await bcFollowJob(job.id,ui.on,0);
  bcJobForget();
  return bcSettle(ui,q,done);
 }catch(e){
  const pending=!(e&&e.terminal);
  if(!pending)bcJobForget();
  ui.fail(String(e&&e.message||e),pending?'暂时连不上服务；稍后刷新页面可再次接续。':'可在已构建本体里确认结果，或重新发起构建。');
  bcBuilt();
  return null;
 }finally{
  ui.cancellable(false);BC_BUSY=false;$('#bc_send').disabled=false;ui.log.setAttribute('aria-busy','false');bcHintSync();
 }
}

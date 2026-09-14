/* Build streams must end with a confirmed result; transport EOF is never success. */
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
    if (event.type === 'error') throw new Error(event.error || '构建失败');
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
async function bcInquire(){
 if(BC_BUSY)return;
 if(BC.contextLoading){toast('正在读取底本上下文，请稍候');return;}
 if(BC.contextError){toast('底本上下文未加载，请重新选择本体或改为新建');return;}
 const q=$('#bc_q').value.trim();if(!q){toast('请描述要构建的本体');return;}
 const cqs=$('#bc_cq').value.split(/\n+/).map(x=>x.trim()).filter(Boolean);
 const references=window.bcRefsPayload?bcRefsPayload():{version:1,industry:{id:$('#bc_ref_industry').value,mode:$('#bc_ref_industry_mode').value},ontology_standard:{id:$('#bc_ref_standard').value,mode:$('#bc_ref_standard_mode').value}};
 const request={q,source:BC.src,name:$('#bc_name').value,skills:[...BC.skills],cqs,references,base_graph:BC.base||''};
 BC_BUSY=true;$('#bc_send').disabled=true;$('#bc_send').textContent='构建中…';$('#bc_q').value='';
 if(!BC.welcomed||$('#bc_log').querySelector('.bc-welcome'))$('#bc_log').innerHTML='';
 const log=$('#bc_log');log.setAttribute('aria-busy','true');
 log.insertAdjacentHTML('beforeend',`<div class="bc-user">${esc(q)}</div>`);
 const trace=document.createElement('div');trace.className='bc-trace';
 trace.innerHTML=(BC.base?`<div style="margin:0 0 7px;padding:5px 9px;border-left:3px solid var(--acc);background:#f2f7ff;border-radius:0 5px 5px 0;font-size:11.5px;color:#24406e">继续构建 · 本轮结果将并入「<b>${esc(BC.baseName)}</b>」</div>`:'')
  +`<div style="margin:0 0 7px;font-size:11.5px;color:var(--sub)">构建参照 · ${esc(window.bcRefsText?bcRefsText(references):'—')}</div>`
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
 const controller=new AbortController();let headerTimer=setTimeout(()=>controller.abort(),120000);
 try{
  const response=await fetch('/api/build/inquire',{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream'},signal:controller.signal,body:JSON.stringify(request)});
  clearTimeout(headerTimer);headerTimer=null;
  const done=await bcReadBuildStream(response,ev=>{
   if(ev.type==='step'){steps.push(ev);status='';elapsed=0;draw();}
   else if(ev.type==='status'){status=ev.text;elapsed=0;draw();}
   else if(ev.type==='log')pushLog(ev);
   else if(ev.type==='tick'){elapsed=ev.elapsed||0;draw();}
  });
  status='';draw();if(nlog)procN.textContent=nlog+' 行 · 可展开回看（保留最近 1000 行）';
  if(!request.base_graph&&$('#bc_name').value===request.name)$('#bc_name').value='';
  bcRenderResult(log,done);
  if(!BC_CONV)BC_CONV={id:'b'+Math.random().toString(36).slice(2,9),title:'',turns:[],ts:0};
  BC_CONV.turns.push({q,done});if(!BC_CONV.title)BC_CONV.title=q.slice(0,40);BC_CONV.ts=Date.now();bcSaveConv();
  BC.turns=BC_CONV.turns.length;$('#bc_conv_n').textContent='· 第 '+BC.turns+' 次构建';
  loaded.graph=0;bcBuilt();log.scrollTop=1e9;
 }catch(e){
  status='';draw();const error=e&&e.name==='AbortError'?'连接长时间无响应，未确认完成':String(e&&e.message||e);
  trace.insertAdjacentHTML('beforeend',`<div class="bc-failure" role="alert">${esc(error)}<div>本轮诉求已保留；请先检查已构建本体，再决定是否重试。</div></div>`);
  if(!$('#bc_q').value.trim())$('#bc_q').value=q;
  bcBuilt();
 }finally{
  clearTimeout(headerTimer);BC_BUSY=false;$('#bc_send').disabled=false;log.setAttribute('aria-busy','false');bcHintSync();
 }
}

/* 指标中心的图谱切换、计算规则与溯源 — DR-054 / DR-186 / DR-187。 */
// 指标状态(DR-054):certified 业务已确认 > verified 已核验 > candidate 待核验；旧形状指标只有启发式计算规则
const MST={certified:['ok','业务已确认'],verified:['ok','已核验'],candidate:['warn','待核验'],deprecated:['evt','已停用'],heuristic:['warn','旧规则(启发式)']};
function mStatus(x){return x.status||(x.measure&&x.measure.agg?'candidate':'heuristic');}
function mstTag(st){const t=MST[st]||['evt',st||'-'];return `<span class="tag ${t[0]}">${esc(t[1])}</span>`;}
function mCaliber(x){if(!(x.measure&&x.measure.agg))return '';const a=x.measure.agg.toUpperCase().replace('COUNT_DISTINCT','COUNT DISTINCT'),c=x.measure.col||'*';
 const f=(x.filters||[]).map(f=>`${f.col} ${f.op}${f.value!==undefined&&f.value!==null?' '+JSON.stringify(f.value):''}`).join(' AND ');
 return `${a}(${c})${f?' WHERE '+f:''}${x.time&&x.time.col?' 按 '+x.time.col+'/'+((x.time.grain||['month'])[0]):''}`;}
function mGraph(){return ($('#m_sel')&&$('#m_sel').value)||'demo';}
const METRIC_REQUEST_TIMEOUT = 30000;
const metricRequest = (url, options) => J(url, {...options, timeoutMs: METRIC_REQUEST_TIMEOUT});
let M_GRAPHS=null, M_GRAPHS_LOADING=null, M_METRICS_REQUEST=0, M_LINEAGE_REQUEST=0;
async function metrics({keepDetails=false}={}){
 // DR-186: only the current request may update the table and its summary.
 const request=++M_METRICS_REQUEST, body=$('#m_body'), kpi=$('#m_kpi');
 const details=keepDetails?$('#m_lineage'):null;
 if(!details){body.innerHTML='<div class="muted" role="status">正在加载指标…</div>';kpi.textContent='';}
 const fail=message=>{
  if(details)toast(message,false);
  else body.innerHTML=`<div class="muted" role="alert">${esc(message)}</div><button type="button" onclick="metrics()">重试读取</button>`;
  return false;
 };
 if(!M_GRAPHS){
  const graphs=await (M_GRAPHS_LOADING??=JL('/api/graphs',METRIC_REQUEST_TIMEOUT).finally(()=>{M_GRAPHS_LOADING=null;}));
  if(request!==M_METRICS_REQUEST)return false;
  if(!graphs)return fail('图谱列表加载失败。');
  M_GRAPHS=graphs;
  const sel=$('#m_sel');
  sel.innerHTML=graphOptions(M_GRAPHS,g=>g.name);
  if(M_GRAPHS.some(g=>g.id==='demo'))sel.value='demo';
 }
 const g=mGraph(), m=await metricRequest('/api/metrics?graph='+encodeURIComponent(g));
 if(request!==M_METRICS_REQUEST||g!==mGraph())return false;
 if(m.error)return fail(m.error);
 const names={atomic:'原子指标',derived:'派生指标',composite:'复合指标'};
 const cnt={};Object.values(m).forEach(a=>(a||[]).forEach(x=>{const st=mStatus(x);cnt[st]=(cnt[st]||0)+1;}));
 kpi.innerHTML=['certified','verified','candidate','deprecated','heuristic'].filter(k=>cnt[k]).map(k=>`${mstTag(k)}<span class="muted" style="margin-right:8px">${cnt[k]}</span>`).join('');
 if(!details)body.innerHTML='<div class="card" id="m_lineage" style="display:none"></div><div id="m_tables"></div>';
 $('#m_tables').innerHTML=
  Object.entries(m).map(([k,arr])=>`<div class="card"><h3>${names[k]||k}(${arr.length})<span class="muted">· 点行查看计算规则与数据溯源</span></h3>
  <div class="scroll" style="max-height:26vh"><table><tr><th>名称</th><th>状态</th><th>计算规则</th><th>来源表</th><th>取值列</th><th>单位</th><th>说明</th></tr>`+
  arr.map(x=>`<tr style="cursor:pointer" onclick="lineage(${jsAttr(x.id||x.name)})"><td><b>${esc(x.name)}</b></td><td>${mstTag(mStatus(x))}</td><td><code style="font-size:12px">${esc(mCaliber(x))||'<span class=muted>未声明</span>'}</code></td><td>${esc(x.table)}</td><td>${esc(x.value_col)}</td><td>${esc(x.unit)}</td><td>${esc(x.desc||x.formula)}</td></tr>`).join('')+'</table></div></div>').join('');
 return true;
}
async function metricReadjudicate(){const g=mGraph(), view=M_METRICS_REQUEST;toast('重跑核验中…');
 const d=await metricRequest('/api/metric/adjudicate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({graph:g})});
 if(d.error){toast(d.error,false);return;}
 toast(`核验完成 · 规则 ${d.contracts} 条 · 已核验 ${d.verified} 条`,true);
 if(view===M_METRICS_REQUEST&&g===mGraph())metrics();
}
async function metricStatus(name,status){const g=mGraph(), details=$('#m_lineage');const who=($('#ml_who')&&$('#ml_who').value.trim())||'';const reason=($('#ml_reason')&&$('#ml_reason').value.trim())||'';
 if(status==='certified'&&!who){toast('业务确认须填写确认人(姓名或工号)',false);$('#ml_who').focus();return;}
 try{localStorage.setItem('rv_reviewer',who);}catch(e){}
 const d=await metricRequest('/api/metric/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({graph:g,metric:name,status,reviewer:who,reason})});
 if(d.error){toast(d.error,false);return;}
 toast(`已置为 ${MST[status]?MST[status][1]:status}`,true);
 if(details&&details===$('#m_lineage')&&g===mGraph()&&await metrics({keepDetails:true})&&details.dataset.metric===name)lineage(name);
}
const LAYER_CN={atomic:'原子指标',derived:'派生指标',composite:'复合指标'};
async function lineage(name){
 const el=$('#m_lineage');if(!el)return;
 const g=mGraph(), request=++M_LINEAGE_REQUEST;
 el.dataset.metric=name;
 el.style.display='block';
 el.innerHTML='<p class="muted" role="status">正在读取指标详情…</p>';
 const query='?graph='+encodeURIComponent(g)+'&name='+encodeURIComponent(name);
 const [d,c]=await Promise.all([metricRequest('/api/metric/lineage'+query),metricRequest('/api/metric/contract'+query)]);
 if(request!==M_LINEAGE_REQUEST||el!==$('#m_lineage')||g!==mGraph())return;
 if(d.error||c.error){el.innerHTML=`<h3>${esc(name)}</h3><div class="muted" role="alert">${esc(d.error||c.error)}</div><button type="button" onclick="lineage(${jsAttr(name)})">重试读取</button>`;return;}
 const m=d.metric||{},ly=m.layer,st=mStatus(m),ev=m.evidence||{},ref=ev.reference||{};
 const srcLine=d.source_table
   ? `<b>数据来源</b>: ${esc(d.source_table)} . ${esc(d.source_col||'-')} <a href="#" onclick="event.preventDefault();document.querySelector('[data-p=catalog]').click();setTimeout(()=>tbl(${jsAttr((d.source_table||'').toLowerCase())}),400)">查表↗</a>`
   : `<b>数据来源</b>: <span class="muted">${ly==='derived'?'派生指标 · 由原子指标按业务规则汇总/计算(未直接绑表)':ly==='composite'?'复合指标 · 由多指标按公式组合计算(未直接绑表)':'未绑定数据表'}</span>`;
 const isC=!!(c&&c.is_contract);
 const evLine=isC?`<div class="step"><b>核验证据</b>: ${ev.executed?'<span class="tag ok">已执行</span>':'<span class="tag warn">未执行</span>'}`+
   (ev.match===true?'<span class="tag ok">与参照一致</span>':ev.match===false?'<span class="tag warn">与参照不一致</span>':'<span class="tag evt">无参照</span>')+
   (ref.source?`<span class="muted"> 参照:${esc(ref.source)}${ref.note?' · '+esc(ref.note):''}</span>`:'')+(ev.error?`<div class="muted" style="color:var(--warn)">${esc(ev.error)}</div>`:'')+
   (ev.value!==undefined?`<div class="muted">标量值:${esc(String(ev.value))}${m.unit?' '+esc(m.unit):''}${ev.checked_at?' · 核验于 '+esc(ev.checked_at):''}</div>`:'')+`</div>`:'';
 const dims=(c&&c.allowed_dimensions)||null;
 const dimLine=dims?`<div class="step"><b>可下钻维度</b>: ${(dims.columns||[]).map(x=>`<code>${esc(x)}</code>`).join(' ')||'<span class=muted>无</span>'}${(dims.objects||[]).length?' · 关联对象: '+dims.objects.map(o=>`${esc(o.object)}<span class="muted">(${esc(o.table)}${o.fan_out_risk?' · 父侧，有扇出风险':''})</span>`).join('、'):''}</div>`:'';
 const prov=(m.provenance||[]).map(p=>`${esc(p.kind||'-')}:${esc(p.source||'')}`).join('; ');
 const who=localStorage.getItem('rv_reviewer')||'';
 const actions=isC?`<div class="step" style="border-top:1px dashed #E5E9F2;padding-top:8px">
   <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
    确认人 <input id="ml_who" placeholder="姓名或工号" value="${esc(who)}" style="width:140px"> 意见 <input id="ml_reason" placeholder="确认依据(可选)" style="width:260px">
    ${st!=='certified'?`<button onclick="metricStatus(${jsAttr(name)},'certified')" title="由业务人员确认计算规则；等价于关系的人工断言">业务确认</button>`:''}
    ${st!=='deprecated'?`<button class="ghost" onclick="metricStatus(${jsAttr(name)},'deprecated')">停用</button>`:''}
    ${(st==='certified'||st==='deprecated')?`<button class="ghost" onclick="metricStatus(${jsAttr(name)},'candidate')">撤回为待核验</button>`:''}
   </div><div class="muted" style="margin-top:4px">「已核验」只能由核验产生，人工只能授予「业务已确认」或「停用」。</div></div>`
  :`<div class="step muted">该指标是旧形状(未声明聚合方式)，不能做业务确认；先在构建时反解或补充计算规则。</div>`;
 el.innerHTML=`<h3>${esc(m.name||name)} ${mstTag(st)}<span class="tag ok">${esc(LAYER_CN[ly]||ly)}</span>${m.unit?`<span class="tag" style="background:#F4F4F5;color:#606266">${esc(m.unit)}</span>`:''}${m.certified_by?`<span class="muted" style="font-size:12px">确认人 ${esc(m.certified_by)}${m.certified_at?' · '+esc(m.certified_at):''}</span>`:''}</h3>
  ${isC?`<div class="step"><b>计算规则</b>: <code>${esc(c.caliber||'')}</code></div><div class="step"><b>编译 SQL</b>: <code style="font-size:12px;white-space:pre-wrap">${esc(c.compiled_sql||c.compile_error||'')}</code></div>`:''}
  ${m.desc?`<div class="step"><b>业务说明</b>: ${esc(m.desc)}</div>`:''}
  <div class="step">${srcLine}</div>${d.formula?`<div class="step"><b>计算公式</b>: ${esc(d.formula)}</div>`:''}
  ${evLine}${dimLine}
  ${prov?`<div class="step"><b>来源</b>: <span class="muted">${prov}</span></div>`:''}
  ${(d.inputs||[]).length?`<div class="step"><b>输入指标</b>: ${d.inputs.map(i=>`<a href="#" onclick="event.preventDefault();lineage(${esc(JSON.stringify(i))})">${esc(i)}</a>`).join('、')}</div>`:''}
  <div class="step"><b>被本体对象引用(${(d.referenced_by||[]).length})</b>: ${(d.referenced_by||[]).map(r=>`${esc(r.object)}<span class="muted">(${esc(r.kind)})</span>`).join('、')||'<span class=muted>无(该指标未挂载到本体对象)</span>'}</div>
  <div class="step"><b>编码</b>: <code>${esc(m.id||'-')}</code>${m.review_reason?` · <span class="muted">${esc(m.review_reason)}</span>`:''}</div>${actions}`;
 el.scrollIntoView({behavior:'smooth'});}

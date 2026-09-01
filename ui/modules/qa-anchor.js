/* 深度问数 · 本体约束解释层（DR-032 / DR-043）。
 * 关系图是可展开的技术证据；主视图先讲清“为什么选、实际用了什么”。 */
var DQ_ANC_LAST=null, DQ_ANC_OPEN=false;
const DQ_ANC_C={'关键词命中':['#4A5FF3','#EEF2FF'],'数据源限定':['#4A5FF3','#EEF2FF'],'核心事实表':['#7c3aed','#F3EEFF'],
 '沿本体关系召回':['#0ea5e9','#E8F6FE'],'无命中·默认候选':['#94a3b8','#F1F5F9']};

function dqReasonInfo(reason){
 const m={
  '关键词命中':['问题直接命中','问题中的业务词、别名或字段名称直接匹配到该对象'],
  '数据源限定':['用户限定的数据表','由本次选择的数据源或数据表明确限定'],
  '核心事实表':['分析主表候选','作为常用事实表进入规划范围，是否采用仍由 SQL 决定'],
  '沿本体关系召回':['关系扩展候选','为可能的维度补充或表连接提供候选对象'],
  '无命中·默认候选':['默认候选','问题没有明确命中时提供的保守备选']};
 return m[reason]||[reason||'其他依据','由本体规则选入 SQL 规划上下文'];
}

function dqAnchorWithResults(a,results){
 /* 兼容升级前保存的历史记录：旧记录没有 explanation，但保留了结果 SQL。
  * 直接从 SQL 的 FROM/JOIN 恢复执行事实，不能用“1 个不同表”猜“0 个 JOIN”
  * （同一张表也可能自连接）。新记录已有后端摘要时不重复计算。 */
 if(!a||(a.explanation&&a.explanation.phase==='complete')||!(results||[]).length)return a;
 const tables=[],ctes=new Set();let joins=0,sqls=0;
 (results||[]).forEach(r=>{const sql=String((r||{}).sql||'');if(!sql)return;sqls++;
  joins+=(sql.match(/\bjoin\b/ig)||[]).length;
  for(const x of sql.matchAll(/(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_."`\[\]]*)\s+as\s*\(/ig))ctes.add(x[1].replace(/["`\[\]]/g,'').toLowerCase());
  for(const x of sql.matchAll(/\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_."`\[\]]*)/ig)){
   let t=x[1].replace(/["`\[\]]/g,'').toLowerCase();if(t.startsWith('up.'))t=t.split('.').pop();
   if(t&&!ctes.has(t)&&!tables.includes(t))tables.push(t);
  }});
 const used=new Set(tables),objects=a.objects||[];
 return {...a,used:tables,explanation:{phase:'complete',ontology_object_count:(a.ontology||{}).objects||objects.length,
  context_object_count:objects.length,relation_candidate_count:(a.relations||[]).length,used_tables:tables,
  context_only_tables:objects.filter(o=>o.table&&!used.has(String(o.table).toLowerCase())).map(o=>o.table),
  sql_join_count:joins,result_sql_count:sqls}};
}

function dqAnchorModel(a){
 a=a||{};const all=a.objects||[],rels=(a.relations||[]).filter(r=>r&&r.s&&r.t),x=a.explanation||{};
 const usedTables=(x.used_tables||a.used||[]).map(String),used=new Set(usedTables.map(t=>t.toLowerCase()));
 const usedObjects=all.filter(o=>used.has(String(o.table||'').toLowerCase()));
 const contextOnly=all.filter(o=>!used.has(String(o.table||'').toLowerCase()));
 const grouped={};all.forEach(o=>(grouped[o.reason||'其他依据']=grouped[o.reason||'其他依据']||[]).push(o));
 const joinCount=Number.isInteger(x.sql_join_count)?x.sql_join_count:null;
 const complete=x.phase==='complete'||usedTables.length>0;
 return {all,rels,x,usedTables,used,usedObjects,contextOnly,grouped,joinCount,complete,ont:a.ontology||{}};
}

function dqObjectName(o){
 const cn=String(o.cn||'').trim(),tb=String(o.table||'').trim();
 return cn&&cn.toLowerCase()!==tb.toLowerCase()?cn:tb;
}
function dqObjectRow(o){
 const hits=(o.hits||[]).filter(Boolean);
 return `<div class="qa-object"><span class="mark">✓</span><div class="main"><div class="cn">${esc(dqObjectName(o))}</div>`+
  `<code>${esc(o.table||'')}</code>${hits.length?`<div class="why">问句命中：${hits.map(esc).join('、')}</div>`:''}</div></div>`;
}
function dqTableChips(objects,limit){
 const xs=(objects||[]).slice(0,limit||50).map(o=>`<span title="${esc(o.reason||'')}">${esc(dqObjectName(o))}<code>${esc(o.table||'')}</code></span>`);
 if((objects||[]).length>xs.length)xs.push(`<span>另有 ${(objects||[]).length-xs.length} 张</span>`);
 return `<div class="qa-table-chips">${xs.join('')}</div>`;
}

function dqAnchorGraph(a,m){
 const all=m.all,rels=m.rels,CAP=16,LIM=20,ai={};
 all.forEach((o,i)=>{if(o.table)ai[String(o.table).toLowerCase()]=i;});
 const ends=r=>[ai[String(r.s).toLowerCase()],ai[String(r.t).toLowerCase()]];
 const deg={};rels.forEach(r=>{const e=ends(r);if(e[0]==null||e[1]==null||e[0]===e[1])return;deg[e[0]]=(deg[e[0]]||0)+1;deg[e[1]]=(deg[e[1]]||0)+1;});
 const keep=all.length<=CAP?all.map((_,i)=>i):all.map((_,i)=>i).sort((x,y)=>(deg[y]||0)-(deg[x]||0)).slice(0,CAP);
 const inK=new Set(keep),drawn=rels.filter(r=>{const e=ends(r);return e[0]!=null&&e[1]!=null&&e[0]!==e[1]&&inK.has(e[0])&&inK.has(e[1]);});
 const cut=all.length-keep.length,cutR=rels.length-drawn.length,adj={};
 drawn.forEach(r=>{const e=ends(r);(adj[e[0]]=adj[e[0]]||[]).push(e[1]);(adj[e[1]]=adj[e[1]]||[]).push(e[0]);});
 const order=[],seen=new Set();keep.slice().sort((x,y)=>(adj[y]||[]).length-(adj[x]||[]).length).forEach(st=>{if(seen.has(st))return;const q=[st];seen.add(st);while(q.length){const c=q.shift();order.push(c);(adj[c]||[]).forEach(n=>{if(!seen.has(n)){seen.add(n);q.push(n);}});}});
 const COL=Math.min(4,order.length),CW=164,CH=84,PAD=18,NW=144,NH=48,pos={};
 order.forEach((n,k)=>{pos[n]={x:PAD+(k%COL)*CW+CW/2,y:PAD+Math.floor(k/COL)*CH+NH/2};});
 const W=PAD*2+COL*CW,H=PAD*2+Math.ceil(order.length/COL)*CH;
 let svg=`<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:${W}px;height:auto;display:block;margin:auto" role="img" aria-label="本体召回对象与候选关系图">`;
 drawn.forEach(r=>{const e=ends(r),st=EST[r.status]||EST.gap;svg+=`<line x1="${pos[e[0]].x}" y1="${pos[e[0]].y}" x2="${pos[e[1]].x}" y2="${pos[e[1]].y}" stroke="${st.c}" stroke-width="1.6"${estDash(st)?` stroke-dasharray="${estDash(st)}"`:''}><title>${esc(r.key||'')}</title></line>`;});
 order.forEach(n=>{const o=all[n],p=pos[n],c=DQ_ANC_C[o.reason]||DQ_ANC_C['关键词命中'],on=m.used.has(String(o.table||'').toLowerCase());
  const tb=String(o.table||''),label=dqObjectName(o),shown=label.length>11?label.slice(0,10)+'…':label;
  svg+=`<g><title>${esc(label)} · ${esc(tb)} · ${esc(o.reason||'')}</title><rect x="${p.x-NW/2}" y="${p.y-NH/2}" width="${NW}" height="${NH}" rx="7" fill="${on?'#effaf3':c[1]}" stroke="${on?'#16924b':c[0]}" stroke-width="${on?2.4:1}"${m.complete&&!on?' opacity="0.58"':''}/>`+
   `<text x="${p.x}" y="${p.y-3}" text-anchor="middle" font-size="12" font-weight="${on?'650':'500'}" fill="#273244">${esc(shown)}${on?' ✓':''}</text>`+
   `<text x="${p.x}" y="${p.y+13}" text-anchor="middle" font-size="9.5" fill="#697386">${esc(tb.length>25?tb.slice(0,24)+'…':tb)}</text></g>`;});
 svg+='</svg>';
 const est={};rels.forEach(r=>{est[r.status]=(est[r.status]||0)+1;});
 const legend=Object.keys(est).map(k=>{const s=EST[k]||EST.gap;return `<span><i style="display:inline-block;width:14px;border-top:2px ${estDash(s)?'dashed':'solid'} ${s.c};margin-right:4px"></i>${esc(s.n)} ${est[k]}</span>`;}).join('');
 const note=(cut||cutR)?`<div class="qa-proof-meta">${cut?`图中只画了 ${CAP} 个对象（共 ${all.length} 个，优先展示有关系的对象）`:''}${cut&&cutR?'；':''}${cutR?`另有 ${cutR} 条关系因端点未入图而未画`:''}。未显示的内容仍在规划上下文中，没有少喂给引擎。</div>`:'';
 return `<div class="qa-graph-wrap">${svg}<div class="qa-graph-legend">${legend}</div>${note}</div>`;
}

function dqAnchorHTML(a,bare){
 const m=dqAnchorModel(a);if(!m.all.length)return '';
 const total=m.x.ontology_object_count||m.ont.objects||m.all.length,nm=(m.ont.names||['示例本体'])[0];
 const joinText=m.joinCount===null?'待 SQL 生成':`${m.joinCount} 个 JOIN`;
 const lead=m.complete
  ?`系统从 <b>${total}</b> 个本体对象中选出 <b>${m.all.length}</b> 个供 SQL 规划；最终 SQL 直接使用 <b>${m.usedTables.length}</b> 张表，包含 <b>${joinText}</b>。${m.contextOnly.length?`其余 ${m.contextOnly.length} 张表只是规划候选，并未被本次 SQL 读取。`:''}`
  :`系统已从 <b>${total}</b> 个本体对象中选出 <b>${m.all.length}</b> 个候选对象，并提供 <b>${m.rels.length}</b> 条候选连接依据。SQL 尚在生成，当前不能把候选对象当作实际使用对象。`;
 const used=m.usedObjects.length?m.usedObjects.map(dqObjectRow).join(''):`<div class="qa-proof-empty">${m.complete?'SQL 未映射到本体中的绑定表，请结合“查看 SQL”核对。':'SQL 尚在生成，完成后这里会只列出真正出现在 FROM / JOIN 中的表。'}</div>`;
 const reasons=Object.entries(m.grouped).map(([reason,objs])=>{const info=dqReasonInfo(reason);return `<div class="qa-reason"><div class="qa-reason-hd"><b>${esc(info[0])}</b><span class="count">${objs.length} 张</span></div><div class="desc">${esc(info[1])}</div>${dqTableChips(objs,4)}</div>`;}).join('');
 let relationNote='';
 if(!m.complete)relationNote=`当前 ${m.rels.length} 条关系是规划候选，SQL 完成前不能判断是否执行了 JOIN。`;
 else if(m.joinCount===0)relationNote=`本次 SQL 没有执行 JOIN。${m.rels.length} 条本体关系只用于向规划器提供可选连接方式，不代表结果使用了 ${m.rels.length} 条关系。`;
 else if(m.joinCount===null)relationNote=`本体提供了 ${m.rels.length} 条候选连接依据；这条历史记录没有保存 JOIN 计数，请以结果区的“查看 SQL”为准。`;
 else relationNote=`本次结果的 SQL 共包含 ${m.joinCount} 个 JOIN；本体提供了 ${m.rels.length} 条候选连接依据。具体采用哪条连接，请以结果区的“查看 SQL”为准。`;
 const ev=m.all.filter(o=>(o.hits||[]).length).slice(0,16),LIM=20;
 const evidence=ev.length?`<details><summary>查看问题命中证据（${ev.length}）</summary><div class="qa-evidence">${ev.map(o=>`<div><code>${esc(o.table||'')}</code> ${esc(dqObjectName(o))} ← ${(o.hits||[]).map(h=>`<span class="qa-hit">${esc(h)}</span>`).join('')}</div>`).join('')}</div></details>`:'';
 const relEvidence=m.rels.length?`<details><summary>查看候选连接依据（${m.rels.length}）</summary><div class="qa-evidence">${m.rels.slice(0,LIM).map(r=>{const st=EST[r.status]||EST.gap;const key=r.key_src==='name_mismatch'?`<span style="color:#9a5b0a">键存疑·已降级 ${esc(r.dropped_key||'')}</span>`:(r.key_src==='note'?'<span class="muted">键·备注回填</span>':(r.has_key===false?'<span style="color:#9a5b0a">未记录连接键</span>':''));return `<div class="qa-rel-row"><span style="color:${st.c}">${r.hop===2?'⋈⋈':'⋈'}</span> <code>${esc(r.key||'')}</code> <span class="muted">${esc(r.verb||'关联')} · ${esc(st.n)}</span> ${key}</div>`;}).join('')}${m.rels.length>LIM?`<div class="qa-proof-meta">另有 ${m.rels.length-LIM} 条候选依据。</div>`:''}</div></details>`:'';
 const inner=`<div class="qa-proof">
  ${a.fallback?`<div class="qa-proof-alert">${esc(a.fallback)}</div>`:''}${a.focus_miss?`<div class="qa-proof-alert">显式选择的 ${a.focus_miss} 张表不在该本体中，已改为按问题召回。</div>`:''}${a.scope_warning?`<div class="qa-proof-alert">${esc(a.scope_warning)}</div>`:''}
  <div class="qa-proof-lead">${lead}</div>
  <div class="qa-proof-flow">
   <div class="qa-proof-step"><div class="k">1 · 分析问题</div><div class="v">${esc(a.question||'未记录问题')}</div><div class="s">识别问题中的业务对象与指标</div></div>
   <div class="qa-proof-step"><div class="k">2 · 本体范围</div><div class="v">${esc(nm)}</div><div class="s">共 ${total} 个对象${m.ont.relations!=null?` / ${m.ont.relations} 条关系`:''}</div></div>
   <div class="qa-proof-step"><div class="k">3 · 进入规划</div><div class="v">${m.all.length} 张候选表</div><div class="s">${m.rels.length} 条候选连接依据</div></div>
   <div class="qa-proof-step actual"><div class="k">4 · SQL 实际执行</div><div class="v">${m.complete?`${m.usedTables.length} 张表`:'生成中'}</div><div class="s">${m.complete?joinText:'完成后据实回填'}</div></div>
  </div>
  <div class="qa-proof-grid">
   <section class="qa-proof-panel actual"><h5>SQL 最终直接使用 <span class="n">FROM / JOIN 中实际出现</span></h5><div class="qa-object-list">${used}</div></section>
   <section class="qa-proof-panel"><h5>为什么选入规划上下文 <span class="n">候选不等于实际使用</span></h5><div class="qa-reason-list">${reasons}</div></section>
  </div>
  <section class="qa-proof-panel"><h5>关系在本次分析中的作用</h5><div class="qa-relation-note ${m.joinCount===0?'zero':''}">${esc(relationNote)}</div>${m.contextOnly.length?`<div class="qa-proof-meta">仅作规划候选的表（${m.contextOnly.length}）：</div>${dqTableChips(m.contextOnly,12)}`:''}${evidence}${relEvidence}<details><summary>查看关系图（技术证据）</summary>${dqAnchorGraph(a,m)}</details></section>
  ${(a.metrics||[]).length?`<div class="qa-proof-meta">命中指标：${a.metrics.slice(0,LIM).map(x=>`${esc(x.name)} <code>${esc(x.table||'')}.${esc(x.col||'')}</code>`).join('、')}</div>`:''}
 </div>`;
 const summary=`本体如何约束本次分析 · 规划候选 ${m.all.length} 张表${m.complete?` · SQL 用 ${m.usedTables.length} 张表 · ${joinText}`:''}`;
 return bare?inner:`<details class="dq-exec" open style="margin:8px 0"><summary class="qa-proof-summary">${summary}</summary>${inner}</details>`;
}

function dqAncBar(a){
 const bar=$('#dq_ancbar');if(!bar)return;DQ_ANC_LAST=a;
 if(!a||!(a.objects||[]).length){bar.style.display='none';return;}
 const m=dqAnchorModel(a),nm=(m.ont.names||['示例本体'])[0],joinLabel=m.complete?(m.joinCount===null?'JOIN 待确认':'个 JOIN'):'SQL 生成中';
 bar.style.display='block';bar.innerHTML=`<div class="hd"><span>本体约束</span><span class="ont">${esc(nm)}</span><span class="fun"><span class="qa-bar-chip"><b>${m.all.length}</b> 张规划候选</span><span class="qa-bar-chip actual"><b>${m.complete?m.usedTables.length:'—'}</b> 张 SQL 实际使用</span><span class="qa-bar-chip actual"><b>${m.complete?(m.joinCount===null?'—':m.joinCount):'—'}</b> ${joinLabel}</span></span>${m.complete&&m.contextOnly.length?`<span class="qa-bar-note">其余 ${m.contextOnly.length} 张表和 ${m.rels.length} 条关系仅作规划候选</span>`:''}${a.fallback?`<span class="qa-proof-alert" style="margin:0;padding:3px 7px">${esc(a.fallback)}</span>`:''}<span class="act"><button type="button" class="qa-link" onclick="dqAncToggle()">${DQ_ANC_OPEN?'收起说明 ▴':'查看约束说明 ▾'}</button><button type="button" class="qa-link" onclick="dqAncHighlight()" title="切到本体图谱页并定位本次召回对象">在图谱中定位 ↗</button></span></div>${DQ_ANC_OPEN?`<div class="body">${dqAnchorHTML(a,true)}</div>`:''}`;
}
function dqAncToggle(){DQ_ANC_OPEN=!DQ_ANC_OPEN;dqAncBar(DQ_ANC_LAST);}
function dqAncHighlight(){
 const a=DQ_ANC_LAST;if(!a)return;const gk=((a.ontology||{}).keys||['demo'])[0];
 GRAPH_HL={key:gk,set:new Set((a.objects||[]).map(o=>o.key)),used:new Set((a.used||[]).map(t=>String(t).toLowerCase()))};
 document.querySelector('[data-p=graph]').click();setTimeout(()=>{const sel=$('#g_sel');if(!sel)return;if(![...sel.options].some(o=>o.value===gk)){toast('该图谱不在图谱列表中，无法定位',false);return;}if(sel.value===gk){drawGraph(gk);return;}sel.value=gk;drawGraph(gk);},350);
}

/* DR-043：数据目录页面模块。依赖主应用提供的 J/$/esc/cell/KCOL/lineage。 */
let CAT=[],CAT_DOM='全部';
const DOMS={'全部':()=>1,'维度dim':n=>/^dim_/i.test(n),'事实fact':n=>/^fact_/i.test(n),'汇总DWS':n=>/^dws/i.test(n),'聚合agg':n=>/^agg/i.test(n),'元数据':n=>/metadata|metric_catalog|metric_config/i.test(n)};
async function catalog(){CAT=await J('/api/tables');
 $('#cat_tags').innerHTML=Object.keys(DOMS).map(d=>`<span class="tag ${d===CAT_DOM?'ok':''}" style="cursor:pointer" onclick="CAT_DOM='${d}';catalog_tags();catRender()">${d}</span>`).join('');
 catRender();}
function catalog_tags(){document.querySelectorAll('#cat_tags .tag').forEach(t=>t.className='tag '+(t.textContent===CAT_DOM?'ok':''));}
function catRender(){const q=($('#cat_q').value||'').toLowerCase();
 const rows=CAT.filter(t=>DOMS[CAT_DOM](t.name)&&(!q||(t.name+' '+(t.cn||'')).toLowerCase().includes(q)));
 $('#tbl_list').innerHTML=`<div class="muted" style="margin-bottom:6px">${rows.length}/${CAT.length} 张</div><table><tr><th>表</th><th>行</th><th>列</th></tr>`+rows.map(t=>`<tr onclick="tbl(${esc(JSON.stringify(t.name))})" style="cursor:pointer"><td><div>${esc(t.name)}</div>${t.cn?`<div class="muted">${esc(t.cn)}</div>`:''}</td><td>${t.rows}</td><td>${t.cols}</td></tr>`).join('')+'</table>';}
async function tbl(n){const en=encodeURIComponent(n);const d=await J('/api/table/'+en);const info=await J('/api/table/'+en+'/info');
 const chips=(info.objects||[]).map(o=>`<span class="tag" style="background:${KCOL(o.kind)}22;color:${KCOL(o.kind)}">${esc(o.name)}</span>`).join('')||'<span class=muted>未绑定对象</span>';
 const mchips=(info.metrics||[]).map(m=>`<a href="#" class="tag" style="background:#EEF2FF" onclick="event.preventDefault();document.querySelector('[data-p=metrics]').click();setTimeout(()=>lineage(${esc(JSON.stringify(m.name))}),400)">${esc(m.name)}</a>`).join('')||'<span class=muted>无</span>';
 $('#tbl_cols').innerHTML=`<div class="step" style="margin-bottom:8px"><b>基本信息</b>· ${info.rows} 行 × ${info.cols} 列 &nbsp; <b>所属对象</b>: ${chips} &nbsp; <b>支撑指标</b>: ${mchips}</div><h3>${n} · 字段</h3><div class="scroll" style="max-height:26vh"><table><tr><th>列</th><th>中文</th><th>类型</th><th>PK</th></tr>`+
  d.columns.map(c=>`<tr><td>${esc(c.col)}</td><td>${esc(c.cn)}</td><td>${esc(c.type)}</td><td>${c.pk?'✓':''}</td></tr>`).join('')+'</table></div>';
 const p=d.preview;$('#tbl_prev').innerHTML=`<h3>数据预览</h3><table><tr>${p.columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr>`+
  p.rows.map(r=>`<tr>${p.columns.map(c=>`<td>${cell(r[c])}</td>`).join('')}</tr>`).join('')+'</table>';}

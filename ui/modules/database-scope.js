/* Shared database selection. Each browser retains its own selection; the server DB is immutable. */
window.APP_DATA = {id: 'demo', context: null, ready: false, draft: null};

function appSourceId() { return window.APP_DATA.id; }
function appSourceStorageKey() { return appSourceId() === 'demo' ? 'dq_convs' : 'dq_convs:' + appSourceId(); }
function appRequestOptions(url, options) {
  const out = {...(options || {})};
  if (String(url).startsWith('/api/')) {
    const headers = new Headers(out.headers || {});
    if (appSourceId() && !headers.has('X-DataMind-Source')) headers.set('X-DataMind-Source', appSourceId());
    out.headers = headers;
  }
  return out;
}
function appFetch(url, options) { return fetch(url, appRequestOptions(url, options)); }

function appGraphInitialLayout(graph) {
  const select=document.getElementById('g_layout');
  if(select && !select.dataset.userSelected)select.value=graph.nodes.length>120?'force':'dagre';
}

function appSourceChange(id) {
  if (!id || id === appSourceId()) return;
  if (typeof BC_BUSY !== 'undefined' && BC_BUSY) {
    document.getElementById('app_source').value = appSourceId();
    toast('构建进行中，请待本轮结束后切换数据库', false);
    return;
  }
  appSaveDraft();
  try { localStorage.setItem('datamind_source', id); } catch (_) {}
  // A new document prevents late queries, charts or worker streams from the previous source
  // updating the current page. Saved question history remains in its source-specific storage.
  const url = new URL(location.href);
  url.searchParams.set('source', id);
  location.assign(url.href);
}

function appSaveDraft() {
  const fields={};
  for (const id of ['sd_q','vz_sql','vz_title','vz_board_name','bc_name','bc_q','bc_workload','bc_cq','chat_q','sq_q']) {
    const el=document.getElementById(id); if(el)fields[id]=el.value;
  }
  const draft={fields, vizSource:document.getElementById('vz_src')?.value, sqlSource:document.getElementById('sd_src')?.value,
    board:typeof VZ_BOARD!=='undefined'?VZ_BOARD:[]};
  try { sessionStorage.setItem('datamind_draft:'+appSourceId(),JSON.stringify(draft)); } catch (_) {}
}

function appRestoreDraft() {
  let draft=null;
  try { draft=JSON.parse(sessionStorage.getItem('datamind_draft:'+appSourceId())||'null'); } catch (_) {}
  window.APP_DATA.draft=draft;
  if(!draft || typeof draft.fields!=='object')return;
  for(const [id,value] of Object.entries(draft.fields)) {
    if(!['sd_q','vz_sql','vz_title','vz_board_name','bc_name','bc_q','bc_workload','bc_cq','chat_q','sq_q'].includes(id))continue;
    const el=document.getElementById(id); if(el&&typeof value==='string')el.value=value;
  }
  if(typeof VZ_BOARD!=='undefined' && Array.isArray(draft.board))VZ_BOARD=draft.board;
}

async function appSourceInit() {
  const select = document.getElementById('app_source');
  const status = document.getElementById('app_source_status');
  try {
    // Bootstrap is independent of a stale saved source, so removed connections remain recoverable.
    const response = await fetch('/api/build/sources');
    const data = await response.json();
    if (!response.ok || !Array.isArray(data.sources)) throw new Error(data.error || '数据源清单不可用');
    const ready = data.sources.filter(s => s.kind === 'sqlite' && s.ready);
    window.APP_DATA.sources=ready;
    if (!ready.length) throw new Error('没有可用数据库，请在数据连接中检查或登记 SQLite 文件');
    let wanted = new URL(location.href).searchParams.get('source');
    if (!wanted) { try { wanted = localStorage.getItem('datamind_source'); } catch (_) {} }
    const source = ready.find(s => s.id === (wanted || 'demo')) || ready[0];
    window.APP_DATA.id = source.id;
    const context = await J('/api/data/context');
    if (context.error) throw new Error(context.error);
    window.APP_DATA.context = context;
    window.APP_DATA.ready = true;
    select.innerHTML = ready.map(s => `<option value="${esc(s.id)}">${esc(s.name)} · ${s.tables} 表</option>`).join('');
    select.value = source.id; select.disabled = false; select.title = source.name;
    status.textContent = `${context.tables} 表 · ${context.graphs.length} 个本体`;
    if (wanted && wanted !== source.id) {
      status.textContent += ' · 原数据源不可用，已改选';
      toast('原数据源已不可用，当前已切换至「' + source.name + '」', false);
    }
    try { localStorage.setItem('datamind_source', source.id); } catch (_) {}
    if (typeof BC !== 'undefined') { BC.src = source.id; BC.srcName = source.name; }
    if(source.id!=='demo') {
      for(const id of ['sd_q','vz_sql']){const el=document.getElementById(id);if(el)el.value='';}
    }
    appRestoreDraft();
    if (source.id !== 'demo' && typeof DQ_DS !== 'undefined') {
      DQ_DS.graphs = context.default_graph ? [context.default_graph] : [];
      DQ_DS.graphNames = context.default_graph ? [(context.graph_names || {})[context.default_graph] || context.default_graph] : [];
    }
    // Copy identifiable legacy history to the new bucket; keep the original record untouched.
    if(source.id!=='demo') {
      try {
        const old=JSON.parse(localStorage.getItem('dq_convs')||'[]'),current=JSON.parse(localStorage.getItem(appSourceStorageKey())||'[]');
        if(Array.isArray(old)&&Array.isArray(current)) {
          const ids=new Set(current.map(c=>c.id));
          const owned=old.filter(c=>{const keys=c.scope?.graphs||[];return keys.length&&keys.every(k=>context.graphs.includes(k))&&!ids.has(c.id);});
          if(owned.length)localStorage.setItem(appSourceStorageKey(),JSON.stringify([...current,...owned]));
        }
      }catch(_){}
    }
    return true;
  } catch (error) {
    window.APP_DATA.id = ''; window.APP_DATA.ready = false;
    status.textContent = error.message || '数据源初始化失败';
    select.innerHTML = '<option value="">数据库不可用</option>';
    select.disabled = true;
    return false;
  }
}

function appOverviewFallback(data) {
  const rows = data.table_distribution || [];
  if (!(data.trend || []).length) {
    document.getElementById('home_trend_title').textContent = '各表记录数（前 12 表）';
    mk($('#c_trend'), {grid:{top:20,left:70,right:20,bottom:90},tooltip:{trigger:'axis'},
      xAxis:{type:'category',data:rows.map(r=>r.cn||r.name),axisLabel:{rotate:35,fontSize:11}},
      yAxis:{type:'value',...AX},series:[{type:'bar',data:rows.map(r=>r.rows),itemStyle:{color:'#4A5FF3'}}]});
  }
  if (!(data.prod || []).length) {
    document.getElementById('home_prod_title').textContent = '各表字段数（同组表）';
    mk($('#c_prod'), {grid:{top:20,left:50,right:20,bottom:90},tooltip:{trigger:'axis'},
      xAxis:{type:'category',data:rows.map(r=>r.cn||r.name),axisLabel:{rotate:35,fontSize:11}},
      yAxis:{type:'value',...AX},series:[{type:'bar',data:rows.map(r=>r.cols),itemStyle:{color:'#38bdf8'}}]});
  }
}

async function appLibrary() {
  const graphs = await J('/api/graphs');
  if (!Array.isArray(graphs)) { $('#lib_list').textContent = graphs.error || '本体列表不可用'; return; }
  if (!graphs.length) { $('#lib_list').textContent = '当前数据库尚无本体，请先在本体构建中生成。'; return; }
  $('#lib_list').innerHTML = '<table><tr><th>名称</th><th>对象数</th><th>关系数</th><th>操作</th></tr>'
    + graphs.map(o=>`<tr><td>${esc(o.name)}</td><td>${o.nodes}</td><td>${o.edges}</td><td>`
      + `<a href="#" onclick="event.preventDefault();bcView(${jsAttr(o.id)})">查看</a> · `
      + `<a href="#" onclick="event.preventDefault();dqStartFromOntology(${jsAttr(o.id)})">问数</a>`
      + (o.cat==='scenario' ? ` · <a href="#" onclick="event.preventDefault();if(confirm('删除此本体？'))libDel(${jsAttr(o.id.replace(/^forged_/,''))})">删除</a>` : '')
      + '</td></tr>').join('') + '</table>';
}

function appMetricChart(el, data) {
  const rows = data.data.rows || [], cols = data.data.columns || [];
  if (!rows.length) { el.textContent = '当前查询没有返回数据'; return; }
  const time = cols.find(c=>c!==data.metric);
  if (!time) {
    const chart=echarts.getInstanceByDom(el); if(chart)chart.dispose();
    el.innerHTML=`<div style="padding:40px;text-align:center"><div class="muted">${esc(data.metric)}</div><div style="font-size:32px;font-weight:700">${cell(rows[0][data.metric])}</div><div class="muted">${esc(data.unit||'')}</div></div>`;
    return;
  }
  mk(el,{grid:{top:28,left:60,right:20,bottom:30},tooltip:{trigger:'axis'},
    xAxis:{type:'category',data:rows.map(r=>r[time]),...AX},yAxis:{type:'value',...AX},
    series:[{name:data.metric,type:'line',data:rows.map(r=>r[data.metric]),itemStyle:{color:'#4A5FF3'}}]});
}
Object.assign(window, {appSourceId, appSourceStorageKey, appRequestOptions, appFetch, appSourceChange, appSourceInit});

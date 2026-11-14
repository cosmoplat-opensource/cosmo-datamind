/* 评审台(DR-107):关系表、证据展开、取值样例、筛选、批量确认与否决、分流移出的关系。
   数据来自 /api/ont/review;写操作经 rvmOpen / rvmSubmit(index.html)提交到 /api/ont/apply。 */
const RV = {data: null, filter: {pending: true, status: '', q: ''}, shown: 50, picked: new Set(), open: new Set(), samples: {}};
const RV_STATUS = {verified: '已验证', asserted: '人工断言', candidate: '待取证', inferred: '推理得出', gap: '待补', rejected: '已否决'};
const RV_PAGE = 50;

function rvKey(r) { return r.s + '->' + r.t; }
function rvPct(v) { return typeof v === 'number' && isFinite(v) ? (Math.round(v * 10) / 10) + '%' : '-'; }
function rvYes(v) { return v === true ? '是' : v === false ? '否' : '-'; }

function rvVisible() {
  const f = RV.filter, q = f.q.trim().toLowerCase();
  return ((RV.data && RV.data.rows) || []).filter(r =>
    (!f.pending || r.pending) && (!f.status || r.status === f.status) &&
    (!q || [r.s, r.t, r.sn, r.tn, r.verb, r.child_table, r.parent_table].some(x => String(x || '').toLowerCase().includes(q))));
}

/* 一条关系的评审依据。纯函数:所有来自数据或模型的文本都经 esc。 */
function rvEvidenceHTML(r) {
  const ev = r.evidence || {}, rows = [];
  const add = (label, html) => rows.push(`<tr><th>${esc(label)}</th><td>${html}</td></tr>`);
  if (ev.child_key || ev.parent_key) {
    add('取证的键', `${esc(r.child_table || r.s)}.${esc(ev.child_key || '?')} → ${esc(r.parent_table || r.t)}.${esc(ev.parent_key || '?')}`);
  } else {
    add('取证的键', '<span class="muted">没有记录连接键：这条关系还没有数据证据</span>');
  }
  if (ev.overlap != null) {
    const counts = ev.child_distinct != null ? `（子键 ${esc(ev.child_distinct)} 个取值${ev.matched_distinct != null ? `，其中 ${esc(ev.matched_distinct)} 个能在父表找到` : ''}）` : '';
    add('取值重合', `${esc(rvPct(ev.overlap))}${counts}`);
  }
  if (ev.orphan_rate != null && ev.orphan_rate > 0) add('在父表找不到', `${esc(rvPct(ev.orphan_rate * 100))} 的子键取值`);
  add('父键唯一 / 键名有据', `${rvYes(ev.parent_unique)} / ${rvYes(ev.name_ok)}${ev.direction === 'reverse' ? '；方向存疑（子列唯一而父列不唯一）' : ''}`);
  if (ev.condition) add('判别条件', esc(`${ev.condition.column} = ${ev.condition.value} 时成立`));
  if (ev.valid_rows) add('有效行规则', esc(ev.valid_rows));
  if (ev.evidence_complete === false || ev.sample_limited) {
    const sides = Object.entries(ev.sample_limited || {}).map(([k, v]) => (k === 'child' ? '子表' : '父表') + v).join('、');
    add('证据范围', `<span class="tag warn">不完整</span> ${esc(sides || '取证被截断或未完成')}${ev.verified_on_sample ? '；在抽样数据上成立，须在全量数据上核对' : ''}`);
  }
  if (r.note) add('裁决说明', esc(r.note));
  if (r.why_review) add('还缺什么', esc(r.why_review));
  if (r.reason || r.by) add('评审记录', esc([r.by, r.time, r.reason].filter(Boolean).join(' · ')));
  const key = rvKey(r), sm = RV.samples[key];
  let samples = `<button class="ghost" onclick="rvSamples(${jsAttr(key)})">看取值样例</button>`;
  if (sm === 'loading') samples = '<span class="muted">取样中…</span>';
  else if (sm && sm.error) samples = `<span class="muted">${esc(sm.error)}</span>`;
  else if (sm) {
    const list = xs => xs.length ? xs.map(x => `<code>${esc(x)}</code>`).join(' ') : '<span class="muted">无</span>';
    samples = `<div>能在父表找到：${list(sm.matched || [])}</div><div>找不到：${list(sm.unmatched || [])}</div><div class="muted">${esc(sm.child || '')} 对 ${esc(sm.parent || '')}，各取至多 5 例</div>`;
  }
  if (ev.child_key && ev.parent_key) add('取值样例', samples);
  return `<table class="rv-ev">${rows.join('')}</table>`;
}

function rvRowHTML(r) {
  const key = rvKey(r), tag = r.status === 'verified' ? 'ok' : r.status === 'asserted' ? 'warn' : 'evt';
  const human = r.human_review === 'approved' ? '<span class="tag ok">已通过</span>'
    : r.human_review === 'rejected' ? '<span class="tag evt">已否决</span>'
    : r.pending ? '<span class="tag warn">待审</span>' : '<span class="muted">未评审</span>';
  const ops = r.status !== 'rejected'
    ? `<button class="ghost" onclick="rvOp('confirm_relation',${jsAttr(key)})">通过</button>
       <button class="ghost" onclick="rvOp('reject_relation',${jsAttr(key)})">否决</button>
       <button class="ghost" onclick="rvVerb(${jsAttr(key)},${jsAttr(r.verb)})">改动词</button>
       <button class="ghost" style="color:#b91c1c" onclick="rvDelRel(${jsAttr(key)})">删除</button>`
    : `<button class="ghost" onclick="rvOp('confirm_relation',${jsAttr(key)})">恢复为断言</button>`;
  const open = RV.open.has(key);
  return `<tr class="${r.pending ? 'rv-pending' : ''}">
    <td><input type="checkbox" aria-label="选择这条关系" ${RV.picked.has(key) ? 'checked' : ''} onchange="rvPick(${jsAttr(key)},this.checked)"></td>
    <td>${esc(r.sn)} → ${esc(r.tn)}<div class="muted rv-ids">${esc(r.s)} → ${esc(r.t)}</div></td>
    <td>${esc(r.verb)}</td><td><span class="tag ${tag}">${esc(RV_STATUS[r.status] || r.status || '-')}</span></td>
    <td>${r.overlap != null ? esc(r.overlap) + '%' : '<span class="muted">-</span>'}</td>
    <td>${r.semantic === 'pass' ? '<span class="tag ok">通过</span>' : r.semantic === 'fail' ? '<span class="tag warn">存疑</span>' : '<span class="muted">-</span>'}</td>
    <td>${human}</td>
    <td><button class="ghost" aria-expanded="${open}" onclick="rvToggle(${jsAttr(key)})">${open ? '收起依据' : '查看依据'}</button> ${ops}</td></tr>`
    + (open ? `<tr class="rv-evrow"><td></td><td colspan="7">${rvEvidenceHTML(r)}</td></tr>` : '');
}

function rvAsideHTML(items) {
  if (!items || !items.length) return '';
  return `<details class="rv-aside"><summary>构建时被分流移出图谱的关系（${items.length} 条）</summary>
    <div class="muted">这些关系因没有数据证据或证据明显不成立，没有进入图谱。确有业务依据的，可以加回为人工断言。</div>
    <table><tr><th>关系</th><th>动词</th><th>处理</th><th>原因</th><th>操作</th></tr>${items.map(x =>
      `<tr><td>${esc(x.sn)} → ${esc(x.tn)}</td><td>${esc(x.verb)}</td>
       <td>${x.decision === 'defer' ? '暂缓（无数据）' : '自动驳回'}</td><td>${esc(x.reason || '-')}</td>
       <td><button class="ghost" onclick="rvAddBack(${jsAttr(x.s + '->' + x.t)},${jsAttr(x.verb)})">加回为人工断言</button></td></tr>`).join('')}</table></details>`;
}

function rvDraw() {
  const d = RV.data, body = $('#rv_body');
  if (!d || !body) return;
  const rows = rvVisible(), shown = rows.slice(0, RV.shown), f = RV.filter;
  const picked = [...RV.picked].filter(k => rows.some(r => rvKey(r) === k));
  const status = Object.entries(RV_STATUS).map(([k, v]) => `<option value="${esc(k)}" ${f.status === k ? 'selected' : ''}>${esc(v)}</option>`).join('');
  body.innerHTML = `<div class="rv-bar">
     <label><input type="checkbox" ${f.pending ? 'checked' : ''} onchange="rvSetFilter('pending',this.checked)"> 只看待审</label>
     <label>状态 <select onchange="rvSetFilter('status',this.value)"><option value="">全部</option>${status}</select></label>
     <input placeholder="按对象、表名或动词筛选" value="${esc(f.q)}" oninput="rvSetFilter('q',this.value)" aria-label="筛选关系">
     <span class="muted">显示 ${shown.length} / ${rows.length} 条（共 ${d.rows.length} 条）</span><span style="flex:1"></span>
     <span>已选 ${picked.length} 条</span>
     <button class="ghost" ${picked.length ? '' : 'disabled'} onclick="rvBatch('confirm_relation')">通过所选</button>
     <button class="ghost" ${picked.length ? '' : 'disabled'} onclick="rvBatch('reject_relation')">否决所选</button>
     <button class="ghost" onclick="rvPickAll(${picked.length === shown.length && shown.length ? 'false' : 'true'})">${picked.length === shown.length && shown.length ? '取消全选' : '全选本页'}</button></div>`
    + (shown.length
      ? `<table class="rv-table"><tr><th></th><th>关系</th><th>动词</th><th>状态</th><th>取值重合</th><th>语义评审</th><th>人审</th><th>操作</th></tr>${shown.map(rvRowHTML).join('')}</table>`
      : `<div class="muted" style="padding:14px 4px">${d.rows.length ? '没有符合筛选条件的关系。' : '这张图谱还没有关系。'}</div>`)
    + (rows.length > shown.length ? `<div style="margin:10px 0"><button class="ghost" onclick="rvMore()">再显示 ${Math.min(RV_PAGE, rows.length - shown.length)} 条</button></div>` : '')
    + rvAsideHTML(d.set_aside);
  // 输入框重绘后保持焦点与光标,否则每敲一个字就失焦
  if (RV._focusQ) { const q = body.querySelector('.rv-bar input[placeholder]'); if (q && q.focus) { q.focus(); if (q.setSelectionRange) q.setSelectionRange(f.q.length, f.q.length); } RV._focusQ = false; }
}

/* 由 rvLoad 调用:换了图谱或刷新后重置选择与展开状态;没有待审项时不默认只看待审。 */
function rvRender(d) {
  const sameGraph = RV.data && RV.data.graph === d.graph;
  RV.data = d; RV.picked.clear(); RV.samples = {};
  if (!sameGraph) { RV.open.clear(); RV.shown = RV_PAGE; RV.filter = {pending: (d.counts.pending || 0) > 0, status: '', q: ''}; }
  else if (RV.filter.pending && !(d.counts.pending > 0)) RV.filter.pending = false;
  rvDraw();
}
function rvSetFilter(name, value) { RV.filter[name] = value; RV.shown = RV_PAGE; RV._focusQ = name === 'q'; rvDraw(); }
function rvMore() { RV.shown += RV_PAGE; rvDraw(); }
function rvToggle(key) { if (RV.open.has(key)) RV.open.delete(key); else RV.open.add(key); rvDraw(); }
function rvPick(key, on) { if (on) RV.picked.add(key); else RV.picked.delete(key); rvDraw(); }
function rvPickAll(on) { RV.picked.clear(); if (on) rvVisible().slice(0, RV.shown).forEach(r => RV.picked.add(rvKey(r))); rvDraw(); }

async function rvSamples(key) {
  const [s, t] = key.split('->'), graph = RV.data.graph;
  RV.samples[key] = 'loading'; rvDraw();
  const d = await J(`/api/ont/relation/${encodeURIComponent(graph)}?s=${encodeURIComponent(s)}&t=${encodeURIComponent(t)}&samples=1`).catch(e => ({error: '' + e}));
  if (!RV.data || RV.data.graph !== graph) return;          // 取样期间换了图谱:丢弃晚到的结果
  RV.samples[key] = d.error ? {error: d.error} : (d.samples || {error: '没有取到样例'});
  rvDraw();
}

/* 批量确认或否决:复用评审表单(评审人必填;否决必须写原因),一次提交,服务端要么全部记录要么都不记。 */
function rvBatch(op) {
  const visible = new Set(rvVisible().map(rvKey)), targets = [...RV.picked].filter(k => visible.has(k)).map(k => 'rel:' + k);
  if (!targets.length) { toast('先勾选要处理的关系', false); return; }
  rvmOpen(op, targets[0], `共 ${targets.length} 条关系，使用同一条评审意见`);
  RVM.batch = targets;
  $('#rvm_title').textContent = (op === 'reject_relation' ? '批量否决 ' : '批量通过 ') + targets.length + ' 条';
}
function rvAddBack(key, verb) { rvmOpen('add_relation', 'rel:' + key, '关系 ' + key, {verb: verb || '关联'}); }

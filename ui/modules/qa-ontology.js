/* 深度问数 × 本体作用域：直接入口、可问数状态和会话级作用域。 */
let DQ_DS_BEFORE = null;

async function dqEnsureGraphs() {
  if (!Array.isArray(DQ_GRAPHS)) DQ_GRAPHS = await J('/api/graphs');
  return Array.isArray(DQ_GRAPHS) ? DQ_GRAPHS : [];
}

function dqGraphMeta(id) {
  return (DQ_GRAPHS || []).find(g => g.id === id) || null;
}

function dqScopeSnapshot() {
  return {
    graphs: [...(DQ_DS.graphs || [])],
    graphNames: dqScopeNames(DQ_DS),
    availableCount: dqScopeAvailable(DQ_DS),
    tables: [...(DQ_DS.tables || [])],
    allTables: DQ_DS.allTables !== false
  };
}

function dqScopeNames(scope) {
  const ids = (scope && scope.graphs) || [];
  const saved = (scope && scope.graphNames) || [];
  return ids.map((id, index) => (dqGraphMeta(id) || {}).name || saved[index] || id);
}

function dqScopeAvailable(scope) {
  const ids = (scope && scope.graphs) || [];
  if (!ids.length) return 0;
  const metas = ids.map(dqGraphMeta).filter(Boolean);
  if (ids.length && metas.length === ids.length)
    return metas.reduce((n, graph) => n + (graph.available_table_count || 0), 0);
  return Number.isFinite(scope && scope.availableCount) ? scope.availableCount : null;
}

function dqScopeReportName(scope) {
  const names = dqScopeNames(scope || dqScopeSnapshot());
  return names.length ? names.map(n => `「${n}」`).join('、') : '示例本体图谱';
}

function dqUpdateSourceLabel() {
  const ng = DQ_DS.graphs.length, nt = DQ_DS.tables.length;
  let label = '全部';
  if (ng === 1 && !nt) label = `本体：${dqScopeNames(DQ_DS)[0]}`;
  else if (ng && !nt) label = `本体 ${ng} 个`;
  else if (nt && !ng) label = `数据表 ${nt} 张`;
  else if (ng && nt) label = `本体 ${ng} 个 + 表 ${nt} 张`;
  const el = $('#dq_src_lbl');
  if (el) el.textContent = '数据源：' + label;
}

function dqRestoreScope(conv) {
  let scope = conv && conv.scope;
  const turns = (conv && conv.turns) || [];
  const anchor = turns.length && turns[turns.length - 1].d && turns[turns.length - 1].d.anchor;
  if (!scope) {
    const keys = anchor && anchor.ontology && anchor.ontology.keys;
    const names = anchor && anchor.ontology && anchor.ontology.names;
    scope = {graphs: (keys && keys[0] !== 'demo') ? keys : [], graphNames: names || [], tables: [], allTables: true};
  } else if (!(scope.graphNames || []).length && anchor && anchor.ontology) {
    scope = {...scope, graphNames: anchor.ontology.names || []};
  }
  if (!Number.isFinite(scope.availableCount) && anchor && anchor.ontology)
    scope = {...scope, availableCount: anchor.ontology.available_tables};
  DQ_DS = {
    graphs: [...(scope.graphs || [])],
    graphNames: [...(scope.graphNames || [])],
    availableCount: Number.isFinite(scope.availableCount) ? scope.availableCount : null,
    tables: [...(scope.tables || [])],
    allTables: scope.allTables !== false
  };
  dqUpdateSourceLabel();
}

function dqHeroScoped() {
  const graphs = (DQ_DS && DQ_DS.graphs) || [];
  const names = dqScopeNames(DQ_DS);
  const selected = graphs.map(dqGraphMeta).filter(Boolean);
  const title = names.length === 1 ? `基于「${esc(names[0])}」深度问数` : '深度问数';
  const source = names.length ? names.map(esc).join('、') : '示例本体图谱';
  const usable = dqScopeAvailable(DQ_DS);
  const status = names.length
    ? `<div class="step" style="display:inline-block;text-align:left;margin:8px auto 14px"><b>当前本体：</b>${source}`
      + (usable == null ? ' · 绑定表状态将在数据源列表加载后更新。' : ` · <b>${usable}</b> 张绑定表可查询。`)
      + `新对话将持续使用该本体，不会自动切回示例本体。</div>`
    : '';
  const examples = names.length ? '' : `<div class="egs">${EG_Q.map(q => `<span class="eg" onclick="dqAskEg(${esc(JSON.stringify(q))})">${esc(q)}</span>`).join('')}</div>`;
  return `<div class="dq-hero"><h1>${title}</h1>`
    + `<p>输入自然语言问题，系统在 ${source} 的对象、关系与可查询表边界内生成 SQL、图表和分析说明；执行过程与数据出处可追溯。</p>`
    + status + examples + `</div>`;
}

function gQaSync(id) {
  const button = $('#g_ask');
  if (!button) return;
  const graph = dqGraphMeta(id);
  const ok = !!(graph && graph.queryable);
  button.disabled = !ok;
  button.textContent = ok ? '基于此本体问数' : '此本体暂无可查询表';
  button.title = graph ? graph.reason : '正在读取本体的数据表绑定状态';
}

async function dqStartFromOntology(id) {
  const graphs = await dqEnsureGraphs();
  const graph = graphs.find(g => g.id === id);
  if (!graph) {
    toast('未找到该本体，请刷新本体列表后重试', false);
    return false;
  }
  if (!graph.queryable) {
    toast(graph.reason || '该本体尚未绑定当前可查询的数据表', false);
    return false;
  }
  DQ_DS = {graphs: [id], graphNames: [graph.name], availableCount: graph.available_table_count || 0,
    tables: [], allTables: true};
  dqUpdateSourceLabel();
  dqNew();
  goPage('chat');
  if (location.hash.slice(1) !== 'chat') location.hash = 'chat';
  setTimeout(() => { const input = $('#chat_q'); if (input) input.focus(); }, 80);
  toast(`已基于「${graph.name}」新建问数`, true);
  return true;
}

function dqDsBegin() {
  DQ_DS_BEFORE = dqScopeSnapshot();
}

function dqDsCancel() {
  if (DQ_DS_BEFORE) {
    DQ_DS = {
      graphs: [...DQ_DS_BEFORE.graphs], graphNames: [...(DQ_DS_BEFORE.graphNames || [])],
      availableCount: DQ_DS_BEFORE.availableCount,
      tables: [...DQ_DS_BEFORE.tables],
      allTables: DQ_DS_BEFORE.allTables
    };
  }
  DQ_DS_BEFORE = null;
  dqUpdateSourceLabel();
  dqCloseModal('dq_dsmodal');
}

function dqDsCommit() {
  const valid = new Set((DQ_GRAPHS || []).filter(g => g.queryable).map(g => g.id));
  DQ_DS.graphs = DQ_DS.graphs.filter(id => valid.has(id));
  DQ_DS.availableCount = dqScopeAvailable(DQ_DS);
  const before = JSON.stringify(DQ_DS_BEFORE || {});
  const after = JSON.stringify(dqScopeSnapshot());
  DQ_DS_BEFORE = null;
  dqUpdateSourceLabel();
  dqCloseModal('dq_dsmodal');
  if (before !== after && DQ_CONV && (DQ_CONV.turns || []).length) {
    dqNew();
    toast('数据范围已变更，已新建对话，避免混用上一套本体的上下文', true);
  } else if (!DQ_CONV) {
    const stream = $('#dq_stream');
    if (stream && stream.querySelector('.dq-hero')) stream.innerHTML = dqHero();
  }
}

Object.assign(window, {
  dqScopeSnapshot, dqScopeReportName, dqRestoreScope, dqHeroScoped,
  dqStartFromOntology, gQaSync, dqDsBegin, dqDsCancel, dqDsCommit
});

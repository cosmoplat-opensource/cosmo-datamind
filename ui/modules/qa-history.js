/* 深度问数会话状态：区分本次实时分析与历史回放，历史记录只读、续问自动分支。 */
window.DQ_VIEW_MODE = 'new';

function dqAllConv() {
  try { return JSON.parse(localStorage.getItem('dq_convs') || '[]'); }
  catch (_) { return []; }
}

function dqHistoryTime(ts) {
  if (!ts) return '旧记录';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '旧记录';
  const pad = n => String(n).padStart(2, '0');
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function dqSaveConv(conv) {
  conv = conv || DQ_CONV;
  if (!conv) return;
  if (!conv.scope) conv.scope = dqScopeSnapshot();
  conv.ts = Date.now();
  let all = dqAllConv().filter(c => c.id !== conv.id);
  all.unshift(conv);
  all = all.slice(0, 30);
  try { localStorage.setItem('dq_convs', JSON.stringify(all)); } catch (_) {}
  dqRenderHist();
}

function dqRenderHist() {
  const all = dqAllConv();
  // 当前分析虽已安全落盘，但在本轮仍属于“实时会话”，不混进历史栏造成跳转错觉。
  const visible = all.filter(c => !(window.DQ_VIEW_MODE === 'live' && DQ_CONV && c.id === DQ_CONV.id));
  $('#dq_hist').innerHTML = visible.length ? visible.map(c => {
    const selected = window.DQ_VIEW_MODE === 'history' && DQ_CONV && c.id === DQ_CONV.id;
    return `<div class="it ${selected ? 'on' : ''}" onclick="dqOpen(${esc(JSON.stringify(c.id))})">`
      + `<span class="t">${esc(c.title || '(空)')} <small class="muted">· ${esc(dqHistoryTime(c.ts))}</small></span>`
      + `<span class="del" title="删除" onclick="event.stopPropagation();dqDelConv(${esc(JSON.stringify(c.id))})">✕</span></div>`;
  }).join('') : '<div class="muted" style="padding:8px 6px">暂无历史，新分析完成并离开当前对话后会在这里显示</div>';
}

function dqDelConv(id) {
  const all = dqAllConv().filter(c => c.id !== id);
  try { localStorage.setItem('dq_convs', JSON.stringify(all)); } catch (_) {}
  if (DQ_CONV && DQ_CONV.id === id) dqNew();
  else dqRenderHist();
}

function dqOpen(id) {
  const c = dqAllConv().find(x => x.id === id);
  if (!c) return;
  dqAbort();
  dqRestoreScope(c);
  DQ_CONV = c;
  window.DQ_VIEW_MODE = 'history';
  CHAT_HIST = c.turns.map(t => ({q: t.q, summary: (t.d && t.d.summary) || ''}));
  $('#dqv_deep').classList.remove('dqv-empty');
  dqMode('deep');
  const stream = $('#dq_stream');
  stream.innerHTML = '';
  c.turns.forEach(t => {
    stream.insertAdjacentHTML('beforeend', `<div class="dq-usermsg">${esc(t.q)}</div>`);
    const card = document.createElement('div');
    card.className = 'dq-card';
    stream.appendChild(card);
    dqRenderCard(card, t.q, t.d, 'history');
  });
  const last = c.turns[c.turns.length - 1];
  dqAncBar(last && last.d && last.d.anchor);
  dqRenderHist();
  stream.scrollTop = 1e9;
}

function dqBeginQuestion() {
  const fromHistory = window.DQ_VIEW_MODE === 'history';
  const inherited = fromHistory ? CHAT_HIST.slice(-2) : CHAT_HIST;
  if (fromHistory) {
    // 历史记录是只读快照。续问继承最近上下文，但写入新的会话，不覆盖原记录。
    DQ_CONV = null;
    $('#dq_stream').innerHTML = '';
    dqAncBar(null);
  }
  if (!DQ_CONV) {
    DQ_CONV = {id: 'c' + Math.random().toString(36).slice(2, 9), title: '', turns: [], ts: 0,
      scope: dqScopeSnapshot()};
    $('#dq_stream').innerHTML = '';
  }
  CHAT_HIST = inherited;
  window.DQ_VIEW_MODE = 'live';
  dqRenderHist();
  return {conv: DQ_CONV, branched: fromHistory};
}

Object.assign(window, {
  dqAllConv, dqSaveConv, dqRenderHist, dqDelConv, dqOpen, dqBeginQuestion
});

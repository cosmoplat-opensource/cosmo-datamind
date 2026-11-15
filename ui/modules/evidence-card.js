/* 依据卡(DR-119):问数答案的结论、依据、关键值、提示与下一步。
   renderEvidenceCard(card, host) 返回 HTML;"下一步"里的问题点击后交给 window.EVC_HOSTS[host](问题),
   宿主页面各自登记(深度问数为 'dq'),互不串用。 */
(function(){
'use strict';
const e = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const KIND = {rule:'规则', constraint:'约束', sql:'查询', action:'动作', lighting:'数据', metric:'指标', relation:'关系', caveat:'提醒'};
const TYPE_COLOR = {lookup:'#4A5FF3', judge:'#7A4FD6', whatif:'#D9772B', explain:'#2E8B62', trace:'#005AAA', unlit:'#C0392B', monitor:'#7A4FD6', clarify:'#8A8F99'};
let STYLED = false;
function style(){
  if(STYLED) return; STYLED = true;
  const css = `.evc{border:1px solid #E5E9F2;border-radius:6px;background:#FAFBFF;margin:10px 0;padding:10px 12px;font-size:12.5px;line-height:1.55}
.evc .evc-h{display:flex;gap:8px;align-items:flex-start}.evc .evc-t{color:#fff;border-radius:3px;padding:0 6px;font-size:11.5px;white-space:nowrap;margin-top:2px}
.evc .evc-c{font-weight:600;color:#1f2329}.evc .evc-kv{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:8px 0 2px;color:#1f2329}
.evc .evc-kv span:nth-child(odd){color:#646a73}.evc .evc-b{margin:8px 0 0;padding:0;list-style:none}.evc .evc-b li{margin:4px 0}
.evc .evc-k{display:inline-block;min-width:30px;font-size:11px;color:#4A5FF3;background:#EEF2FF;border-radius:3px;padding:0 5px;margin-right:6px;text-align:center}
.evc code,.evc pre{font-size:11.5px;background:#F2F4F8;border-radius:3px}.evc pre{padding:6px 8px;white-space:pre-wrap;margin:4px 0}
.evc .evc-ev{color:#646a73}.evc ol{margin:4px 0 0 18px;padding:0}.evc .evc-f{color:#B26A00;margin-top:6px}
.evc .evc-n{margin-top:8px;display:flex;flex-wrap:wrap;gap:6px}.evc .evc-n a,.evc .evc-n button{border:1px solid #D6DCFF;background:#fff;color:#4A5FF3;border-radius:12px;padding:1px 10px;cursor:pointer;text-decoration:none;font:inherit;line-height:inherit}.evc .evc-n a:focus-visible,.evc .evc-n button:focus-visible{outline:2px solid #4A5FF3;outline-offset:1px}
.evc details summary{cursor:pointer;color:#646a73}
.evc .evc-p{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px}.evc .evc-p code{white-space:nowrap;padding:0 5px}`;
  const el = document.createElement('style'); el.textContent = css; document.head.appendChild(el);
}
function trace(t){ return (t||[]).map(c => `${e(c.field)} ${e(c.op)} ${e(JSON.stringify(c.expect !== undefined ? c.expect : c.value))}(实际 ${e(JSON.stringify(c.actual))})`).join(';'); }
function params(p){ const kv = Object.entries(p || {}); return kv.length ? `<div class="evc-p">${kv.map(([x, y]) => `<code>${e(x)}=${e(y)}</code>`).join('')}</div>` : ''; }
function basis(b){
  const k = `<span class="evc-k">${e(KIND[b.kind] || b.kind)}</span>`;
  if(b.kind === 'sql') return b.text ? `<li>${k}${e(b.title || '')}${b.result_id ? ` <span class="evc-ev" title="表格、文字说明与本卡引用的是同一个结果">结果 ${e(b.result_id)}</span>` : ''}<details><summary>SQL</summary><pre>${e(b.text)}</pre></details></li>` : '';
  if(b.kind === 'rule') return `<li>${k}<b>${e(b.cn || b.id)}</b> <code>${e(b.id || '')}</code>:${e(b.text)}${b.count != null ? ` · 命中 ${e(b.count)}` : ''}${b.trace ? `<div class="evc-ev">例:${trace(b.trace)}</div>` : ''}</li>`;
  if(b.kind === 'constraint') return `<li>${k}<b>${e(b.cn)}</b>:${e(b.text)}${params(b.params)}${b.evidence ? `<div class="evc-ev">依据 ${e(b.evidence)}</div>` : ''}</li>`;
  if(b.kind === 'action') return `<li>${k}<b>${e(b.cn)}</b>${b.record_id ? ` · 记录 <code>${e(b.record_id)}</code>` : ''}${b.mode === 'simulate' ? ' <span class="evc-ev">只读试算 · 未写回业务系统</span>' : (b.real_writeback === true ? ' <span class="evc-ev">已写回业务系统</span>' : '')}${params(b.params)}
    ${(b.notes || []).length ? `<div class="evc-ev">${e(b.notes.join(';'))}</div>` : ''}
    ${(b.path || []).length ? `<ol>${b.path.map(p => `<li>${e(p.check)}:<b>${e(p.result)}</b>${p.constraint ? ` <span class="evc-ev">(${e(p.constraint)})</span>` : ''}${(p.detail || []).length ? `<div class="evc-ev">${e(p.detail.slice(0,4).join(';'))}</div>` : ''}</li>`).join('')}</ol>` : ''}
    ${b.validation ? `<div class="evc-ev">${e(b.validation)}</div>` : ''}</li>`;
  if(b.kind === 'lighting') return `<li>${k}尚未接入:${e(b.text)} · 需要 <b>${e(b.needs)}</b>${b.why ? `<div class="evc-ev">${e(b.why)}</div>` : ''}</li>`;
  if(b.kind === 'metric') return `<li>${k}<b>${e(b.name)}</b>:${e(b.text || '')} <span class="evc-ev">状态 ${e(b.status || '未标注')}</span></li>`;
  if(b.kind === 'relation') return `<li>${k}${e(b.text)} <span class="evc-ev">${e(b.status)}${b.overlap != null ? ' · 重叠 ' + e(b.overlap) + '%' : ''}</span></li>`;
  return `<li>${k}${e(b.text)}</li>`;
}
window.EVC_HOSTS = window.EVC_HOSTS || {};
window.EVC_GO = function(el){
  const q = el.dataset.q, href = el.dataset.href, ask = window.EVC_HOSTS[el.dataset.host];
  if(q && typeof ask === 'function') return ask(q);
  if(href) location.hash = href.replace(/^#/, '');
};
function chip(it, host, suffix){
  // 候选与追问是原生按钮(键盘可聚焦,Enter / 空格可选);页面跳转是带 href 的链接
  if(it.kind === 'question' && it.q)
    return `<button type="button" onclick="EVC_GO(this)" data-host="${e(host || '')}" data-q="${e(it.q)}" title="${e(it.q)}">${e(it.label)}${suffix || ''}</button>`;
  const href = '#' + String(it.href || '').replace(/^#/, '');
  return `<a href="${e(href)}" data-host="${e(host || '')}" data-href="${e(it.href || '')}" title="${e(it.href || '')}">${e(it.label)}${suffix || ''}</a>`;
}
window.renderEvidenceCard = function(card, host){
  if(!card || !card.type) return '';
  style();
  const color = TYPE_COLOR[card.type] || '#4A5FF3';
  const nexts = (card.next || []).filter(n => (n.kind === 'question' && n.q) || n.href).map(n => chip(n, host));
  const links = (card.links || []).filter(l => l.href).map(l => chip(l, host, ' ↗'));
  const bs = (card.basis || []).map(basis).join('');
  return `<div class="evc"><div class="evc-h"><span class="evc-t" style="background:${color}">依据卡 · ${e(card.type_cn || card.type)}</span><span class="evc-c">${e(card.conclusion).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')}</span></div>
   ${(card.kv || []).length ? `<div class="evc-kv">${card.kv.map(([k, v]) => `<span>${e(k)}</span><span>${e(v)}</span>`).join('')}</div>` : ''}
   ${bs ? `<ul class="evc-b">${bs}</ul>` : ''}
   ${(card.flags || []).map(f => `<div class="evc-f">⚠ ${e(f)}</div>`).join('')}
   ${nexts.length || links.length ? `<div class="evc-n"><span class="evc-ev">下一步:</span>${nexts.join('')}${links.join('')}</div>` : ''}</div>`;
};
})();

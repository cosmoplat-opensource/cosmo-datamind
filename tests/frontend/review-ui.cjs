'use strict';
// 评审台模块(ui/modules/review.js)的可执行行为测试:用最小 DOM 替身运行真实代码。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

class El { constructor() { this.innerHTML = ''; this.textContent = ''; this.value = ''; this.disabled = false; this.style = {}; }
  querySelector() { return null; } focus() {} }
function fixture() {
  const els = new Map(), state = {messages: [], opened: [], fetched: []};
  const $ = sel => { if (!els.has(sel)) els.set(sel, new El()); return els.get(sel); };
  const ctx = vm.createContext({
    console, $, state,
    esc: x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    toast: (m, ok) => state.messages.push([m, ok]),
    J: async url => { state.fetched.push(url); return state.reply || {}; },
    RVM: null,
  });
  ctx.jsAttr = x => ctx.esc(JSON.stringify(x));
  ctx.rvmOpen = (op, target, sub, params) => { ctx.RVM = {op, target, params: params || {}}; state.opened.push({op, target, sub, params}); };
  vm.runInContext(fs.readFileSync(path.join(root, 'ui/modules/review.js'), 'utf8'), ctx);
  return {ctx, $, state, run: code => vm.runInContext(code, ctx)};
}
const row = (s, t, extra = {}) => ({s, t, sn: s, tn: t, verb: '引用', status: 'candidate', pending: true, human_review: '',
  overlap: 25, semantic: '', child_table: 't_' + s, parent_table: 't_' + t,
  evidence: {child_key: 'ref_no', parent_key: 'no', overlap: 25, child_distinct: 80, matched_distinct: 20, parent_unique: true, name_ok: false, orphan_rate: 0.75},
  note: '弱重叠 25%,送审', why_review: '缺少:键名依据', ...extra});
const data = rows => ({graph: 'built_x', counts: {pending: rows.filter(r => r.pending).length}, rows, set_aside: []});

const cases = {
  evidence_shows_keys_counts_orphans_and_reasons() {
    const f = fixture(); f.ctx.rvRender(data([row('订单', '客户')]));
    const html = f.ctx.rvEvidenceHTML(f.run('RV.data.rows[0]'));
    assert.match(html, /t_订单\.ref_no → t_客户\.no/);
    assert.match(html, /25%（子键 80 个取值，其中 20 个能在父表找到）/);
    assert.match(html, /75% 的子键取值/); assert.match(html, /缺少:键名依据/); assert.match(html, /弱重叠/);
    assert.match(html, /看取值样例/);
  },
  evidence_escapes_text_from_data_and_models() {
    const f = fixture(), attack = '<img src=x onerror=alert(1)>';
    const html = f.ctx.rvEvidenceHTML(row(attack, attack, {note: attack, why_review: attack, child_table: attack,
      evidence: {child_key: attack, parent_key: attack, condition: {column: attack, value: attack}, valid_rows: attack}}));
    assert.ok(!html.includes('<img'), 'evidence fields must never create HTML nodes');
    f.ctx.rvRender(data([row(attack, attack, {verb: attack})]));
    assert.ok(!f.$('#rv_body').innerHTML.includes('<img'));
  },
  incomplete_sample_evidence_is_flagged() {
    const f = fixture();
    const html = f.ctx.rvEvidenceHTML(row('a', 'b', {evidence: {child_key: 'k', parent_key: 'k', evidence_complete: false,
      verified_on_sample: true, sample_limited: {parent: '抽样 10 / 约 100000 行'}}}));
    assert.match(html, /不完整/); assert.match(html, /父表抽样 10 \/ 约 100000 行/); assert.match(html, /全量数据上核对/);
  },
  pending_filter_is_default_only_when_something_is_pending() {
    const f = fixture();
    f.ctx.rvRender(data([row('a', 'b'), row('c', 'd', {pending: false, status: 'verified'})]));
    assert.equal(f.ctx.rvVisible().length, 1); assert.match(f.$('#rv_body').innerHTML, /显示 1 \/ 1 条（共 2 条）/);
    f.ctx.rvSetFilter('pending', false); assert.equal(f.ctx.rvVisible().length, 2);
    const g = fixture(); g.ctx.rvRender(data([row('c', 'd', {pending: false, status: 'verified'})]));
    assert.equal(g.ctx.rvVisible().length, 1, 'nothing pending: show everything rather than an empty table');
  },
  unreviewed_verified_rows_are_not_labelled_pending() {
    const f = fixture(); f.ctx.rvRender(data([row('c', 'd', {pending: false, status: 'verified'})]));
    const html = f.$('#rv_body').innerHTML;
    assert.ok(!html.includes('<span class="tag warn">待审</span>')); assert.match(html, /未评审/);
  },
  text_and_status_filters_and_paging() {
    const f = fixture(), rows = [];
    for (let i = 0; i < 120; i++) rows.push(row('s' + i, 't' + i, {child_table: i === 7 ? 'fact_orders' : 'x' + i}));
    f.ctx.rvRender(data(rows));
    assert.match(f.$('#rv_body').innerHTML, /显示 50 \/ 120 条/); assert.match(f.$('#rv_body').innerHTML, /再显示 50 条/);
    f.ctx.rvMore(); assert.match(f.$('#rv_body').innerHTML, /显示 100 \/ 120 条/);
    f.ctx.rvSetFilter('q', 'FACT_ORD'); assert.equal(f.ctx.rvVisible().length, 1);
    f.ctx.rvSetFilter('q', ''); f.ctx.rvSetFilter('status', 'verified'); assert.equal(f.ctx.rvVisible().length, 0);
    assert.match(f.$('#rv_body').innerHTML, /没有符合筛选条件的关系/);
  },
  batch_uses_only_visible_selected_rows() {
    const f = fixture();
    f.ctx.rvRender(data([row('a', 'b'), row('c', 'd'), row('e', 'f', {pending: false, status: 'verified'})]));
    f.ctx.rvBatch('confirm_relation'); assert.equal(f.state.opened.length, 0); assert.equal(f.state.messages.at(-1)[1], false);
    f.ctx.rvSetFilter('pending', false); f.ctx.rvPickAll(true);
    f.ctx.rvSetFilter('pending', true);                       // e->f 不再可见:不能被批量处理
    f.ctx.rvBatch('reject_relation');
    assert.deepEqual([...f.run('RVM.batch')], ['rel:a->b', 'rel:c->d']);
    assert.equal(f.state.opened[0].op, 'reject_relation'); assert.match(f.$('#rvm_title').textContent, /批量否决 2 条/);
  },
  set_aside_relations_are_listed_and_can_be_added_back() {
    const f = fixture(), d = data([row('a', 'b')]);
    d.set_aside = [{s: '企业', t: '能源类型', sn: '企业', tn: '能源类型', verb: '消耗', decision: 'reject', reason: '没有可回放的连接键证据'}];
    f.ctx.rvRender(d);
    const html = f.$('#rv_body').innerHTML;
    assert.match(html, /分流移出图谱的关系（1 条）/); assert.match(html, /没有可回放的连接键证据/); assert.match(html, /自动驳回/);
    f.ctx.rvAddBack('企业->能源类型', '消耗');
    assert.equal(JSON.stringify(f.state.opened[0]), JSON.stringify({op: 'add_relation', target: 'rel:企业->能源类型', sub: '关系 企业->能源类型', params: {verb: '消耗'}}));
  },
  async samples_are_fetched_on_demand_and_late_results_are_dropped() {
    const f = fixture(); f.ctx.rvRender(data([row('a', 'b')])); f.ctx.rvToggle('a->b');
    f.state.reply = {samples: {child: 't_a.ref_no', parent: 't_b.no', matched: ['D1'], unmatched: ['<b>GONE</b>']}};
    await f.ctx.rvSamples('a->b');
    assert.match(f.state.fetched[0], /\/api\/ont\/relation\/built_x\?s=a&t=b&samples=1/);
    const html = f.$('#rv_body').innerHTML;
    assert.match(html, /<code>D1<\/code>/); assert.match(html, /&lt;b&gt;GONE/); assert.ok(!html.includes('<b>GONE'));
    const pending = f.ctx.rvSamples('a->b');
    f.ctx.rvRender({...data([row('x', 'y')]), graph: 'built_other'}); await pending;
    assert.equal(f.run("RV.samples['a->b']"), undefined, 'a result for the previous graph must not leak into the new one');
  },
};

(async () => {
  const name = process.argv[2];
  if (!cases[name]) { console.error('unknown case ' + name + '; cases: ' + Object.keys(cases).join(', ')); process.exit(2); }
  await cases[name]();
})().catch(e => { console.error(e); process.exit(1); });

'use strict';
// 依据卡模块(ui/modules/evidence-card.js)的可执行行为测试:在最小 DOM 替身里运行真实代码。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');

function load() {
  const styles = [], win = {};
  const ctx = vm.createContext({window: win, location: {hash: ''},
    document: {createElement: () => ({textContent: ''}), head: {appendChild: el => styles.push(el)}}});
  vm.runInContext(fs.readFileSync(path.join(root, 'ui/modules/evidence-card.js'), 'utf8'), ctx);
  return {win, ctx, styles};
}

const cases = {
  empty_or_untyped_cards_render_nothing() {
    const {win} = load();
    assert.equal(win.renderEvidenceCard(null, 'dq'), '');
    assert.equal(win.renderEvidenceCard({conclusion: 'x'}, 'dq'), '');
  },
  a_judgement_card_shows_conclusion_values_rule_and_sql() {
    const {win, styles} = load();
    const html = win.renderEvidenceCard({type: 'judge', type_cn: '判定', conclusion: '4 行中 1 行命中', kv: [['命中', 1], ['总行数', 4]],
      basis: [{kind: 'rule', id: 'r_late', cn: '工序任务拖期', text: 'late_days > 0 → 需要重排', count: 1,
               trace: [{field: 'late_days', op: '>', expect: 0, actual: 2}]},
              {kind: 'sql', title: '', text: 'SELECT * FROM tasks WHERE late_days > 0'}],
      flags: ['下表只列出前 500 行'], next: [], links: []}, 'dq');
    assert.match(html, /依据卡 · 判定/); assert.match(html, /4 行中 1 行命中/);
    assert.match(html, /<span>命中<\/span><span>1<\/span>/);
    assert.match(html, /工序任务拖期/); assert.match(html, /命中 1/); assert.match(html, /实际 2/);
    assert.match(html, /SELECT \* FROM tasks WHERE late_days &gt; 0/);
    assert.match(html, /⚠ 下表只列出前 500 行/);
    assert.equal(styles.length, 1);
    win.renderEvidenceCard({type: 'lookup', conclusion: 'x'}, 'dq');
    assert.equal(styles.length, 1);                        // 样式只注入一次
  },
  constraints_show_parameters_and_evidence_and_missing_data_says_what_is_needed() {
    const {win} = load();
    const html = win.renderEvidenceCard({type: 'explain', conclusion: 'c', basis: [
      {kind: 'constraint', cn: '干燥等待时限', text: '等待不超过 72 小时', params: {max_wait_h: 72}, evidence: 'scheduler.py:169-180'},
      {kind: 'lighting', text: '工序任务.实际完工', needs: 'MES 报工', why: '只有计划'},
      {kind: 'relation', text: 'tasks.order_no → orders.order_no', status: 'verified', overlap: 100},
      {kind: 'metric', name: '准时率', text: '按计划完工', status: 'candidate'}]}, 'dq');
    assert.match(html, /max_wait_h=72/); assert.match(html, /依据 scheduler\.py:169-180/);
    assert.match(html, /尚未接入:工序任务\.实际完工 · 需要 <b>MES 报工<\/b>/);
    assert.match(html, /重叠 100%/); assert.match(html, /状态 candidate/);
  },
  a_simulation_says_it_is_read_only_and_a_relation_shows_its_role() {
    const {win} = load();
    const sim = win.renderEvidenceCard({type: 'whatif', conclusion: 'c', basis: [
      {kind: 'action', cn: '插单试算', record_id: 'r1', mode: 'simulate', real_writeback: false, params: {customer: 'C002'}}]}, 'dq');
    assert.match(sim, /只读试算 · 未写回业务系统/); assert.match(sim, /customer=C002/);
    const real = win.renderEvidenceCard({type: 'whatif', conclusion: 'c', basis: [
      {kind: 'action', cn: '计划下发', record_id: 'r2', mode: 'executor', real_writeback: true}]}, 'dq');
    assert.match(real, /已写回业务系统/); assert.ok(!real.includes('只读试算'));
    const trace = win.renderEvidenceCard({type: 'trace', conclusion: 'c', basis: [
      {kind: 'relation', text: 'ops.op_id → orders.last_op_id(末道工序)', role: '末道工序', status: 'asserted'}]}, 'dq');
    assert.match(trace, /末道工序/); assert.match(trace, /asserted/);
  },
  text_from_data_and_models_is_escaped() {
    const {win} = load();
    const evil = '<img src=x onerror=alert(1)>';
    const html = win.renderEvidenceCard({type: 'lookup', conclusion: evil, kv: [[evil, evil]],
      basis: [{kind: 'sql', title: evil, text: evil}, {kind: 'caveat', text: evil}], flags: [evil],
      next: [{kind: 'question', label: evil, q: evil}]}, 'dq');
    assert.ok(!html.includes('<img'));
  },
  next_step_questions_go_to_the_registered_host_and_links_change_the_page() {
    const {win, ctx} = load();
    const asked = [];
    win.EVC_HOSTS.dq = q => asked.push(q);
    const html = win.renderEvidenceCard({type: 'judge', conclusion: 'c',
      next: [{kind: 'question', label: '追踪 P1001', q: '订单 P1001 现在到哪了?'}, {kind: 'action', label: '任务重排', href: '#actioncenter'}],
      links: [{label: '规则本', href: '#graph'}]}, 'dq');
    assert.match(html, /data-q="订单 P1001 现在到哪了\?"/); assert.match(html, /data-href="#actioncenter"/); assert.match(html, /规则本 ↗/);
    win.EVC_GO({dataset: {q: '订单 P1001 现在到哪了?', host: 'dq'}});
    assert.deepEqual(asked, ['订单 P1001 现在到哪了?']);
    win.EVC_GO({dataset: {href: '#actioncenter', host: 'dq'}});
    assert.equal(ctx.location.hash, 'actioncenter');
    win.EVC_GO({dataset: {q: 'x', host: 'other'}});       // 未登记的宿主不串用
    assert.equal(asked.length, 1);
  },
  candidates_are_native_buttons_and_page_links_are_real_links() {
    const {win} = load();
    const html = win.renderEvidenceCard({type: 'clarify', conclusion: 'c',
      next: [{kind: 'question', label: '客户:C002 · 上海', q: '…(客户取 C002)'}, {kind: 'action', label: '任务重排', href: '#actioncenter'}],
      links: [{label: '规则本', href: '#graph'}]}, 'dq');
    // 候选要能用键盘选:原生按钮自带聚焦与 Enter / 空格;跳转是带 href 的链接
    assert.match(html, /<button type="button"[^>]*data-q="…\(客户取 C002\)"[^>]*>客户:C002 · 上海<\/button>/);
    assert.match(html, /<a href="#actioncenter"[^>]*>任务重排<\/a>/); assert.match(html, /<a href="#graph"[^>]*>规则本 ↗<\/a>/);
    assert.ok(!/<a (?![^>]*href=)/.test(html));                  // 没有不带 href 的 <a>
  },
};

const name = process.argv[2];
if (!cases[name]) { console.error('unknown case: ' + name); process.exit(2); }
cases[name]();

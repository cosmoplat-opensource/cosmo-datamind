'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const ui = fs.readFileSync(path.join(root, 'ui/index.html'), 'utf8');

class Element {
  constructor() {
    this.value = ''; this.innerHTML = ''; this.textContent = ''; this.disabled = false;
    this.style = {}; this.dataset = {}; this.children = []; this.options = [];
    this.attributes = {}; this.isConnected = true; this.placeholder = '';
    this.classList = {add() {}, remove() {}, contains() { return false; }};
    this.selectors = new Map();
  }
  querySelector(selector) {
    if (selector === '.bc-welcome') return this.innerHTML.includes('bc-welcome') ? {} : null;
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }
  querySelectorAll() { return []; }
  insertAdjacentHTML(_where, html) { this.innerHTML += html; }
  appendChild(el) { this.children.push(el); return el; }
  replaceChildren(...children) { this.children = children; this.options = children; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key]; }
  removeAttribute(key) { delete this.attributes[key]; }
  focus() { this.focused = true; }
}
function fixture() {
  const elements = new Map();
  const $ = selector => { if (!elements.has(selector)) elements.set(selector, new Element()); return elements.get(selector); };
  const state = {rendered: [], messages: [], saved: new Map()};
  const context = {
    console, TextDecoder, TextEncoder, AbortController, ReadableStream, Response, setTimeout, clearTimeout,
    document: {querySelector: $, querySelectorAll: () => [], createElement: () => new Element(), createTextNode: text => ({textContent: text})},
    $, J: async () => ({}), fetch: async () => { throw Error('unexpected fetch'); },
    toast: (msg) => state.messages.push(msg), a11yScan() {}, loaded: {},
    esc: x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    localStorage: {getItem: k => state.saved.get(k) ?? null, setItem: (k,v) => state.saved.set(k,v)},
  };
  context.window = context;
  context.jsAttr = x => context.esc(JSON.stringify(x));
  context.bcRefsPayload = () => ({version:1});
  context.bcRefsText = () => '测试参照';
  context.bcRefsApply = x => { state.references = x; };
  const ctx = vm.createContext(context);
  const streamPath = path.join(root, 'ui/modules/build-stream.js');
  if (fs.existsSync(streamPath)) vm.runInContext(fs.readFileSync(streamPath, 'utf8'), ctx);
  vm.runInContext(ui.slice(ui.indexOf("const BC={"), ui.indexOf('// ══════ 数据连接 ══════')), ctx);
  state.renderResult = ctx.bcRenderResult;
  state.loadBuilt = ctx.bcBuilt;
  ctx.bcRenderResult = (_log, done) => state.rendered.push(done);
  ctx.bcBuilt = () => {}; ctx.bcSyncHeight = () => {};
  $('#bc_q').value = '补充订单与客户关系'; $('#bc_name').value = '测试本体';
  $('#bc_iter_hist').style.display = 'none';
  const run = code => vm.runInContext(code, ctx);
  return {ctx, $, state, run};
}
function stream(text) {
  const bytes = new TextEncoder().encode(text);
  // Split every byte, including UTF-8 characters and CR/LF frame boundaries.
  let offset = 0;
  return new Response(new ReadableStream({pull(controller) {
    if (offset === bytes.length) controller.close(); else controller.enqueue(bytes.slice(offset, ++offset));
  }}), {headers:{'Content-Type':'text/event-stream'}});
}
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; }

const cases = {
  async upload_failure_is_not_success() {
    const f=fixture(); f.ctx.FormData=class {append(){}};
    f.$('#bc_files').files=[{name:'orders.csv'}]; f.ctx.bcSources=()=>{};
    f.ctx.J=async()=>({saved:[],tables:[{table:'orders.csv',error:'CSV 列名重复'}]});
    await f.ctx.bcUpload();
    assert.match(f.state.messages.at(-1),/失败.*CSV 列名重复/);
    assert.ok(!f.state.messages.at(-1).includes('已上传'));
  },
  async truncated_stream() {
    const f = fixture(); f.ctx.fetch = async () => stream('data: {"type":"status","text":"处理中"}\n\n');
    await f.ctx.bcInquire();
    assert.equal(f.state.rendered.length, 0);
    assert.match(f.$('#bc_log').children[0].innerHTML, /未收到完成|中断|未完成/);
    assert.equal(f.$('#bc_q').value, '补充订单与客户关系', 'failed request must preserve the user draft');
    assert.equal(f.run('BC_BUSY'), false); assert.equal(f.$('#bc_send').disabled, false);
  },
  async http_error() {
    const f = fixture(); f.ctx.fetch = async () => new Response(JSON.stringify({error:'构建名额已满'}), {status:429, headers:{'Content-Type':'application/json'}});
    await f.ctx.bcInquire();
    assert.match(f.$('#bc_log').children[0].innerHTML, /429.*构建名额已满|构建名额已满.*429/);
    assert.equal(f.$('#bc_q').value, '补充订单与客户关系');
  },
  async terminal_error() {
    const f = fixture(); f.ctx.fetch = async () => stream('data: {"type":"error","error":"缺少可读表"}\n\n');
    await f.ctx.bcInquire();
    assert.match(f.$('#bc_log').children[0].innerHTML, /缺少可读表/);
    assert.equal(f.$('#bc_q').value, '补充订单与客户关系');
  },
  async success_and_duplicate_submit() {
    const f = fixture(), wait = deferred(); let calls = 0;
    f.ctx.fetch = async () => { calls++; await wait.promise; return stream('data: {"type":"done","graph_key":"built_test","name":"订单"}\n\n'); };
    const first = f.ctx.bcInquire(); await f.ctx.bcInquire();
    assert.equal(calls, 1); assert.equal(f.$('#bc_send').disabled, true);
    f.$('#bc_q').value = '下一轮诉求'; wait.resolve(); await first;
    assert.equal(f.state.rendered.length, 1); assert.equal(f.$('#bc_q').value, '下一轮诉求');
    assert.equal(f.$('#bc_send').disabled, false);
  },
  async crlf_stream() {
    const f = fixture(); f.ctx.fetch = async () => stream(': 心跳\r\ndata: {"type":"status","text":"构建中"}\r\n\r\ndata:{"type":"done",\r\ndata: "graph_key":"built_test","name":"订单"}\r\n\r\n');
    await f.ctx.bcInquire(); assert.equal(f.state.rendered.length, 1);
    assert.equal(f.state.rendered[0].name, '订单');
  },
  async result_html_escaping() {
    const f = fixture(), attack = '<img src=x onerror=alert(1)>';
    const html = f.ctx.bcResultHTML({name:attack, elapsed:attack, stats:{objects:attack}, graph_key:'test'});
    assert.ok(!html.includes(attack), 'API/local history fields must never create HTML nodes');
    assert.ok(html.includes('&lt;img'));
  },
  async iteration_race() {
    const f = fixture(), a = deferred(), b = deferred();
    f.ctx.J = url => url.endsWith('/a') ? a.promise : b.promise;
    const first = f.ctx.bcIterate('a','旧底本'), second = f.ctx.bcIterate('b','新底本');
    b.resolve({references:{id:'b'}, rounds:[]}); await second;
    a.resolve({references:{id:'a'}, rounds:[]}); await first;
    assert.equal(f.run('BC.base'), 'b'); assert.equal(f.state.references.id, 'b');
    assert.match(f.$('#bc_log').innerHTML, /新底本/); assert.ok(!f.$('#bc_log').innerHTML.includes('旧底本'));
  },
  async clear_iteration_race() {
    const f = fixture(), wait = deferred(); f.ctx.J = () => wait.promise;
    const pending = f.ctx.bcIterate('a','旧底本'); f.ctx.bcIterClear();
    wait.resolve({references:{id:'a'},rounds:[]}); await pending;
    assert.equal(f.run('BC.base'), ''); assert.ok(!f.$('#bc_log').innerHTML.includes('旧底本'));
  },
  async busy_iteration_guard() {
    const f = fixture(); f.run("BC.base='a'; BC.baseName='订单'; BC_BUSY=true;");
    f.ctx.bcIterClear(); assert.equal(f.run('BC.base'), 'a');
  },
  async corrupt_history() {
    const f = fixture(); f.state.saved.set('bc_convs','{"bad":"shape"}');
    assert.ok(Array.isArray(f.ctx.bcAllConv())); f.ctx.bcRenderHist();
  },
  async late_reference_catalog() {
    const f = fixture(), wait = deferred();
    f.$('#bc_ref_industry_mode').options = [{value:'reference'}, {value:'constraint'}];
    f.$('#bc_ref_standard_mode').options = [{value:'reference'}, {value:'constraint'}];
    f.ctx.fetch = () => wait.promise;
    vm.runInContext(fs.readFileSync(path.join(root,'ui/modules/build-references.js'),'utf8'), f.ctx);
    f.$('#bc_ref_industry').value = 'chemical'; f.ctx.bcRefsSync();
    wait.resolve(new Response(JSON.stringify({industries:[{id:'none',name:'无'},{id:'chemical',name:'化工'}],ontology_standards:[{id:'bfo_iof',name:'BFO'}]}),{headers:{'Content-Type':'application/json'}}));
    await f.ctx.bcRefsInit(); assert.equal(f.$('#bc_ref_industry').value, 'chemical', 'late catalog response must preserve current user choices');
  },
  async duplicate_preview_ids() {
    const f = fixture(), done = {graph_key:'built_order', name:'订单', stats:{}};
    f.state.renderResult(f.$('#bc_log'),done); f.state.renderResult(f.$('#bc_log'),done);
    const ids = [...f.$('#bc_log').innerHTML.matchAll(/ id="([^"]+)"/g)].map(m=>m[1]);
    assert.equal(new Set(ids).size, ids.length, 'each iteration needs an independent graph container');
  },
  async history_clears_old_base() {
    const f = fixture(); f.run("BC.base='unrelated';BC.baseName='其他本体';");
    f.state.saved.set('bc_convs', JSON.stringify([{id:'history',turns:[{q:'旧问题',done:{graph_key:'built_old'}}]}]));
    f.ctx.bcOpen('history'); assert.equal(f.run('BC.base'), '', 'history must not reuse another conversation’s merge target');
  },
  async failed_context_blocks_build() {
    const f = fixture(); f.ctx.J = async()=>({error:'历史读取失败'}); let calls = 0;
    f.ctx.fetch = async()=>{calls++;return stream('data: {"type":"done","graph_key":"built_bad"}\n\n');};
    await f.ctx.bcIterate('missing','缺失的底本'); await f.ctx.bcInquire();
    assert.equal(calls,0, 'failed context must not silently use the previous references');
    f.ctx.bcIterClear(); assert.equal(f.$('#bc_send').disabled,false);
  },
  async stream_guards() {
    const f = fixture();
    await assert.rejects(f.ctx.bcReadBuildStream(stream('data: {bad}\n\n'),()=>{}), /格式无效/);
    await assert.rejects(f.ctx.bcReadBuildStream(stream('data: {"type":"done"}\n\n'),()=>{}), /图谱标识/);
    await assert.rejects(f.ctx.bcReadBuildStream(new Response('{}',{headers:{'Content-Type':'application/json'}}),()=>{}), /事件流/);
    const huge = new Response('data: '+ 'a'.repeat(1048580), {headers:{'Content-Type':'text/event-stream'}});
    await assert.rejects(f.ctx.bcReadBuildStream(huge,()=>{}), /过大/);
    let cancelled = false;
    const stalled = new Response(new ReadableStream({cancel(){cancelled=true;}}),{headers:{'Content-Type':'text/event-stream'}});
    await assert.rejects(f.ctx.bcReadBuildStream(stalled,()=>{},5), /长时间无响应/);
    assert.equal(cancelled,true);
    const completed = new Response(new ReadableStream({start(c){c.enqueue(new TextEncoder().encode('data: {"type":"done","graph_key":"test"}\n\n'));}}),{headers:{'Content-Type':'text/event-stream'}});
    assert.equal((await f.ctx.bcReadBuildStream(completed,()=>{},5)).graph_key,'test');
  },
  async failure_preserves_new_draft() {
    const f=fixture(), wait=deferred(); f.ctx.fetch=()=>wait.promise;
    const running=f.ctx.bcInquire(); f.$('#bc_q').value='下一轮诉求'; wait.resolve(stream('data: {"type":"error","error":"构建失败"}\n\n'));
    await running; assert.equal(f.$('#bc_q').value,'下一轮诉求');
  },
  async built_load_failure() {
    const f=fixture();f.ctx.J=async()=>({error:'服务暂不可用'});await f.state.loadBuilt();
    assert.match(f.$('#bc_built').innerHTML,/服务暂不可用/);assert.match(f.$('#bc_built').innerHTML,/重新加载/);
  },
  async sources_load_failure() {
    const f=fixture();f.ctx.J=async()=>({error:'服务暂不可用'});await f.ctx.bcSources();
    assert.match(f.$('#bc_sources').innerHTML,/服务暂不可用/);assert.match(f.$('#bc_sources').innerHTML,/重新加载/);
  },
  async skills_load_failure() {
    const f=fixture();f.ctx.J=async()=>({error:'服务暂不可用'});await f.ctx.bcSkills();
    assert.match(f.$('#bc_skills').innerHTML,/服务暂不可用/);assert.match(f.$('#bc_skills').innerHTML,/重新加载/);
  },
  async failed_delete_preserves_base() {
    const f=fixture();f.run("BC.base='built_order';BC_DEL_ARM.built_order=1;");f.ctx.J=async()=>({error:'无权限删除'});
    await f.ctx.bcDelBuilt('built_order',new Element());
    assert.equal(f.run('BC.base'),'built_order');assert.ok(f.state.messages.includes('无权限删除'));
  },
  async engine_empty_registry() {
    const f=fixture();f.ctx.egRenderLLM=()=>{};
    f.ctx.J=async()=>({driver:'hermes',runtimes:[],model_options:{},task_models:{},keys:{},llm:{ready:false}});
    vm.runInContext(ui.slice(ui.indexOf('let EG=null;'),ui.indexOf('async function egPost')),f.ctx);
    await f.ctx.egLoad();
    assert.match(f.$('#eg_runtimes').innerHTML,/没有已注册的运行时/);
    assert.ok(!f.$('#eg_runtimes').innerHTML.includes('仅 OpenAI 兼容端点一种运行时'));
    assert.match(f.$('#eg_runtimes').innerHTML,/未注册.*所有大模型环节会退回内置模板/s);
  },
};
if (!cases[process.argv[2]]) throw new Error('unknown case');
cases[process.argv[2]]().then(() => console.log(`PASS ${process.argv[2]}`)).catch(error => { console.error(error); process.exitCode = 1; });

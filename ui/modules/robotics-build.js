/* Source build controller and page — DR-060 / DR-063 / DR-064. */
(function (global) {
  'use strict';
  const terminal = status => ['completed', 'failed', 'interrupted'].includes(status);
  const errorOf = e => ({message: e.message || String(e), ...(e.status ? {status:e.status} : {}), ...(e.code ? {code:e.code} : {})});
  async function json(fetcher, url, options) {
    const response = await fetcher(url, options);
    let body;
    try { body = await response.json(); } catch (_) { throw Object.assign(new Error(`请求失败（HTTP ${response.status}）：响应不是 JSON`), {status:response.status}); }
    if (!response.ok) throw Object.assign(new Error(body.error?.message || body.error || `请求失败（HTTP ${response.status}）`), {status:response.status, code:body.error?.code});
    return body;
  }
  function createController(options = {}) {
    const fetcher = options.fetch || global.fetch.bind(global), emit = options.onChange || (() => {});
    const schedule = options.schedule || global.setTimeout.bind(global), cancel = options.cancelSchedule || global.clearTimeout.bind(global);
    const newId = options.requestId || (() => global.crypto.randomUUID());
    let state = {enabled:false, busy:false, draft:{name:'', profile:'ur_pick_place', behavior_tree:null, node_models:null}, run:null, events:[], after:0, error:null};
    let caps = null, dead = false, generation = 0, timer = null, flight = null, selected = null, requestId = null, retryId = null;
    const snapshot = () => ({...state, draft:{...state.draft}, events:[...state.events]});
    const notify = () => { if (!dead) emit(snapshot()); };
    const clearTimer = () => { if (timer !== null) cancel(timer); timer = null; };
    const current = token => !dead && token === generation;
    function later() {
      clearTimer();
      if (!dead && selected && !terminal(state.run?.status)) {
        const token = generation;
        timer = schedule(() => { timer = null; if (current(token)) return poll(); }, 1000);
      }
    }
    function select(id, row = null) {
      generation++; clearTimer(); flight = null; selected = id; retryId = null;
      state = {...state, run:row, events:[], after:0, error:null};
    }
    async function init() {
      try {
        const result = await json(fetcher, '/api/robotics/capabilities');
        if (dead) return;
        caps = result; state.enabled = result.enabled === true; state.limits = result.limits; state.error = null;
      } catch (e) { if (dead) return; state.enabled = false; state.error = errorOf(e); }
      notify();
    }
    function setDraft(patch) {
      if (dead) return;
      if (Object.keys(patch).some(k => state.draft[k] !== patch[k])) requestId = null;
      state.draft = {...state.draft, ...patch}; notify();
    }
    function validate() {
      if (!state.enabled) return '机器人构建功能未启用。';
      const d = state.draft;
      if (!d.name?.trim()) return '请填写机器人本体名称。';
      if (d.name.length > 256) return '本体名称不能超过 256 个字符。';
      if (!caps?.recipes?.find(r => r.id === 'bt_xml')?.profiles?.includes(d.profile)) return '此机器人 profile 不受支持。';
      for (const [key, label] of [['behavior_tree','主行为树 XML'], ['node_models','节点模型 XML']]) {
        const file = d[key];
        if (!file || !file.size) return `请选择非空的${label}。`;
        if (file.size > caps.limits.file_bytes) return `${label}超过单文件大小限制（${caps.limits.file_bytes} 字节）。`;
      }
      return null;
    }
    async function submit() {
      if (dead || state.busy) return;
      const problem = validate();
      if (problem) { state.error = {message:problem}; notify(); return; }
      requestId ||= newId();
      const body = new FormData();
      for (const [k,v] of Object.entries({name:state.draft.name, recipe:'bt_xml', profile:state.draft.profile, request_id:requestId,
        behavior_tree:state.draft.behavior_tree, node_models:state.draft.node_models})) body.append(k,v);
      const token = generation;
      state.busy = true; state.error = null; notify();
      try {
        const row = await json(fetcher, '/api/robotics/builds', {method:'POST', body});
        if (!current(token)) return;
        select(row.run_id, row); later();
      } catch (e) { if (current(token)) state.error = errorOf(e); }
      finally { if (!dead) { state.busy = false; notify(); } }
    }
    function poll() {
      if (dead || !selected) return Promise.resolve();
      if (flight) return flight;
      clearTimer();
      const token = generation, id = selected, after = state.after;
      const promise = (async () => {
        try {
          const row = await json(fetcher, '/api/domain-runs/' + encodeURIComponent(id));
          if (!current(token)) return;
          // Read events after the run: a terminal row must include its committed final event.
          const events = await json(fetcher, '/api/domain-runs/' + encodeURIComponent(id) + '/events?after=' + after);
          if (!current(token)) return;
          if (row.run_id !== id) throw new Error('返回的运行身份不匹配。');
          const merged = new Map(state.events.map(e => [e.seq, e]));
          for (const event of events.items || []) merged.set(event.seq, event);
          state.events = [...merged.values()].sort((a,b) => a.seq - b.seq);
          state.after = Math.max(after, ...state.events.map(e => e.seq));
          state.run = row; state.error = null;
        } catch (e) { if (current(token)) state.error = errorOf(e); }
        finally {
          if (current(token)) { flight = null; notify(); if (state.error?.status !== 404) later(); }
        }
      })();
      flight = promise;
      return promise;
    }
    async function openRun(id) {
      if (dead) return;
      select(id); notify(); await poll();
    }
    async function retry() {
      if (dead || state.busy || !state.enabled || !['failed','interrupted'].includes(state.run?.status)) return;
      retryId ||= newId();
      const token = generation, id = state.run.run_id;
      state.busy = true; state.error = null; notify();
      try {
        const row = await json(fetcher, '/api/domain-runs/' + encodeURIComponent(id) + '/retry', {
          method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({request_id:retryId})});
        if (!current(token)) return;
        select(row.run_id, row); later();
      } catch (e) { if (current(token)) state.error = errorOf(e); }
      finally { if (!dead) { state.busy = false; notify(); } }
    }
    return {init, setDraft, submit, openRun, poll, retry, snapshot,
      dispose() { dead = true; generation++; clearTimer(); }};
  }

  let active = null, routeKey = null;
  function page(pageName) {
    if (pageName !== 'build') { active?.dispose(); active = null; routeKey = null; return; }
    const query = new URLSearchParams(global.location.hash.startsWith('#build?') ? global.location.hash.split('?')[1] : '');
    const recipe = query.get('recipe') === 'robotics' ? 'robotics' : 'data', runId = query.get('run');
    const key = recipe + ':' + (runId || '');
    if (active && routeKey === key) return;
    active?.dispose(); routeKey = key;
    const doc = global.document, select = doc.getElementById('rb_recipe'), panel = doc.getElementById('rb_panel');
    const robotic = recipe === 'robotics';
    select.value = recipe; select.disabled = true;
    doc.querySelector('#p_build .bc-grid').hidden = robotic;
    doc.querySelector('#p_build .bc-hero').hidden = robotic;
    panel.hidden = !robotic;
    const ui = global.RoboticsAssets;
    panel.innerHTML = `<div class="rb-heading"><span class="tag">机器人本体 · XML 导入</span><h2>从行为树构建本体候选</h2><p class="muted">上传两份 XML，系统保存来源快照并在后台构建。成功后可查看待评审版本及下载文件。</p></div>
      <form id="rb_form" class="card rb-form" novalidate hidden>
        <label>机器人本体名称<input id="rb_name" maxlength="256" autocomplete="off" placeholder="例如：UR 抓移放工作站"></label>
        <label>机器人 profile<select id="rb_profile" aria-label="机器人 profile"><option value="ur_pick_place">UR 抓移放 · ur_pick_place</option></select></label>
        <label>主行为树 XML<input id="rb_tree" type="file" accept=".xml,application/xml,text/xml"></label>
        <label>节点模型 XML<input id="rb_models" type="file" accept=".xml,application/xml,text/xml"></label>
        <p class="muted rb-wide" id="rb_limits">正在读取构建能力…</p>
        <div class="rb-actions rb-wide"><button type="submit">构建机器人本体</button><a href="#jobs">查看构建历史</a></div>
      </form><div id="rb_error" role="alert" hidden></div><section class="card rb-content" data-testid="robotics-run" hidden></section>`;
    const form = doc.getElementById('rb_form'), runPanel = panel.querySelector('[data-testid="robotics-run"]');
    const error = doc.getElementById('rb_error');
    let controller;
    function render(state) {
      const robotOption = select.querySelector('[value="robotics"]');
      if (state.enabled && !robotOption) {
        const option = doc.createElement('option'); option.value = 'robotics'; option.textContent = '行为树 XML → 机器人本体'; select.append(option);
      } else if (!state.enabled && robotOption) robotOption.remove();
      select.value = robotic && state.enabled ? 'robotics' : 'data';
      form.querySelector('button').disabled = !state.enabled || state.busy;
      error.hidden = !state.error; error.textContent = state.error ? ui.errorText(state.error) : '';
      const row = state.run;
      runPanel.hidden = !row;
      if (!row) return;
      const e = ui.escape, ref = row.output_asset_ref;
      runPanel.dataset.runId = row.run_id; runPanel.dataset.status = row.status;
      runPanel.innerHTML = `<div class="rb-heading"><span class="tag">${e(ui.statusText(row.status))}</span><h3>${e(row.build_input?.name)}</h3><p>运行 <code>${e(row.run_id)}</code></p></div>
        ${row.retry_of ? `<p>重试来源 <a href="${e(ui.runLink(row.retry_of))}">${e(row.retry_of)}</a></p>` : ''}
        <ol class="rb-events">${state.events.map(event => `<li data-event-seq="${e(event.seq)}"><span>${e(event.seq)} · ${e(ui.statusText(event.payload?.status || event.payload?.stage || event.kind))}</span><small>${e(event.created_at || '')}</small></li>`).join('')}</ol>
        ${row.result ? ui.readiness(row.result) + ui.issues(row.result.issues) : '<p class="muted">构建在后台运行，离开页面不会取消任务。</p>'}
        ${ui.sources(row.build_input?.sources)}
        <div class="rb-actions">${ref ? `<a href="${e(ui.versionLink(ref.ontology_id,ref.version_id))}">查看本体候选</a>` : ''}
        ${['failed','interrupted'].includes(row.status) && state.enabled ? '<button type="button" data-retry>重试构建</button>' : ''}</div>
        ${ui.downloads(row.artifacts)}<div data-download-error role="alert" hidden></div>`;
      const retryButton = runPanel.querySelector('[data-retry]');
      if (retryButton) { retryButton.disabled = state.busy; retryButton.onclick = () => controller.retry(); }
      ui.bindDownloads(runPanel);
      // Admission already happened; recording a deep link never submits again.
      if (robotic && global.location.hash !== ui.runLink(row.run_id)) {
        routeKey = 'robotics:' + row.run_id;
        global.history.replaceState(null, '', ui.runLink(row.run_id));
      }
    }
    controller = createController({onChange:render}); active = controller;
    select.onchange = () => { global.location.hash = select.value === 'robotics' ? '#build?recipe=robotics' : '#build'; };
    const sync = () => controller.setDraft({name:doc.getElementById('rb_name').value, profile:doc.getElementById('rb_profile').value,
      behavior_tree:doc.getElementById('rb_tree').files[0] || null, node_models:doc.getElementById('rb_models').files[0] || null});
    form.oninput = sync; form.onchange = sync;
    form.onsubmit = event => { event.preventDefault(); sync(); controller.submit(); };
    controller.init().then(async () => {
      if (active !== controller) return;
      select.disabled = false; form.hidden = false;
      doc.getElementById('rb_limits').textContent = controller.snapshot().enabled
        ? `两份文件按角色独立保存；单文件上限 ${controller.snapshot().limits.file_bytes} 字节。仅支持 UR 抓移放模板。` : '机器人构建未启用或能力暂不可读，请联系服务管理员。';
      if (runId) await controller.openRun(runId);
    });
  }
  global.RoboticsBuild = {createController, page};
})(window);

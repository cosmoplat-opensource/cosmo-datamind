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
        caps = result; state.enabled = result.enabled === true; state.limits = result.limits; state.error = result.configuration_issue; state.build_ready=result.build_ready; state.building_method=result.building_method;
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
      if(caps?.build_ready===false)return caps.configuration_issue?.message||'模型配置不可用';
      const d = state.draft;
      if (!d.name?.trim()) return '请填写机器人本体名称。';
      if (d.name.length > 256) return '本体名称不能超过 256 个字符。';
      if (!caps?.recipes?.find(r => r.id === 'bt_xml')?.profiles?.includes(d.profile)) return '此构建模板 不受支持。';
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
      if(state.draft.description)body.append('description',state.draft.description);
      const token = generation;
      state.busy = true; state.error = null; notify();
      try {
        const row = await json(fetcher, '/api/robotics/builds', {method:'POST', body});
        if (!current(token)) return;
        if(row.session_ref?.namespace==='robotics_design'){state.session=row;}else{select(row.run_id, row); later();}
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
    const sessionId=query.get('session');
    const key = recipe + ':' + (sessionId || runId || '');
    if (active && routeKey === key) return;
    active?.dispose(); routeKey = key;
    const doc = global.document, select = doc.getElementById('rb_recipe'), panel = doc.getElementById('rb_panel');
    const robotic = recipe === 'robotics';
    select.value = recipe; select.disabled = true;
    doc.querySelector('#p_build .bc-grid').hidden = robotic;
    doc.querySelector('#p_build .bc-hero').hidden = robotic;
    panel.hidden = !robotic;
    const ui = global.RoboticsAssets;
    select.onchange = () => { global.location.hash = select.value === 'robotics' ? '#build?recipe=robotics' : '#build'; };
    if(robotic&&sessionId){panel.innerHTML='<section class="card" id="design_host"></section>';active=global.RoboticsDesign.mount(panel.firstChild,sessionId);select.disabled=false;return;}
    panel.innerHTML = `<header class="rb-hero"><div class="rb-hero-copy"><svg aria-hidden="true" width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="5" r="2.5"/><circle cx="5" cy="18" r="2.5"/><circle cx="19" cy="18" r="2.5"/><path d="M11 7 6 16M13 7l5 9M8 18h8"/></svg><div><h2>机器人本体构建</h2><p>从已有行为树整理技能、参数与业务知识，为任务生成准备本体。</p><div class="rb-pipeline"><span>上传 XML</span><i>→</i><span>构建与检查</span><i>→</i><span>生成行为树</span></div></div></div><span class="rb-hero-badge">构建模板<br><b>UR 抓移放</b></span></header>
      <div class="rb-workspace"><div class="rb-source-panel card"><div class="rb-section-title"><h3>构建来源</h3><span class="rb-soft-tag">2 份 XML</span></div>
      <form id="rb_form" class="rb-form" novalidate hidden>
        <div><label for="rb_name">机器人本体名称</label><input id="rb_name" maxlength="256" autocomplete="off" placeholder="例如：UR 抓移放工作站"></div>
        <div><label for="rb_profile">构建模板</label><select id="rb_profile" aria-label="构建模板"><option value="ur_pick_place">UR 抓移放 · ur_pick_place</option></select></div>
        <div class="rb-upload"><div class="rb-upload-title"><span aria-hidden="true">01</span><label for="rb_tree">主行为树 XML</label></div><p>包含抓取、移动与放置的流程。</p><input id="rb_tree" type="file" accept=".xml,application/xml,text/xml"></div>
        <div class="rb-upload"><div class="rb-upload-title"><span aria-hidden="true">02</span><label for="rb_models">节点模型 XML</label></div><p>定义节点接口及输入、输出参数。</p><input id="rb_models" type="file" accept=".xml,application/xml,text/xml"></div>
        <div><label for="rb_description">建模要求</label><textarea id="rb_description" maxlength="20000"></textarea><p id="rb_method"></p></div><p class="muted" id="rb_limits">正在读取构建能力…</p>
        <div id="rb_error" role="alert" hidden></div>
        <button type="submit" class="dq-send rb-submit">构建机器人本体</button><a class="rb-history-link" href="#jobs">查看构建历史 →</a>
      </form></div>
      <section class="rb-workbench card"><div class="rb-section-title"><h3>构建工作区</h3><span id="rb_stage" class="rb-soft-tag" role="status">等待上传</span></div>
        <div id="rb_welcome" class="rb-welcome"><div class="rb-empty-mark" aria-hidden="true">◇</div><h3>让已有流程成为可复用的知识</h3><p>在左侧选择两份 XML，点击构建。<br>完成后可查看本体、下载 OWL，并开始描述机器人任务。</p><div class="rb-feature-list"><div><b>技能与流程</b><span>保留原行为树的执行结构</span></div><div><b>配置与参数</b><span>整理已有参数及其来源</span></div><div><b>任务生成</b><span>复用本体，按需补齐信息</span></div></div></div>
        <section class="rb-content" data-testid="robotics-run" hidden></section>
      </section></div>`;
    const form = doc.getElementById('rb_form'), runPanel = panel.querySelector('[data-testid="robotics-run"]');
    const error = doc.getElementById('rb_error');
    let controller;
    function render(state) {
      const robotOption = select.querySelector('[value="robotics"]');
      if (state.enabled && !robotOption) {
        const option = doc.createElement('option'); option.value = 'robotics'; option.textContent = '行为树 XML → 机器人本体'; select.append(option);
      } else if (!state.enabled && robotOption) robotOption.remove();
      select.value = robotic && state.enabled ? 'robotics' : 'data';
      form.querySelector('button').disabled = !state.enabled || state.busy || state.build_ready===false;
      error.hidden = !state.error; error.textContent = state.error ? (state.error.code==='configuration_error'?'模型配置错误：':'')+ui.errorText(state.error) : '';
      doc.getElementById('rb_method').textContent=state.building_method==='legacy_xml'?'离线构建：建模要求不参与语义建模。':state.building_method==='llm_tbox_abox'?'模型辅助构建':'';
      if(state.session){global.location.hash='#build?recipe=robotics&session='+encodeURIComponent(state.session.session_id);return;}
      const row = state.run;
      runPanel.hidden = !row;
      doc.getElementById('rb_welcome').hidden = !!row;
      doc.getElementById('rb_stage').textContent = state.busy ? '正在提交' : row ? ui.statusText(row.status) : '等待上传';
      if (!row) return;
      const e = ui.escape, ref = row.output_asset_ref;
      runPanel.dataset.runId = row.run_id; runPanel.dataset.status = row.status;
      runPanel.innerHTML = `<div class="rb-heading"><span class="tag">${e(ui.statusText(row.status))}</span><h3>${e(row.build_input?.name)}</h3><details class="rb-run-meta"><summary>运行详情</summary><code>${e(row.run_id)}</code></details></div>
        ${row.retry_of ? `<p>重试来源 <a href="${e(ui.runLink(row.retry_of))}">${e(row.retry_of)}</a></p>` : ''}
        <ol class="rb-events">${state.events.map(event => `<li data-event-seq="${e(event.seq)}"><span>${e(event.seq)} · ${e(ui.statusText(event.payload?.status || event.payload?.stage || event.kind))}</span><small>${e(event.created_at || '')}</small></li>`).join('')}</ol>
        ${row.result ? ui.readiness(row.result) + ui.issues(row.result.issues) : '<p class="muted">构建在后台运行，离开页面不会取消任务。</p>'}
        ${ui.sources(row.build_input?.sources)}
        <div class="rb-actions">${ref ? `<a href="${e(global.RoboticsTask.link(ref.ontology_id,ref.version_id))}">生成行为树</a><a href="${e(ui.versionLink(ref.ontology_id,ref.version_id))}">查看本体候选</a>` : ''}
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
    const sync = () => controller.setDraft({description:doc.getElementById('rb_description').value,name:doc.getElementById('rb_name').value, profile:doc.getElementById('rb_profile').value,
      behavior_tree:doc.getElementById('rb_tree').files[0] || null, node_models:doc.getElementById('rb_models').files[0] || null});
    form.oninput = sync; form.onchange = sync;
    form.onsubmit = event => { event.preventDefault(); sync(); controller.submit(); };
    controller.init().then(async () => {
      if (active !== controller) return;
      select.disabled = false; form.hidden = false;
      doc.getElementById('rb_limits').textContent = controller.snapshot().enabled
        ? `每份 XML 最大 ${Math.round(controller.snapshot().limits.file_bytes / 1024 / 1024)} MiB。源文件与构建结果均会保留。` : '机器人构建未启用或能力暂不可读，请联系服务管理员。';
      if (runId) await controller.openRun(runId);
    });
  }
  global.RoboticsBuild = {createController, page};
})(window);

/* Fixed asset versions, catalog and safe artifact presentation — DR-063 / DR-064. */
(function (global) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const errorText = error => [error.status ? `HTTP ${error.status}` : '', error.code, error.message].filter(Boolean).join(' · ');
  const versionLink = (oid, vid) => '#library?asset=' + encodeURIComponent(oid) + '&version=' + encodeURIComponent(vid);
  const runLink = rid => '#build?recipe=robotics&run=' + encodeURIComponent(rid);
  const statusText = status => ({queued:'排队中', running:'构建中', completed:'构建完成', failed:'构建失败', interrupted:'运行中断',
    computing:'计算与结构校验', committing:'保存候选与报告', candidate:'待评审', published:'已发布', retired:'已退役'})[status] || status || '未记录';
  async function json(fetcher, url) {
    const response = await fetcher(url);
    let body;
    try { body = await response.json(); } catch (_) { throw Object.assign(new Error(`请求失败（HTTP ${response.status}）：响应不是 JSON`), {status:response.status}); }
    if (!response.ok) throw Object.assign(new Error(body.error?.message || body.error || `请求失败（HTTP ${response.status}）`), {status:response.status, code:body.error?.code});
    return body;
  }
  function createController(options = {}) {
    const fetcher = options.fetch || global.fetch.bind(global), emit = options.onChange || (() => {});
    let state = {items:[], detail:null, error:null}, dead = false, catalogToken = 0, detailToken = 0;
    const snapshot = () => ({...state, items:[...state.items]});
    const notify = () => { if (!dead) emit(snapshot()); };
    const failure = e => ({message:e.message || String(e), status:e.status, code:e.code});
    return {
      snapshot,
      async loadCatalog({domain} = {}) {
        const token = ++catalogToken;
        state.error = null; notify();
        try {
          const body = await json(fetcher, '/api/ontology-assets' + (domain ? '?domain=' + encodeURIComponent(domain) : ''));
          if (dead || token !== catalogToken) return;
          state.items = body.items || []; state.error = null;
        } catch (e) { if (dead || token !== catalogToken) return; state.items = []; state.error = failure(e); }
        notify();
      },
      async openVersion(oid, vid) {
        const token = ++detailToken;
        state.detail = null; state.error = null; notify();
        try {
          const body = await json(fetcher, '/api/ontology-assets/' + encodeURIComponent(oid) + '/versions/' + encodeURIComponent(vid));
          if (dead || token !== detailToken) return;
          if (body.ontology_id !== oid || body.version_id !== vid) throw new Error('返回的本体或版本身份不匹配。');
          state.detail = body; state.error = null;
        } catch (e) { if (dead || token !== detailToken) return; state.error = failure(e); }
        notify();
      },
      dispose() { dead = true; catalogToken++; detailToken++; }
    };
  }
  function readiness(result = {}) {
    const structure = result.validation?.shacl_conforms === true && result.status === 'built' ? '通过' : result.status === 'error' ? '未通过' : '未记录';
    return `<div class="rb-readiness"><span>结构检查：${structure}</span><span>任务适配：${result.task_readiness === 'not_assessed' ? '未评估' : escape(result.task_readiness || '未评估')}</span><span>执行：${result.execution_readiness === 'not_verified' ? '未验证' : escape(result.execution_readiness || '未验证')}</span></div>`;
  }
  function issues(rows = []) {
    return rows?.length ? '<h4>构建问题与来源定位</h4>' + rows.map(issue => `<div class="rb-issue"><b>${escape(issue.code)}</b><p>${escape(issue.message)}</p><p>${escape(issue.source_role)} · ${escape(issue.source_asset_id)}</p><pre>${escape(JSON.stringify(issue.location || {}, null, 2))}</pre></div>`).join('') : '';
  }
  function sources(rows = []) {
    return rows?.length ? '<details class="rb-sources" open><summary>固定来源快照</summary>' + rows.map(source => `<div><b>${escape(({behavior_tree:'主行为树 XML',node_models:'节点模型 XML'})[source.role] || source.role || '来源')}</b><p>${escape(source.source_asset_id)}</p><p>SHA-256 <code>${escape(source.sha256)}</code></p><small>${escape(source.byte_size)} 字节</small></div>`).join('') + '</details>' : '';
  }
  function downloads(rows = []) {
    return '<div class="rb-actions rb-downloads">' + (rows || []).filter(a => ['ontology','validation','manifest'].includes(a.kind)).map(a =>
      `<a href="${escape(a.download_url)}" data-artifact-name="${escape(a.name || a.version_id + '-' + a.kind + (a.kind === 'ontology' ? '.owl' : '.json'))}">${({ontology:'下载 OWL',validation:'下载报告',manifest:'下载 manifest'})[a.kind]}</a>`).join('') + '</div>';
  }
  function bindDownloads(container) {
    container.querySelectorAll('[data-artifact-name]').forEach(link => { link.onclick = async event => {
      event.preventDefault();
      const message = container.querySelector('[data-download-error]');
      message.hidden = true; message.textContent = '';
      try {
        const url = new URL(link.getAttribute('href'), global.location.origin);
        if (url.origin !== global.location.origin || !(/^\/api\/domain-runs\/[^/]+\/artifacts\/[^/]+$/.test(url.pathname) || /^\/api\/ontology-assets\/[^/]+\/versions\/[^/]+\/(payload|artifacts\/[^/]+)$/.test(url.pathname))) throw new Error('下载地址不可信。');
        const response = await global.fetch(url.href);
        if (!response.ok) {
          let body; try { body = await response.json(); } catch (_) { body = {}; }
          throw new Error(errorText({status:response.status, code:body.error?.code, message:body.error?.message || '下载失败。'}));
        }
        const blob = await response.blob(), objectUrl = URL.createObjectURL(blob), anchor = global.document.createElement('a');
        anchor.href = objectUrl; anchor.download = link.dataset.artifactName; anchor.hidden = true;
        global.document.body.append(anchor); anchor.click(); anchor.remove();
        global.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      } catch (e) { message.textContent = e.message; message.hidden = false; }
    }; });
  }
  let active = null, routeKey = null, jobsGeneration = 0, jobsActive = false;
  function page(pageName) {
    if (pageName !== 'jobs') { jobsGeneration++; jobsActive = false; }
    else if (!jobsActive) { jobsActive = true; loadJobs(); }
    if (pageName !== 'library' || new URLSearchParams(global.location.hash.split('?')[1] || '').get('tab') === 'knowledge') { active?.dispose(); active = null; routeKey = null; return; }
    const params = new URLSearchParams(global.location.hash.startsWith('#library?') ? global.location.hash.split('?')[1] : '');
    const oid = params.get('asset'), vid = params.get('version'), key = (oid || '') + ':' + (vid || '');
    if (active && routeKey === key) return;
    active?.dispose(); routeKey = key;
    const doc = global.document, host = doc.getElementById('ra_host'), version = !!(oid || vid);
    host.innerHTML = version ? '<section class="card rb-content" data-testid="robotics-version"><p>正在读取指定版本…</p></section>' :
      '<div class="rb-toolbar"><label>本体领域<select id="ra_domain" aria-label="本体领域"><option value="">全部领域</option><option value="data">数据本体</option><option value="robotics">机器人本体</option></select></label></div><div data-testid="ontology-catalog" class="rb-catalog"></div>';
    if(!version)global.RoboticsTask?.importButton(host);
    const legacy = doc.getElementById('lib_legacy'); if (legacy) legacy.hidden = version;
    const controller = createController({onChange:state => {
      if (version) {
        const panel = host.querySelector('[data-testid="robotics-version"]'), d = state.detail;
        for (const key of ['ontologyId','versionId','publicationState']) delete panel.dataset[key];
        if (state.error) { panel.innerHTML = `<div role="alert">${escape(errorText(state.error))}</div>`; return; }
        if (!d) { panel.innerHTML = '<p>正在读取指定版本…</p>'; return; }
        panel.dataset.ontologyId = d.ontology_id; panel.dataset.versionId = d.version_id; panel.dataset.publicationState = d.publication_state;
        const counts = d.result?.validation?.ontology_counts || {};
        panel.innerHTML = `<a href="#library">返回本体目录</a><div class="rb-heading"><span class="tag">${escape(statusText(d.publication_state))}</span><h2>${escape(d.asset?.name || d.ontology_id)}</h2><p>${escape(d.asset?.domain === 'robotics' ? '机器人本体' : '数据本体')} · 固定版本</p></div>
          <p class="rb-identities">当前：版本 ${escape(d.version_number)}</p>
          ${d.asset?.domain === 'robotics' ? readiness(d.result || {status:d.validation?.shacl_conforms ? 'built' : undefined, validation:d.validation}) : ''}
          <div class="rb-counts">${Object.entries(counts).map(([kind,n]) => `<div><strong data-count-kind="${escape(kind)}">${escape(n)}</strong><span>${escape(({AtomicSkill:'原子技能',CompositeSkill:'复合技能',SkillInvocation:'技能调用',DataReference:'数据引用',ImplementationConfiguration:'实现配置',ParameterBinding:'参数绑定',SkillParameter:'技能参数'})[kind] || kind)}</span></div>`).join('')}</div>
          ${d.asset?.domain === 'robotics' ? `<p><a href="${escape(global.RoboticsTask.link(d.ontology_id,d.version_id,d.metadata?.mode||d.publication_mode||'real'))}">生成行为树</a></p><p class="muted">高级功能：<a href="${escape(versionLink(d.ontology_id,d.version_id) + '&tab=knowledge')}">维护知识</a></p>` : ''}${sources(d.sources)}${issues(d.result?.issues)}
          ${d.build_run_id ? `<p><a href="${escape(runLink(d.build_run_id))}">查看构建运行与阶段记录</a></p>` : ''}
          ${downloads(d.artifacts)}<div data-download-error role="alert" hidden></div>`;
        bindDownloads(panel);
      } else {
        const list = host.querySelector('[data-testid="ontology-catalog"]');
        if (state.error) { list.innerHTML = `<div role="alert">${escape(errorText(state.error))}</div>`; return; }
        list.innerHTML = state.items.map(item => {
          const legacy = item.entry_kind === 'legacy_reference';
          const title = escape(item.name || item.ontology_id || item.legacy_key);
          const link = legacy || (item.domain === 'data' && item.legacy_key)
            ? `<a href="#graph=${escape(encodeURIComponent(item.legacy_key))}">打开数据图谱</a>`
            : item.version_id ? `<a href="${escape(versionLink(item.ontology_id,item.version_id))}">查看固定版本</a>` : '<span class="muted">暂无版本</span>';
          return `<article class="card rb-content" ${legacy ? `data-legacy-key="${escape(item.legacy_key)}"` : `data-ontology-id="${escape(item.ontology_id)}"`}><h3>${title}</h3><p>${legacy ? '旧数据图谱 · 未登记版本' : (item.domain === 'robotics' ? '机器人本体' : '数据本体') + ' · ' + escape(statusText(item.publication_state))}${item.version_number?' · 版本 '+escape(item.version_number):''}</p>${link}${item.domain==='robotics'&&item.version_id?` · <a href="${escape(global.RoboticsTask.link(item.ontology_id,item.version_id,item.versions?.at(-1)?.metadata?.mode||'real'))}">生成行为树</a>`:''}</article>`;
        }).join('') || '<p class="muted">此领域暂无登记条目。</p>';
      }
    }});
    active = controller;
    if (version) controller.openVersion(oid || '', vid || '');
    else {
      doc.getElementById('ra_domain').onchange = event => {
        if (legacy) legacy.hidden = event.target.value === 'robotics';
        controller.loadCatalog({domain:event.target.value});
      };
      controller.loadCatalog();
    }
  }
  async function loadJobs(offset = 0) {
    const token = ++jobsGeneration, host = global.document.getElementById('ra_jobs');
    host.innerHTML = '<p>正在读取机器人构建历史…</p>';
    try {
      const body = await json(global.fetch.bind(global), '/api/domain-runs?operation=build_ontology&limit=20&offset=' + offset);
      if (token !== jobsGeneration) return;
      host.innerHTML = '<h3>机器人本体构建历史</h3>' + body.items.map(row => `<article class="rb-history"><a href="${escape(runLink(row.run_id))}">${escape(row.build_input?.name || row.run_id)}</a><span>${escape(statusText(row.status))}</span><small>${escape(row.run_id)}</small></article>`).join('') +
        (!body.items.length ? '<p class="muted">暂无构建记录。</p>' : '') + '<div class="rb-actions">' + (offset ? '<button data-prev>上一页</button>' : '') + (body.items.length === 20 ? '<button data-next>下一页</button>' : '') + '</div>';
      host.querySelector('[data-prev]')?.addEventListener('click', () => loadJobs(offset - 20));
      host.querySelector('[data-next]')?.addEventListener('click', () => loadJobs(offset + 20));
    } catch (e) { if (token === jobsGeneration) host.innerHTML = `<div role="alert">${escape(e.message)}</div>`; }
  }
  global.RoboticsAssets = {createController, page, escape, errorText, statusText, runLink, versionLink, readiness, issues, sources, downloads, bindDownloads};
})(window);

/* Fixed asset versions, catalog and safe artifact presentation — DR-063 / DR-064. */
(function (global) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const errorText = error => [error.status ? `HTTP ${error.status}` : '', error.code, error.message].filter(Boolean).join(' · ');
  const versionLink = (oid, vid) => '#library?asset=' + encodeURIComponent(oid) + '&version=' + encodeURIComponent(vid);
  const runLink = rid => '#build?recipe=robotics&run=' + encodeURIComponent(rid);
  const assetIcon = '<svg aria-hidden="true" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5"/></svg>';
  const domainIcon = domain => '<svg aria-hidden="true" viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + (domain === 'robotics'
    ? '<rect x="4" y="7" width="16" height="13" rx="3"/><path d="M12 7V4M2 12v4m20-4v4M9 16h6"/><circle cx="12" cy="3" r="1"/><circle cx="8.5" cy="12" r="1"/><circle cx="15.5" cy="12" r="1"/>'
    : '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>') + '</svg>';
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
    let state = {items:[], detail:null, error:null, loading:false, catalogLoaded:false}, dead = false, catalogToken = 0, detailToken = 0;
    const snapshot = () => ({...state, items:[...state.items]});
    const notify = () => { if (!dead) emit(snapshot()); };
    const failure = e => ({message:e.message || String(e), status:e.status, code:e.code});
    return {
      snapshot,
      async loadCatalog({domain} = {}) {
        const token = ++catalogToken;
        state.error = null; state.loading=true; notify();
        try {
          const body = await json(fetcher, '/api/ontology-assets' + (domain ? '?domain=' + encodeURIComponent(domain) : ''));
          if (dead || token !== catalogToken) return;
          state.items = body.items || []; state.error = null; state.loading=false; state.catalogLoaded=true;
        } catch (e) { if (dead || token !== catalogToken) return; state.items = []; state.error = failure(e); state.loading=false; state.catalogLoaded=true; }
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
    if(result.status==='designed'&&result.contract?.id==='robotics-design')return '<div class="rb-readiness"><span>设计与来源检查：通过</span><span>行为树应用：'+(result.task_readiness==='supported'?'支持':'不支持')+'</span><span>执行：未验证</span></div>';
    const structure = result.validation?.shacl_conforms === true && result.status === 'built' ? '通过' : result.status === 'error' ? '未通过' : '未记录';
    return `<div class="rb-readiness"><span>结构检查：${structure}</span><span>任务适配：${result.task_readiness === 'not_assessed' ? '未评估' : escape(result.task_readiness || '未评估')}</span><span>执行：${result.execution_readiness === 'not_verified' ? '未验证' : escape(result.execution_readiness || '未验证')}</span></div>`;
  }
  // DR-070: design candidates have a generic schema/instance view, independent of UR validation.
  function designDetails(d) {
    const design=d.design, schema=design.tbox||{}, abox=design.abox||{}, report=design.report||{}, coverage=report.source_coverage||{};
    const classes=schema.classes||[], objects=schema.object_properties||[], data=schema.data_properties||[], individuals=abox.individuals||[];
    const labels=new Map([...classes,...objects,...data,...individuals].map(x=>[x.id,x.label||x.id]));
    const names=values=>(values||[]).map(id=>labels.get(id)||id).join('、');
    const table=(heads,rows)=>'<div class="rd-table"><table><thead><tr>'+heads.map(h=>'<th>'+escape(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(v=>'<td>'+escape(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
    const counts=[['类别',classes.length],['对象关系',objects.length],['数据属性',data.length],['实例',individuals.length],['对象断言',(abox.object_assertions||[]).length],['数据断言',(abox.data_assertions||[]).length]];
    const partial=(report.missing_terms||[]).length||(report.omitted_information||[]).length||(report.skipped_assertions||[]).length||(report.skipped_instances||[]).length;
    return `<section class="card ra-section" data-testid="design-overview"><div class="ra-section-heading"><h3>设计本体概览</h3><span>当前固定版本</span></div><div class="rb-counts">${counts.map(([label,n])=>`<div><strong>${n}</strong><span>${label}</span></div>`).join('')}</div></section>
    <section class="card ra-section" data-testid="design-schema"><h3>类别与属性</h3>
      <details open><summary>类别（${classes.length}）</summary>${table(['名称','定义','父类别'],classes.map(x=>[x.label||x.id,x.definition||'',names(x.parent_ids)||'根类别']))}</details>
      <details><summary>对象关系（${objects.length}）</summary>${table(['名称','定义','起点类别','终点类别'],objects.map(x=>[x.label||x.id,x.definition||'',names(x.domain_ids)||'未声明',names(x.range_ids)||'未声明']))}</details>
      <details><summary>数据属性（${data.length}）</summary>${table(['名称','定义','定义域','数据类型'],data.map(x=>[x.label||x.id,x.definition||'',names(x.domain_ids)||'未声明',x.datatype||'未声明']))}</details>
    </section>
    <section class="card ra-section" data-testid="design-instances"><h3>实例与断言</h3><p>根据已确认骨架和 XML 规则抽取。</p>
      <details><summary>实例（${individuals.length}）</summary>${table(['名称','类别','标识'],individuals.map(x=>[x.label||x.id,names(x.class_ids),x.id]))}</details>
      <details><summary>对象关系断言（${(abox.object_assertions||[]).length}）</summary>${table(['主体','关系','对象'],(abox.object_assertions||[]).map(x=>[labels.get(x.subject)||x.subject,labels.get(x.property)||x.property,labels.get(x.object)||x.object]))}</details>
      <details><summary>数据属性断言（${(abox.data_assertions||[]).length}）</summary>${table(['主体','属性','值'],(abox.data_assertions||[]).map(x=>[labels.get(x.subject)||x.subject,labels.get(x.property)||x.property,typeof x.value==='object'?JSON.stringify(x.value):x.value]))}</details>
    </section>
    <section class="card ra-section" data-testid="design-coverage"><h3>来源覆盖与遗漏</h3><p>${partial?'部分覆盖：下列统计不代表所有 XML 信息均已表达。':'未发现已记录的抽取遗漏。'}</p><p>技能调用 ${coverage.calls_emitted??0} / ${coverage.calls_total??0} · 参数绑定 ${coverage.bindings_emitted??0} / ${coverage.bindings_total??0} · 接口端口 ${coverage.declared_ports_emitted??0} / ${coverage.declared_ports_total??0}</p>
    <details><summary>查看完整覆盖与遗漏报告</summary><pre>${escape(JSON.stringify(report,null,2))}</pre></details></section>`;
  }
  function issues(rows = []) {
    return rows?.length ? '<h4>构建问题与来源定位</h4>' + rows.map(issue => `<div class="rb-issue"><b>${escape(issue.code)}</b><p>${escape(issue.message)}</p><p>${escape(issue.source_role)} · ${escape(issue.source_asset_id)}</p><pre>${escape(JSON.stringify(issue.location || {}, null, 2))}</pre></div>`).join('') : '';
  }
  function sourceSize(bytes) {
    if (!Number.isSafeInteger(bytes) || bytes < 0) return '大小未记录';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  function sources(rows = [], {expanded = true} = {}) {
    const labels = {behavior_tree:'主行为树 XML',node_models:'节点模型 XML',imported_ontology:'导入的机器人本体 OWL',demo_supplement:'演示补充材料',evidence:'适配资料',data_snapshot:'数据来源'};
    return rows?.length ? `<details class="rb-sources"${expanded ? ' open' : ''}><summary>构建来源</summary><p class="rb-source-note">本体基于上传或登记时保存的文件副本构建。</p>` + rows.map(source => `<div class="rb-source-file"><svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z"/><path d="M14 3v6h6M8 13h8M8 17h5"/></svg><div><b>${escape(labels[source.role] || '来源文件')}</b><p>${escape(sourceSize(source.byte_size))}</p></div></div>`).join('') + '</details>' : '';
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
    host.innerHTML = version ? '<section class="ra-detail" data-testid="robotics-version"><p class="ra-empty">正在读取指定版本…</p></section>' :
      `<header class="rb-hero ra-hero"><div class="rb-hero-copy">${assetIcon}<div><h2>让本体知识持续复用</h2><p>管理数据与机器人本体，从已有知识开始探索或生成任务。</p></div></div><a class="ra-button ra-hero-action" href="#build">构建本体 <span aria-hidden="true">＋</span></a></header>
      <div class="ra-toolbar card"><label for="ra_domain">本体领域</label><select id="ra_domain" aria-label="本体领域"><option value="">全部领域</option><option value="data">数据本体</option><option value="robotics">机器人本体</option></select><div class="ra-import-host"></div></div><div data-testid="ontology-catalog" class="rb-catalog ra-catalog"></div>`;
    if(!version)global.RoboticsTask?.importButton(host.querySelector('.ra-import-host'));
    const legacy = doc.getElementById('lib_legacy'); if (legacy) legacy.hidden = version;
    const controller = createController({onChange:state => {
      if (version) {
        const panel = host.querySelector('[data-testid="robotics-version"]'), d = state.detail;
        for (const key of ['ontologyId','versionId','publicationState']) delete panel.dataset[key];
        if (state.error) { panel.innerHTML = `<div role="alert">${escape(errorText(state.error))}</div>`; return; }
        if (!d) { panel.innerHTML = '<p class="ra-empty">正在读取指定版本…</p>'; return; }
        panel.dataset.ontologyId = d.ontology_id; panel.dataset.versionId = d.version_id; panel.dataset.publicationState = d.publication_state;
        const counts = d.result?.validation?.ontology_counts || {};
        const session=d.design?.session_ref?.id;
        const designLink=session?'#build?recipe=robotics&session='+encodeURIComponent(session):null;
        panel.innerHTML = `<a class="ra-back" href="#library"><span aria-hidden="true">← </span>返回本体目录</a>
          <section class="card ra-overview"><div class="ra-detail-heading"><div class="ra-icon">${domainIcon(d.asset?.domain)}</div><div class="ra-title"><span class="rt-eyebrow">${d.asset?.domain === 'robotics' ? '机器人本体' : '数据本体'} · 本体详情</span><h2>${escape(d.asset?.name || '未命名本体')}</h2><p>当前：版本 ${escape(d.version_number)}</p></div><span class="rb-soft-tag">${escape(statusText(d.publication_state))}</span></div>
          <div class="ra-detail-actions">${d.asset?.domain === 'robotics' && d.application_compatibility?.status!=='unsupported' && d.application_compatibility?.status!=='invalid' ? `<a class="ra-button ra-primary" href="${escape(global.RoboticsTask.link(d.ontology_id,d.version_id,d.metadata?.mode||d.publication_mode||'real'))}">生成行为树 <span aria-hidden="true">→</span></a><a class="ra-button" href="${escape(global.RoboticsViews.link(d.ontology_id,d.version_id))}">打开图谱</a>` : ''}${d.design?`<a href="/api/ontology-assets/${escape(d.ontology_id)}/versions/${escape(d.version_id)}/exports/json">下载完整 JSON</a><a href="${escape(d.payload_url)}">下载 OWL</a><a href="${escape(global.RoboticsViews.link(d.ontology_id,d.version_id))}">打开图谱</a><p>${escape(d.application_compatibility?.reasons?.join('；'))}</p>`:downloads(d.artifacts)}</div><div data-download-error role="alert" hidden></div></section>
          ${d.asset?.domain === 'robotics' ? `<section class="card ra-section"><div class="ra-section-heading"><h3>验证状态</h3><span>当前版本的检查结果</span></div>${readiness(d.result || {status:d.validation?.shacl_conforms ? 'built' : undefined, validation:d.validation})}</section>` : ''}
          ${d.design?designDetails(d):''}
          ${Object.keys(counts).length ? `<section class="card ra-section"><div class="ra-section-heading"><h3>知识概览</h3><span>本体中的技能与配置</span></div><div class="rb-counts">${Object.entries(counts).map(([kind,n]) => `<div><strong data-count-kind="${escape(kind)}">${escape(n)}</strong><span>${escape(({AtomicSkill:'原子技能',CompositeSkill:'复合技能',SkillInvocation:'技能调用',DataReference:'数据引用',ImplementationConfiguration:'实现配置',ParameterBinding:'参数绑定',SkillParameter:'技能参数'})[kind] || kind)}</span></div>`).join('')}</div></section>` : ''}
          ${d.sources?.length || d.build_run_id || d.asset?.domain === 'robotics' ? `<section class="card ra-section ra-provenance"><div class="ra-section-heading"><h3>来源与维护</h3><span>需要追溯或调整知识时展开查看</span></div>${sources(d.sources,{expanded:false})}<div class="ra-secondary-links">${d.build_run_id ? `<a href="${escape(designLink||runLink(d.build_run_id))}">查看构建运行与阶段记录 →</a>` : ''}${d.design ? (designLink?`<a href="${escape(designLink)}">继续修改设计 →</a>`:'') : d.asset?.domain === 'robotics' ? `<a aria-label="维护知识" href="${escape(versionLink(d.ontology_id,d.version_id) + '&tab=knowledge')}">维护知识 →</a>` : ''}</div></section>` : ''}${issues(d.result?.issues)}`;
        bindDownloads(panel);
      } else {
        const list = host.querySelector('[data-testid="ontology-catalog"]');
        if (state.error) { list.innerHTML = `<div role="alert">${escape(errorText(state.error))}</div>`; return; }
        if(state.loading||!state.catalogLoaded){list.innerHTML='<div class="card ra-empty" role="status">正在加载本体目录…</div>';return;}
        list.innerHTML = state.items.map(item => {
          const legacy = item.entry_kind === 'legacy_reference';
          const title = escape(item.name || item.legacy_key || '未命名本体');
          const link = legacy || (item.domain === 'data' && item.legacy_key)
            ? `<a href="#graph=${escape(encodeURIComponent(item.legacy_key))}">打开数据图谱</a>`
            : item.domain==='robotics'&&item.version_id ? `<a href="${escape(global.RoboticsViews.link(item.ontology_id,item.version_id))}">打开图谱</a>`
            : item.version_id ? `<a href="${escape(versionLink(item.ontology_id,item.version_id))}">查看固定版本</a>` : '<span class="muted">暂无版本</span>';
          return `<article class="card ra-asset" ${legacy ? `data-legacy-key="${escape(item.legacy_key)}"` : `data-ontology-id="${escape(item.ontology_id)}"`}><div class="ra-card-top"><span class="ra-icon">${domainIcon(legacy ? 'data' : item.domain)}</span><span class="rb-soft-tag">${legacy ? '未登记版本' : escape(statusText(item.publication_state))}</span></div><span class="rt-eyebrow">${legacy ? '旧数据图谱' : item.domain === 'robotics' ? '机器人本体' : '数据本体'}</span><h3>${title}</h3><p class="ra-version">${item.version_number ? '版本 '+escape(item.version_number) : '保留原有数据图谱'}</p><div class="ra-card-actions">${link}</div></article>`;
        }).join('') || '<div class="card ra-empty"><div class="rb-empty-mark" aria-hidden="true">◇</div><h3>此领域暂无登记条目</h3><p>构建或导入本体后，即可在这里查看和使用。</p></div>';
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

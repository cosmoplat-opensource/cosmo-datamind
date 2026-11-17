/* Fixed-version ontology detail, bounded tables and independent status axes — DR-155. */
(function (global) {
  'use strict';
  const esc = value => global.RoboticsAssets.escape(value);
  const tabs = ['概览', '类别与关系', '实例', '来源与版本'];
  const size = 25;
  function applicationState(summary = {}) {
    if (!summary.status || summary.status === 'not_assessed') return '尚未检查';
    if (summary.freshness !== 'current') return '检查已过期';
    return summary.status === 'supported' ? '可准备任务' : '有缺项';
  }
  function mount(host, detail, {icon = ''} = {}) {
    const A = global.RoboticsAssets, view = detail.detail_view;
    if (!view || ['ontology_id', 'version_id', 'payload_sha256'].some(key => view[key] !== detail[key]) ||
        ['classes', 'object_properties', 'data_properties', 'individuals', 'object_assertions', 'data_assertions'].some(key => !Array.isArray(view[key]))) {
      throw Error('详情数据与指定的本体版本不匹配，请重新读取。');
    }
    const labels = new Map([...view.classes, ...view.object_properties, ...view.data_properties, ...view.individuals].map(row => [row.id, row.label || row.id]));
    const names = ids => (ids || []).map(id => labels.get(id) || id).join('、');
    const sorted = [...view.individuals].sort((a, b) => a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
    const oid = encodeURIComponent(detail.ontology_id), vid = encodeURIComponent(detail.version_id);
    const base = '/api/ontology-assets/' + oid + '/versions/' + vid;
    const taskLink = global.RoboticsTask.link(detail.ontology_id, detail.version_id, detail.metadata?.mode || detail.publication_mode || 'real') + '&assess=1';
    const graphLink = global.RoboticsViews.link(detail.ontology_id, detail.version_id);
    const method = detail.metadata?.method || (detail.application_design ? 'application_adaptation' : detail.design ? 'llm_tbox_abox' : detail.build_run_id ? 'legacy_xml' : '');
    const methodText = ({llm_tbox_abox:'模型辅助构建', legacy_xml:'XML 离线构建', application_adaptation:'应用适配版本'})[method] || '构建方式未记录';
    const report = detail.design?.report || detail.application_design?.coverage || {};
    const partial = ['missing_terms', 'omitted_information', 'skipped_assertions', 'skipped_instances'].some(key => report[key]?.length);
    const session = detail.design?.session_ref?.id;
    const designLink = session ? '#build?recipe=robotics&session=' + encodeURIComponent(session) : null;
    const historyLink = detail.metadata?.adaptation_id ? '#robotics-task?adaptation=' + encodeURIComponent(detail.metadata.adaptation_id) : designLink || A.runLink(detail.build_run_id);
    const historyLabel = detail.metadata?.adaptation_id ? '查看应用适配记录 →' : '查看构建运行与阶段记录 →';
    const counts = [['类别', '类别数', view.classes.length], ['关系定义', '关系定义数', view.object_properties.length],
      ['实例', '实例数', view.individuals.length], ['实例关系', '实例关系数', view.object_assertions.length]];
    let active = 0, page = 0, query = '', type = '', group = '';
    const positions = new Map();
    host.innerHTML = `<a class="ra-back" href="#library">← 返回本体目录</a>
      <section class="card ra-overview"><div class="ra-detail-heading"><div class="ra-icon">${icon}</div><div class="ra-title"><span class="rt-eyebrow">机器人本体 · 本体详情</span><h2>${esc(detail.asset?.name || '未命名本体')}</h2><p>当前：版本 ${esc(detail.version_number)} · ${esc(methodText)}</p></div><span class="rb-soft-tag">${esc(A.statusText(detail.publication_state))}</span></div>
      <div class="ra-detail-actions"><a class="ra-button ra-primary" href="${esc(taskLink)}">生成行为树 <span aria-hidden="true">→</span></a><a class="ra-button" href="${esc(graphLink)}">打开图谱</a>
      <a href="${base}/exports/json" data-artifact-name="${esc(detail.version_id)}-complete.json">下载完整 JSON</a><a href="${base}/payload" data-artifact-name="${esc(detail.version_id)}.owl">下载 OWL</a></div><div data-download-error role="alert" hidden></div></section>
      <section class="od-summary" aria-label="本体摘要">${counts.map(([label, name, value]) => `<div class="card"><strong aria-label="${name}">${value}</strong><span>${label}</span></div>`).join('')}</section>
      <div class="od-tabs" role="tablist" aria-label="本体详情">${tabs.map((name, i) => `<button type="button" role="tab" id="od-tab-${i}" aria-controls="od-panel" aria-selected="${i === 0}" tabindex="${i === 0 ? 0 : -1}" data-tab="${i}">${name}</button>`).join('')}</div>
      <section id="od-panel" class="card ra-section od-panel" role="tabpanel" aria-labelledby="od-tab-0" tabindex="0"></section>`;
    const panel = host.querySelector('#od-panel');

    function table(name, heads, rows) {
      return `<div class="rd-table"><table aria-label="${esc(name)}"><thead><tr>${heads.map(head => `<th scope="col">${esc(head)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map((value, index) => `<td>${name === '实例列表' && index === 2 ? `<code tabindex="0" title="${esc(value)}">${esc(String(value).split(/[\/#]/).pop() || value)}</code>` : esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    }
    function paged(name, heads, rows, renderRow, key) {
      const index = Math.min(positions.get(key) || 0, Math.max(0, Math.ceil(rows.length / size) - 1));
      positions.set(key, index);
      return `<section class="od-list"><h3>${esc(name)}（${rows.length}）</h3>${rows.length ? table(name, heads, rows.slice(index * size, (index + 1) * size).map(renderRow)) : '<p class="ra-empty">暂无记录。</p>'}
        ${rows.length > size ? `<div class="od-pagination"><span>第 ${index + 1} / ${Math.ceil(rows.length / size)} 页</span><button class="ghost" data-page-key="${key}" data-step="-1" ${index === 0 ? 'disabled' : ''} aria-label="${esc(name)}上一页">上一页</button><button class="ghost" data-page-key="${key}" data-step="1" ${(index + 1) * size >= rows.length ? 'disabled' : ''} aria-label="${esc(name)}下一页">下一页</button></div>` : ''}</section>`;
    }
    function states() {
      const structure = detail.design ? '设计与来源检查：通过' : detail.application_design ? '设计与当前工件：有效' : detail.validation?.shacl_conforms || detail.result?.validation?.shacl_conforms ? '结构检查：通过' : '结构检查：未记录';
      return `<div class="od-states"><div><span>设计与结构</span><strong>${esc(structure)}</strong></div><div><span>应用检查</span><strong>${esc(applicationState(detail.application_compatibility))}</strong></div><div><span>任务就绪</span><strong>尚未提供任务</strong><small>任务适配：未评估</small></div><div><span>执行验证</span><strong>执行未验证</strong><small>执行：未验证</small></div></div>`;
    }
    function advanced(label) {
      return `<details class="od-advanced"><summary>${esc(label)}</summary><div data-raw="${label === '查看完整覆盖与遗漏报告' ? 'report' : 'view'}"></div></details>`;
    }
    function renderInstances() {
      const needle = query.trim().toLocaleLowerCase();
      const items = sorted.filter(row => (!group || row.group === group) && (!type || row.class_ids.includes(type)) &&
        (!needle || (row.label || '').toLocaleLowerCase().includes(needle) || row.id.toLocaleLowerCase().includes(needle)));
      const pages = Math.max(1, Math.ceil(items.length / size));
      page = Math.min(page, pages - 1);
      const results = panel.querySelector('[data-instance-results]');
      let empty = '没有匹配的实例';
      if (!sorted.length) empty = '尚无实例。类别定义不等于实例。';
      else if (group === 'business' && !sorted.some(row => row.group === 'business')) empty = '尚未包含业务对象与配置实例。类别定义不等于实例，可切换到技能与流程或全部对象。';
      results.innerHTML = `${items.length ? table('实例列表', ['名称', '类别', '标识'], items.slice(page * size, (page + 1) * size).map(row => [row.label || row.id, names(row.class_ids), row.id])) : `<p class="ra-empty" role="status">${empty}</p>`}
        <div class="od-pagination"><span role="status">共 ${items.length} 个实例 · 第 ${page + 1} / ${pages} 页</span><button type="button" class="ghost" data-instance-step="-1" ${page === 0 ? 'disabled' : ''}>上一页</button><button type="button" class="ghost" data-instance-step="1" ${page + 1 >= pages ? 'disabled' : ''}>下一页</button></div>`;
      results.querySelectorAll('[data-instance-step]').forEach(button => { button.onclick = () => { page += Number(button.dataset.instanceStep); renderInstances(); }; });
    }
    function render() {
      host.querySelectorAll('[role=tab]').forEach((button, i) => { button.setAttribute('aria-selected', String(i === active)); button.tabIndex = i === active ? 0 : -1; });
      panel.setAttribute('aria-labelledby', 'od-tab-' + active);
      if (active === 0) {
        const coverage = report.source_coverage;
        const coverageCount = key => coverage && Number.isSafeInteger(coverage[key + '_emitted']) && Number.isSafeInteger(coverage[key + '_total'])
          ? coverage[key + '_emitted'] + ' / ' + coverage[key + '_total'] : '未记录';
        const legacyCounts = !detail.design && !detail.application_design ? detail.result?.validation?.ontology_counts || {} : {};
        panel.innerHTML = `<section data-testid="design-overview"><h3>本体概览</h3>${states()}<p class="od-note">浏览本体不会发起应用检查。准备行为树任务时，请使用上方“生成行为树”入口。</p></section>
          <section data-testid="design-coverage"><h3>来源覆盖</h3><p>${partial ? '部分覆盖：已记录的遗漏不代表整个本体为空。' : '未发现已记录的抽取遗漏。'}</p>${coverage ? `<p class="od-note">技能调用 ${esc(coverageCount('calls'))} · 参数绑定 ${esc(coverageCount('bindings'))} · 接口端口 ${esc(coverageCount('declared_ports'))}</p>` : ''}${advanced('查看完整覆盖与遗漏报告')}</section>
          ${Object.keys(legacyCounts).length ? `<section class="od-list"><h3>技能与配置实例</h3><div class="rb-counts">${Object.entries(legacyCounts).map(([kind, value]) => `<div><strong data-count-kind="${esc(kind)}">${esc(value)}</strong><span>${esc(labels.get('https://example.org/ur-skill#' + kind) || kind)}</span></div>`).join('')}</div></section>` : ''}
          ${A.sources(detail.sources, {expanded:false})}`;
      } else if (active === 1) {
        panel.innerHTML = `<div data-testid="design-schema">${paged('类别列表', ['名称', '定义', '父类别'], view.classes, row => [row.label, row.definition || '未记录', names(row.parent_ids) || '根类别'], 'classes')}
          ${paged('关系列表', ['名称', '定义', '起点类别', '终点类别'], view.object_properties, row => [row.label, row.definition || '未记录', names(row.domain_ids) || '未声明', names(row.range_ids) || '未声明'], 'relations')}
          ${paged('数据属性列表', ['名称', '定义', '定义域', '数据类型'], view.data_properties, row => [row.label, row.definition || '未记录', names(row.domain_ids) || '未声明', row.datatype || '未声明'], 'data')}</div>`;
      } else if (active === 2) {
        const typeIds = [...new Set(sorted.flatMap(row => row.class_ids))].sort();
        panel.innerHTML = `<section data-testid="design-instances"><h3>实例</h3><div class="od-filters"><label>搜索实例<input aria-label="搜索实例" type="text" placeholder="输入名称或标识" value="${esc(query)}"></label><label>实例类型<select aria-label="实例类型"><option value="">全部类型</option>${typeIds.map(id => `<option value="${esc(id)}" ${id === type ? 'selected' : ''}>${esc(labels.get(id) || id)}</option>`).join('')}</select></label><label>展示范围<select aria-label="展示范围">${[['', '全部对象'], ['business', '业务对象与配置'], ['skills', '技能与流程'], ['other', '参数与其他对象']].map(([value, label]) => `<option value="${value}" ${value === group ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div><div data-instance-results></div>
          <details class="od-advanced"><summary>查看实例关系与属性断言</summary><div data-assertions></div></details></section>`;
        panel.querySelector('input').oninput = event => { query = event.target.value; page = 0; renderInstances(); };
        panel.querySelector('[aria-label="实例类型"]').onchange = event => { type = event.target.value; page = 0; renderInstances(); };
        panel.querySelector('[aria-label="展示范围"]').onchange = event => { group = event.target.value; page = 0; renderInstances(); };
        renderInstances();
        panel.querySelector('[data-assertions]').parentElement.ontoggle = event => {
          const body = event.currentTarget.querySelector('[data-assertions]');
          body.innerHTML = event.currentTarget.open ? paged('实例关系列表', ['主体', '关系', '对象'], view.object_assertions, row => [names([row.subject]), names([row.property]), names([row.object])], 'assertions') +
            paged('实例属性列表', ['主体', '属性', '值'], view.data_assertions, row => [names([row.subject]), names([row.property]), typeof row.value === 'object' ? JSON.stringify(row.value) : row.value], 'values') : '';
          bindPaging();
        };
      } else {
        panel.innerHTML = `<h3>来源与版本</h3><p>当前固定版本：${esc(detail.version_number)}</p>${A.sources(detail.sources, {expanded:false})}
          <div class="ra-secondary-links">${detail.build_run_id ? `<a href="${esc(historyLink)}">${esc(historyLabel)}</a>` : ''}${designLink ? `<a href="${esc(designLink)}">继续修改设计 →</a>` : ''}</div>${advanced('查看原始详情')}`;
      }
      panel.querySelectorAll('[data-raw]').forEach(body => { body.parentElement.ontoggle = event => { body.innerHTML = event.currentTarget.open ? '<pre>' + esc(JSON.stringify(body.dataset.raw === 'report' ? report : view, null, 2)) + '</pre>' : ''; }; });
      bindPaging();
    }
    function bindPaging() {
      panel.querySelectorAll('[data-page-key]').forEach(button => { button.onclick = () => {
        positions.set(button.dataset.pageKey, (positions.get(button.dataset.pageKey) || 0) + Number(button.dataset.step));
        if (active === 2) {
          const disclosure = panel.querySelector('[data-assertions]').parentElement;
          disclosure.ontoggle({currentTarget:disclosure});
        } else render();
      }; });
    }
    host.querySelectorAll('[role=tab]').forEach((button, i) => {
      button.onclick = () => { active = i; render(); };
      button.onkeydown = event => {
        const index = ({ArrowRight:(i + 1) % tabs.length, ArrowLeft:(i + tabs.length - 1) % tabs.length, Home:0, End:tabs.length - 1})[event.key];
        if (index === undefined) return;
        event.preventDefault(); active = index; render(); host.querySelector('[data-tab="' + active + '"]').focus();
      };
    });
    A.bindDownloads(host);
    global.OntologyLifecycle?.attach(host,{...detail,name:detail.asset?.name},()=>{global.location.hash='#library';});
    render();
  }
  global.OntologyDetail = {mount};
})(window);

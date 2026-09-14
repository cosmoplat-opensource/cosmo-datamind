(function () {
  'use strict';

  const fallbackProfile = {
    version: 1,
    industry: {id: 'none', mode: 'reference'},
    ontology_standard: {id: 'bfo_iof', mode: 'reference'}
  };
  const fallbackCatalog = {
    version: 1,
    industries: [
      {id: 'none', name: '不选择行业参照', description: '仅依据当前数据与资料构建，不套用行业概念模板。'},
      {id: 'manufacturing', name: '制造业', version: '内置参照 1.0', description: '离散制造常见对象与关系参照。'},
      {id: 'chemical', name: '化工行业', version: '内置参照 1.0', description: '流程化工常见对象与关系参照。'},
      {id: 'pcba', name: 'PCBA / SMT', version: '内置参照 1.0', description: 'PCBA 与 SMT 生产对象和关系参照。'}
    ],
    ontology_standards: [
      {id: 'none', name: '不选择本体标准', description: '保留本地业务概念和关系。'},
      {id: 'bfo_iof', name: 'BFO 2020 + IOF Core', version: 'BFO 2020 / IOF 202602'},
      {id: 'isa95', name: 'ISA-95 / IEC 62264', version: 'MESA B2MML V0701'},
      {id: 'ufo', name: 'UFO / OntoUML', version: 'gUFO 1.0.0'}
    ],
    default: fallbackProfile
  };
  const state = {catalog: null, profile: null, loadWarning: '', initPromise: null};

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function item(kind, id) {
    if (!state.catalog) return null;
    const rows = kind === 'industry' ? state.catalog.industries : state.catalog.ontology_standards;
    return (rows || []).find(row => row.id === id) || null;
  }

  function modeName(mode) {
    return mode === 'constraint' ? '强约束' : '参照';
  }

  function read() {
    const industry = document.querySelector('#bc_ref_industry');
    const standard = document.querySelector('#bc_ref_standard');
    const industryMode = document.querySelector('#bc_ref_industry_mode');
    const standardMode = document.querySelector('#bc_ref_standard_mode');
    return {
      version: 1,
      industry: {id: industry ? industry.value : 'none', mode: industryMode ? industryMode.value : 'reference'},
      ontology_standard: {id: standard ? standard.value : 'none', mode: standardMode ? standardMode.value : 'reference'}
    };
  }

  function normalize(profile) {
    profile = profile && typeof profile === 'object' ? profile : fallbackProfile;
    const readChoice = (key, fallbackChoice) => {
      const value = profile[key];
      if (typeof value === 'string') return {id: value, mode: 'reference'};
      return {id: (value && value.id) || fallbackChoice.id,
              mode: (value && value.mode) === 'constraint' ? 'constraint' : 'reference'};
    };
    return {version: 1,
            industry: readChoice('industry', fallbackProfile.industry),
            ontology_standard: readChoice('ontology_standard', fallbackProfile.ontology_standard)};
  }

  function describe(profile) {
    const normalized = normalize(profile || read());
    const parts = [];
    [['industry', normalized.industry], ['ontology_standard', normalized.ontology_standard]].forEach(([kind, choice]) => {
      if (choice.id === 'none') return;
      const found = item(kind, choice.id);
      parts.push(`${found ? found.name : choice.id}（${modeName(choice.mode)}）`);
    });
    return parts.length ? parts.join(' · ') : '未选择行业或本体标准';
  }

  function renderDetail(notes) {
    const detail = document.querySelector('#bc_ref_detail');
    if (!detail) return;
    detail.replaceChildren(document.createTextNode(notes.join(' ') ||
      '不套用行业模板或上层本体标准，仅依据当前数据和资料构建。'));
    if (state.loadWarning) {
      detail.appendChild(document.createTextNode(` ${state.loadWarning} `));
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'ghost';
      retry.style.cssText = 'padding:2px 7px;font-size:10.5px';
      retry.textContent = '重新加载目录';
      retry.onclick = () => init(true);
      detail.appendChild(retry);
    }
  }

  function sync() {
    const profile = read();
    state.profile = profile;
    ['industry', 'standard'].forEach(kind => {
      const select = document.querySelector(`#bc_ref_${kind}`);
      const mode = document.querySelector(`#bc_ref_${kind}_mode`);
      if (select && mode) mode.disabled = select.value === 'none';
    });
    const industry = item('industry', profile.industry.id);
    const standard = item('ontology_standard', profile.ontology_standard.id);
    const notes = [industry && industry.description, standard && standard.description,
                   standard && standard.disclaimer].filter(Boolean);
    if (standard && standard.id !== 'none' && standard.asset) {
      const asset = standard.asset;
      if (asset.ready) {
        const scale = asset.triples ? `${asset.triples} 三元组` : `${asset.term_count || 0} 个术语/类型`;
        notes.push(`本地标准资产已加载：${asset.file_count || 0} 个文件，${scale}，指纹 ${(asset.fingerprint || '').slice(0, 12)}。`);
      } else if (asset.ready === false) {
        notes.push(`本地标准资产未就绪：${(asset.errors || []).join('；') || '文件缺失或解析失败'}。`);
      }
    }
    renderDetail(notes);
    const hint = document.querySelector('#bc_cur_ref');
    if (hint) hint.textContent = describe(profile);
  }

  function fill(selectId, rows, kind) {
    const select = document.querySelector(selectId);
    if (!select) return;
    const options = (rows || []).map(row => {
      const option = document.createElement('option');
      option.value = row.id;
      let suffix = row.version ? ` · ${row.version}` : '';
      if (kind === 'standard' && row.id !== 'none' && row.asset) {
        suffix += row.asset.ready ? ` · 本地已加载 ${row.asset.file_count || 0} 文件` : ' · 本地资产未就绪';
      }
      option.textContent = `${row.name}${suffix}`;
      return option;
    });
    select.replaceChildren(...options);
  }

  function apply(profile) {
    const normalized = normalize(profile);
    const fields = [
      ['#bc_ref_industry', normalized.industry.id],
      ['#bc_ref_industry_mode', normalized.industry.mode],
      ['#bc_ref_standard', normalized.ontology_standard.id],
      ['#bc_ref_standard_mode', normalized.ontology_standard.mode]
    ];
    fields.forEach(([selector, value]) => {
      const el = document.querySelector(selector);
      if (el && [...el.options].some(option => option.value === value)) el.value = value;
    });
    sync();
  }

  function installCatalog(catalog, profile) {
    state.catalog = catalog;
    fill('#bc_ref_industry', catalog.industries, 'industry');
    fill('#bc_ref_standard', catalog.ontology_standards, 'standard');
    apply(profile || catalog.default || fallbackProfile);
  }

  async function fetchCatalog() {
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), 8000) : null;
    try {
      const response = await fetch('/api/build/references', {
        cache: 'no-store', headers: {'Accept': 'application/json'},
        signal: controller ? controller.signal : undefined
      });
      const catalog = await response.json().catch(() => null);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      if (!catalog || !Array.isArray(catalog.industries) || !Array.isArray(catalog.ontology_standards)) {
        throw new Error('目录响应格式无效');
      }
      return catalog;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  async function init(force) {
    if (state.initPromise) return state.initPromise;
    state.loadWarning = '';
    renderDetail(['正在核验本地行业与标准资产目录…']);
    state.initPromise = (async () => {
      try {
        const catalog = await fetchCatalog();
        state.loadWarning = '';
        installCatalog(catalog, state.profile || read());
        return catalog;
      } catch (error) {
        state.loadWarning = `服务端目录不可用，当前使用完整离线选项（${error && error.name === 'AbortError' ? '请求超时' : (error && error.message || '网络错误')}）；重启/更新服务后再构建。`;
        installCatalog(fallbackCatalog, state.profile || read());
        return fallbackCatalog;
      } finally {
        state.initPromise = null;
      }
    })();
    return state.initPromise;
  }

  function reset() {
    apply((state.catalog && state.catalog.default) || fallbackProfile);
  }

  // 模块一到达就先装入完整离线目录，杜绝页面因旧服务、404 或初始化竞态永久停在“加载中”。
  installCatalog(fallbackCatalog, fallbackProfile);
  void init(false);

  window.bcRefsInit = () => init(false);
  window.bcRefsRetry = () => init(true);
  window.bcRefsPayload = () => clone(read());
  window.bcRefsApply = apply;
  window.bcRefsReset = reset;
  window.bcRefsSync = sync;
  window.bcRefsText = describe;
  window.bcRefsCatalogState = state;
})();

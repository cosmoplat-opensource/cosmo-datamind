/* Knowledge forms in the existing library and review routes — DR-059 / DR-061 / DR-063 / DR-064. */
(function (global) {
  'use strict';
  const api = global.RoboticsReview, assets = global.RoboticsAssets, esc = assets.escape;
  const sections = ['知识维护','证据管理','版本比较','重建合并','审核发布'];
  const names = {PartType:'零件类型',Workpiece:'零件',Workstation:'工位',Carrier:'载具',PlacementSlot:'放置位',PerceptionProfile:'感知配置',PlacementProfile:'放置配置',JointConfiguration:'关节配置',CartesianPose:'笛卡尔位姿'};
  const pretty = value => esc(JSON.stringify(value, null, 2));
  function diffView(diff) {
    if (!diff) return '<p>请选择两个固定版本进行比较。</p>';
    return diff.changes.length ? diff.changes.map(row => `<article class="rb-issue"><b>${esc(row.subject)}</b><p>${esc(row.predicate)} · ${!row.before.length ? '新增' : !row.after.length ? '删除' : '修改'}</p><div class="rb-form"><div>原值<pre>${pretty(row.before)}</pre></div><div>新值<pre>${pretty(row.after)}</pre></div></div></article>`).join('') : '<p>没有 RDF 语义变化。</p>';
  }
  let active = null, route = '', lastHash = '', bypass = false;
  async function get(url, options) {
    const r = await fetch(url, options); let b;
    try { b = await r.json(); } catch (_) { throw new Error('HTTP ' + r.status + '：响应不是 JSON'); }
    if (!r.ok) throw new Error('HTTP ' + r.status + ' · ' + b.error?.code + ' · ' + b.error?.message);
    return b;
  }
  function leave(next) {
    if (!active?.controller.snapshot().dirty) { next(); return; }
    if (document.querySelector('[data-knowledge-leave]')) return;
    const dialog = document.createElement('dialog'); dialog.dataset.knowledgeLeave = '';
    dialog.setAttribute('aria-label','未保存的知识修改');
    dialog.innerHTML = '<h3>未保存的知识修改</h3><p>离开后将丢弃当前未保存的操作。</p><div class="rb-actions"><button>继续编辑</button><button>放弃修改并离开</button></div>';
    document.body.append(dialog); dialog.showModal();
    dialog.querySelectorAll('button')[0].onclick = () => { dialog.close(); dialog.remove(); };
    dialog.querySelectorAll('button')[1].onclick = () => { active?.controller.setOperations([]); dialog.close(); dialog.remove(); next(); };
    dialog.addEventListener('cancel', () => dialog.remove());
  }
  document.addEventListener('click', event => {
    if (bypass || !active?.controller.snapshot().dirty) return;
    const target = event.target.closest('a[href],.nav[data-p]');
    if (!target || target.hasAttribute('data-artifact-name')) return;
    event.preventDefault(); event.stopImmediatePropagation();
    leave(() => { bypass = true; target.click(); bypass = false; });
  }, true);
  global.addEventListener('beforeunload', event => { if (active?.controller.snapshot().dirty) { event.preventDefault(); event.returnValue = ''; } });
  api.guardHash = () => {
    if (!active?.controller.snapshot().dirty || global.location.hash === lastHash) return false;
    const target = global.location.hash; global.history.replaceState(null,'',lastHash);
    leave(() => { global.location.hash = target; }); return true;
  };
  api.page = pageName => {
    const params = new URLSearchParams(global.location.hash.split('?')[1] || '');
    const oid = params.get('asset'), vid = params.get('version');
    const enabled = oid && vid && ((pageName === 'library' && params.get('tab') === 'knowledge') || pageName === 'review');
    if (!enabled) { active?.controller.dispose(); active?.panel.remove(); active = null; route = ''; return; }
    if (active && route === global.location.hash) return;
    active?.controller.dispose(); active?.panel.remove();
    route = lastHash = global.location.hash;
    const host = pageName === 'library' ? document.getElementById('ra_host') : document.getElementById('p_review');
    if (pageName === 'library') { host.innerHTML = ''; document.getElementById('lib_legacy').hidden = true; }
    const panel = document.createElement('section'); panel.className = 'card rb-content rr-panel'; panel.dataset.testid = 'robotics-review';
    host.prepend(panel);
    const view = {panel, tab:pageName === 'review' ? '审核发布' : '知识维护', values:{}, targets:{}, catalog:[], detail:null, attachment:null, localError:null, generation:0};
    active = view;
    const prefix = '/api/ontology-assets/' + encodeURIComponent(oid);
    const q = name => panel.querySelector('[data-field="' + name + '"]');
    const value = name => q(name)?.value || '';
    const capture = () => panel.querySelectorAll('[data-field]').forEach(el => { if (el.type !== 'file') view.values[el.dataset.field] = el.value; });
    const input = (key, label, type='text') => `<label>${esc(label)}<input data-field="${key}" aria-label="${esc(label)}" type="${type}" value="${esc(view.values[key] || '')}"></label>`;
    const select = (key, label, choices) => `<label>${esc(label)}<select data-field="${key}" aria-label="${esc(label)}">${choices.map(c => {
      const [id,title] = Array.isArray(c) ? c : [c,c]; return `<option value="${esc(id)}" ${view.values[key] === id ? 'selected' : ''}>${esc(title)}</option>`;
    }).join('')}</select></label>`;
    const button = (name, action, disabled=false) => `<button type="button" data-action="${action}" ${disabled ? 'disabled' : ''}>${name}</button>`;
    const versions = () => view.catalog.find(a => a.ontology_id === oid)?.versions || [];
    const choose = entity => [entity.iri, (entity.label || entity.iri.split('#').pop()) + ' · ' + entity.iri];
    function showError(error) { view.localError = error.message || String(error); render(view.controller.snapshot()); }
    async function refreshDetails(vid) {
      const token = ++view.generation;
      try {
        const [detail, catalog] = await Promise.all([get(prefix + '/versions/' + encodeURIComponent(vid)),get('/api/ontology-assets?domain=robotics')]);
        if (active !== view || token !== view.generation) return;
        view.detail = detail; view.catalog = catalog.items; render(view.controller.snapshot());
      } catch(e) { if (active === view && token === view.generation) showError(e); }
    }
    function entityFields(state) {
      const knowledge = state.knowledge.knowledge, entity = knowledge.entities.find(e => e.iri === value('entity'));
      const fields = knowledge.schema.fields[entity?.entity_type] || ['label','alias'];
      const fieldSelect = q('field'), relationSelect = q('relation'), target = q('target');
      const fill = (el, choices, previous) => { el.innerHTML = choices.map(([id,title]) => `<option value="${esc(id)}">${esc(title)}</option>`).join(''); if (choices.some(c => c[0] === previous)) el.value = previous; };
      fill(fieldSelect,fields.map(f=>[f,f]),value('field') || view.values.field);
      const relations = Object.entries(knowledge.schema.relations).filter(([,r]) => r.source_type === entity?.entity_type);
      fill(relationSelect,relations.map(([k])=>[k,k]),value('relation') || view.values.relation);
      const relation = knowledge.schema.relations[value('relation')];
      const targets = [...knowledge.entities,...(knowledge.references || [])].filter(e=>e.entity_type === relation?.target_type);
      fill(target,[['','清空关联'],...targets.map(choose)],value('target') || view.values.target);
      target.multiple = relation?.max_count === null;
      target.size = target.multiple ? 4 : 1;
      const key = value('entity') + ':' + value('relation');
      const selected = view.targets[key] || entity?.relations[value('relation')] || [];
      [...target.options].forEach(o => { o.selected = selected.includes(o.value); });
      target.onchange = () => { view.targets[key] = [...target.selectedOptions].map(o=>o.value); };
      const description = panel.querySelector('[data-current]');
      description.textContent = entity ? JSON.stringify(entity,null,2) : '';
    }
    function render(state) {
      if (active !== view) return;
      capture();
      const k = state.knowledge;
      if (!k) { panel.innerHTML = state.error ? `<div role="alert">${esc(assets.errorText(state.error))}</div>` : '<p>正在读取固定版本…</p>'; return; }
      panel.dataset.ontologyId = k.ontology_id; panel.dataset.versionId = k.version_id;
      const detail = view.detail?.version_id === k.version_id ? view.detail : null;
      panel.dataset.publicationState = detail?.publication_state || 'candidate';
      const disabled = !state.enabled || state.busy, cleanDisabled = disabled || state.dirty;
      const entities = k.knowledge.entities, references = k.knowledge.references || [];
      panel.innerHTML = `<div class="rb-actions"><a href="#library">返回本体目录</a><a href="#review?asset=${esc(encodeURIComponent(oid))}&version=${esc(encodeURIComponent(k.version_id))}">机器人评审</a></div>
        <h2>机器人知识维护</h2><p>固定版本 <code>${esc(k.version_id)}</code> · ${esc(assets.statusText(detail?.publication_state || 'candidate'))}</p>
        <p>编辑底本：${esc(k.version_id)}<br>当前工作头：${esc(k.head.version_id)} · 修订 ${esc(k.head.revision)}${k.head.version_id !== k.version_id ? '<br>当前明确选择旧版本为底本；保存会基于此底本创建新候选。' : ''}</p><p class="muted">操作者署名用于记录声明；审核结构不代表现场适配或机器人执行已验证。</p>
        ${assets.readiness(detail?.result || {status:detail?.validation?.shacl_conforms ? 'built' : undefined,validation:detail?.validation})}
        ${!state.enabled ? '<p>机器人功能未启用，当前仅可查看。</p>' : ''}
        <div class="rb-form">${input('actor','操作者署名')}${input('reason','操作理由')}</div>
        ${(state.error || view.localError) ? `<div role="alert">${esc(view.localError || assets.errorText(state.error))}</div>` : ''}
        <nav class="rb-actions" aria-label="知识维护分区">${sections.map(name=>`<button type="button" data-section="${name}" aria-pressed="${view.tab === name}">${name}</button>`).join('')}</nav>
        <div data-section-body="知识维护" ${view.tab !== '知识维护' ? 'hidden' : ''}>
          <div class="rb-form">${select('op','编辑方式',[['set_field','修改字段'],['set_relation','设置关系'],['create_entity','新增对象'],['delete_entity','删除对象']])}
          ${select('entity','知识对象',entities.map(choose))}${select('field','知识字段',[])}${input('value','字段值')}
          ${select('relation','关联关系',[])}${select('target','关联对象',[])}
          ${input('newIri','新对象 IRI')}${select('entityType','对象类型',k.knowledge.schema.editable_types.map(t=>[t,names[t] || t]))}${input('label','对象名称')}${input('basis','修改依据')}</div>
          <p class="muted">字段留空登记为未知；删除前需先显式解除引用。关系操作替换目标集合，清空关联将解除该关系。</p>
          <div class="rb-actions">${button('加入修改','add',disabled)}${button('保存新候选','save',disabled || !state.dirty)}${button('放弃待保存操作','discard',disabled || !state.dirty)}</div>
          <pre data-testid="robotics-pending-operations">${pretty(state.operations)}</pre>
          <details open><summary>当前对象</summary><pre data-current></pre></details>
          <p>未知信息与待补依据：${k.knowledge.issues.length}</p><pre>${pretty(k.knowledge.issues)}</pre>
          <div class="rb-actions">${button('接受对象知识','acceptKnowledge',cleanDisabled)}${button('退回对象知识','rejectKnowledge',cleanDisabled)}</div>
        </div>
        <div data-section-body="证据管理" ${view.tab !== '证据管理' ? 'hidden' : ''}>
          <p>上传附件和登记证据不会自动确认。仅审核所选证据的精确内容与适用范围。</p>
          <div class="rb-form">${input('attachment','证据附件','file')}</div><div class="rb-actions">${button('上传证据附件','upload',cleanDisabled)}</div>
          <p>已登记附件：${esc(view.attachment?.source_asset_id || '无')}</p>
          <div class="rb-form">${input('evidenceIri','证据 IRI')}${select('evidenceKind','证据类型',[['grasp_compatibility','抓取兼容依据'],['placement_compatibility','放置兼容依据']])}
          ${select('implementation','技能实现',references.filter(e=>e.entity_type === 'SkillImplementation').map(choose))}
          ${select('partType','适用零件类型',entities.filter(e=>e.entity_type === 'PartType').map(choose))}
          ${select('slot','适用放置位',[['','不指定'],...entities.filter(e=>e.entity_type === 'PlacementSlot').map(choose)])}
          ${input('reference','证据引用')}${input('validUntil','有效期')}</div>
          <div class="rb-actions">${button('登记证据','registerEvidence',cleanDisabled)}</div>
          <div class="rb-form">${select('evidence','待审证据',k.evidence.map(e=>[e.evidence_iri,e.evidence_iri]))}</div>
          <div class="rb-actions">${button('接受证据','acceptEvidence',cleanDisabled)}${button('退回证据','rejectEvidence',cleanDisabled)}${button('撤销证据','revoke',cleanDisabled)}${button('标记证据已替代','supersede',cleanDisabled)}</div>
          ${k.evidence.map(e=>`<article class="rb-issue" data-evidence-iri="${esc(e.evidence_iri)}" data-confirmation-status="${esc(e.confirmation_status)}"><b>${esc(e.evidence_iri)}</b><p>${esc(({pending:'待审核',confirmed:'已确认',needs_review:'需重新审核',rejected:'已退回'})[e.confirmation_status] || e.confirmation_status)} · ${e.valid ? '当前证据有效' : '当前证据无效'}</p><p>${e.issues.map(i=>esc(({scope_changed:'适用范围变化，需要重新评审',revoked:'证据已撤销',expired:'证据已过期',superseded:'证据已替代'})[i.code] || i.code)).join('；')}</p><pre>${pretty(e.declaration)}</pre></article>`).join('')}
        </div>
        <div data-section-body="版本比较" ${view.tab !== '版本比较' ? 'hidden' : ''}>
          <div class="rb-form">${select('diffBase','比较基线版本',versions().map(v=>v.version_id))}${select('diffTarget','比较目标版本',versions().map(v=>v.version_id))}</div>
          <div class="rb-actions">${button('比较版本','compare',state.busy)}</div><div data-testid="robotics-diff">${diffView(state.diff)}</div>
        </div>
        <div data-section-body="重建合并" ${view.tab !== '重建合并' ? 'hidden' : ''}>
          <p>明确选择原始构建和新来源；预览后逐项处理冲突，再保存新的候选。历史版本保持不变。</p>
          <div class="rb-form">${select('mergeBase','原始构建版本',versions().filter(v=>v.metadata?.build_run_id).map(v=>v.version_id))}
          ${select('incomingAsset','新来源资产',view.catalog.filter(a=>a.ontology_id !== oid).map(a=>[a.ontology_id,a.name + ' · ' + a.ontology_id]))}${select('incomingVersion','新来源版本',[])}</div>
          <div class="rb-actions">${button('预览重建合并','preview',cleanDisabled)}</div>
          ${(state.mergePreview?.conflicts || []).map(c=>`<article class="rb-issue" data-conflict-id="${esc(c.id)}"><pre>${pretty(c)}</pre>${select('conflict-' + c.id,'冲突处理',[['','请选择'],...(c.kind === 'evidence_scope' ? [['invalidate','标记证据失效，重新审核']] : c.kind === 'source_identity' ? [] : [['current','保留人工版本'],['incoming','采用新来源'],...(c.kind === 'field' ? [['manual','手工填写新值']] : [])])])}${c.kind === 'field' ? input('manual-' + c.id,'冲突新值') : ''}${c.kind === 'source_identity' ? '<p>调用身份变化不能按旧步骤序号重放，请重新检查来源。</p>' : ''}</article>`).join('')}
          <div class="rb-actions">${button('提交合并','merge',true)}</div>
        </div>
        <div data-section-body="审核发布" ${view.tab !== '审核发布' ? 'hidden' : ''}>
          <p>审核绑定当前版本与 OWL 摘要；发布前服务端重新校验实际字节和结构。任务适配尚未评估，执行未验证。</p>
          <div class="rb-actions">${button('接受当前版本','accept',cleanDisabled)}${button('退回当前版本','reject',cleanDisabled)}${button('发布当前版本','publish',cleanDisabled || state.review?.decision !== 'accepted')}</div>
          <pre>${pretty(state.review || {})}</pre>${assets.downloads(detail?.artifacts)}<div data-download-error role="alert" hidden></div>
          <details><summary>操作历史与依据</summary><pre>${pretty({history:k.history,provenance:k.knowledge.provenance})}</pre></details>
        </div>`;
      // Restore fields only when their option still belongs to the newly loaded version.
      panel.querySelectorAll('[data-field]').forEach(el => { const v = view.values[el.dataset.field]; if (v !== undefined && el.type !== 'file' && (el.tagName !== 'SELECT' || [...el.options].some(o=>o.value === v))) el.value = v; });
      entityFields(state);
      function incomingChoices() {
        const rows = view.catalog.find(a=>a.ontology_id === value('incomingAsset'))?.versions || [];
        q('incomingVersion').innerHTML = rows.filter(v=>v.metadata?.build_run_id).map(v=>`<option value="${esc(v.version_id)}">${esc(v.version_id)}</option>`).join('');
        if (rows.some(v=>v.version_id === view.values.incomingVersion)) q('incomingVersion').value = view.values.incomingVersion;
      }
      incomingChoices();
      q('incomingAsset').onchange = incomingChoices;
      for (const name of ['entity','relation']) q(name).onchange = () => entityFields(state);
      function operationFields() {
        const op = value('op');
        const visible = {entity:op !== 'create_entity',field:op === 'set_field',value:op === 'set_field',
          relation:op === 'set_relation',target:op === 'set_relation',newIri:op === 'create_entity',entityType:op === 'create_entity',label:op === 'create_entity'};
        Object.entries(visible).forEach(([key,show]) => { q(key).parentElement.hidden = !show; });
      }
      q('op').onchange = operationFields; operationFields();
      for (const name of ['actor','reason']) q(name).oninput = () => view.controller.setIdentity({actor:value('actor'),reason:value('reason')});
      const mergeButton = panel.querySelector('[data-action="merge"]');
      const updateMerge = () => { mergeButton.disabled = cleanDisabled || !state.mergePreview || state.mergePreview.conflicts.some(c=>!value('conflict-' + c.id)); };
      panel.querySelectorAll('[data-conflict-id] select').forEach(el=>el.onchange=updateMerge); updateMerge();
      panel.querySelectorAll('[data-section]').forEach(el=>el.onclick=()=>{ view.tab = el.dataset.section; render(view.controller.snapshot()); });
      panel.querySelectorAll('[data-action]').forEach(el=>el.onclick=async()=>{
        capture(); view.localError = null; view.controller.setIdentity({actor:value('actor'),reason:value('reason')});
        try {
          const action = el.dataset.action;
          if (action === 'add') {
            let op = {op:value('op'),iri:value('entity'),basis:value('basis')};
            if (!op.basis.trim()) throw new Error('请填写修改依据');
            if (op.op === 'set_field') Object.assign(op,{field:value('field'),value:value('value') || null});
            if (op.op === 'set_relation') Object.assign(op,{relation:value('relation'),targets:[...q('target').selectedOptions].map(o=>o.value).filter(Boolean)});
            if (op.op === 'create_entity') Object.assign(op,{iri:value('newIri'),entity_type:value('entityType'),label:value('label')});
            view.controller.setOperations([...view.controller.snapshot().operations,op]);
          } else if (action === 'save') await view.controller.save();
          else if (action === 'discard') view.controller.setOperations([]);
          else if (action === 'accept' || action === 'reject') await view.controller.review(action === 'accept' ? 'accepted' : 'rejected');
          else if (action === 'publish') await view.controller.publish();
          else if (action === 'compare') await view.controller.compare(value('diffBase'),value('diffTarget'));
          else if (action === 'upload') {
            const file = q('attachment').files[0]; if (!file) throw new Error('请选择证据附件');
            const body = new FormData(); body.append('file',file);
            view.attachment = await get(prefix + '/evidence-sources',{method:'POST',body}); render(view.controller.snapshot());
          } else if (action === 'registerEvidence') await view.controller.editEvidence('evidence',{evidence:{iri:value('evidenceIri'),kind:value('evidenceKind'),implementation:value('implementation'),part_type:value('partType'),slot:value('slot') || null,reference:value('reference'),valid_until:value('validUntil'),source_asset_ids:view.attachment ? [view.attachment.source_asset_id] : []}});
          else if (action === 'acceptEvidence' || action === 'rejectEvidence') await view.controller.editEvidence('evidence-reviews',{evidence_iri:value('evidence'),decision:action === 'acceptEvidence' ? 'accepted' : 'rejected'});
          else if (action === 'acceptKnowledge' || action === 'rejectKnowledge') await view.controller.editEvidence('knowledge-reviews',{entity_iris:[value('entity')],decision:action === 'acceptKnowledge' ? 'accepted' : 'rejected'});
          else if (action === 'revoke' || action === 'supersede') await view.controller.revoke(value('evidence'), action === 'supersede' ? 'superseded' : 'revoked');
          else if (action === 'preview') {
            const current = view.controller.snapshot().knowledge;
            const baseVersion = versions().find(v=>v.version_id === value('mergeBase'));
            const incoming = view.catalog.find(a=>a.ontology_id === value('incomingAsset'))?.versions.find(v=>v.version_id === value('incomingVersion'));
            if (!baseVersion || !incoming) throw new Error('请选择明确的来源构建版本');
            const ref = v => ({ontology_id:v.ontology_id,version_id:v.version_id,payload_sha256:v.payload_sha256});
            await view.controller.previewMerge({base:ref(baseVersion),current:ref(current),incoming:ref(incoming),expected_head_version_id:current.head.version_id,expected_revision:current.head.revision});
          } else if (action === 'merge') {
            const decisions = Object.fromEntries(view.controller.snapshot().mergePreview.conflicts.map(c=>[c.id,{choice:value('conflict-' + c.id),...(value('conflict-' + c.id) === 'manual' ? {value:value('manual-' + c.id) || null} : {})}]));
            await view.controller.merge(decisions);
          }
        } catch(e) { showError(e); }
      });
      assets.bindDownloads(panel);
      // Complete internal navigation in the click, so the visible fixed-version
      // panel cannot briefly accept input for a page that is already leaving.
      panel.querySelectorAll('a[href^="#"]').forEach(link => { link.onclick = event => {
        event.preventDefault(); global.location.hash = link.getAttribute('href'); global.routeHash();
      }; });
    }
    view.controller = api.createController({onChange:render,onVersion:version=>{
      const next = '#' + pageName + '?asset=' + encodeURIComponent(oid) + '&version=' + encodeURIComponent(version.version_id) + (pageName === 'library' ? '&tab=knowledge' : '');
      global.history.replaceState(null,'',next); route = lastHash = next;
      view.detail = version; return refreshDetails(version.version_id);
    }});
    // Keep the form noninteractive until its initial catalog and fixed metadata
    // are ready. A late metadata response must not replace a focused control.
    (async()=>{ await view.controller.init(); await refreshDetails(vid); await view.controller.openVersion(oid,vid); })();
  };
})(window);

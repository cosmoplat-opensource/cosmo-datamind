/* 构建前预处理(DR-060):外部库只读抽样、构建体检、分轮计划与逐轮构建。
   体检只读、不调模型;逐轮构建复用 bcInquire,每轮在上一轮的本体上继续构建。 */
(function () {
  const PREP = {report: null, running: false, abort: false};
  const LV = {warn: '<span class="bp-lv warn">注意</span>', info: '<span class="bp-lv info">提示</span>'};

  function card(html) {
    const log = $('#bc_log');
    if (log.querySelector('.bc-welcome')) log.innerHTML = '';
    const el = document.createElement('div');
    el.className = 'bc-prep';
    el.innerHTML = html;
    log.appendChild(el);
    log.scrollTop = 1e9;
    return el;
  }

  async function bcPrep() {
    if (BC_BUSY || PREP.running) { toast('构建进行中,请稍候再体检'); return; }
    const el = card(`<div class="bp-h">构建体检 · <b>${esc(BC.srcName || BC.src)}</b></div><div class="muted">正在读取表结构、识别建模规律、核对上传资料…</div>`);
    const d = await J('/api/build/prep', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source: BC.src})}).catch(e => ({error: String(e)}));
    if (!d || d.error) {
      el.innerHTML = `<div class="bp-h">构建体检 · <b>${esc(BC.srcName || BC.src)}</b></div><div role="alert" class="bp-err">${esc(d && d.error || '体检失败')}</div>`
        + (d && d.external ? `<button class="ghost" onclick="bcSampleDlg(${jsAttr(BC.src)})">只读抽样到本地</button>` : '');
      return;
    }
    PREP.report = d;
    render(el, d);
  }

  function render(el, d) {
    const s = d.summary;
    const doc = d.docs || {};
    const refs = (d.references || []).map(r => `<div class="bp-ref"><b>${esc(r.name)}</b> · ${esc(r.reason)}`
      + (r.separate ? '' : ` <a href="#" onclick="event.preventDefault();bcPrepUseRef(${jsAttr(r.industry)})">采用</a>`) + '</div>').join('');
    const fnd = (d.findings || []).map(f => `<details class="bp-f"><summary>${LV[f.level] || ''}${esc(f.title)}</summary>`
      + `<div class="bp-fd">${esc(f.detail)}${f.examples && f.examples.length ? '<div class="muted">例:' + f.examples.map(esc).join(';') + '</div>' : ''}</div></details>`).join('');
    const hubs = (d.hub_keys || []).slice(0, 8).map(h => `<span class="bp-chip">${esc(h.key)} · ${h.tables} 表</span>`).join('');
    const rounds = (d.plan || []).map(p => `<div class="bp-round" data-r="${p.round}">
      <label><input type="checkbox" checked data-round="${p.round}"> <b>第 ${p.round} 轮</b> · ${esc(p.label)} <span class="muted">${p.count} 张表${p.hint ? ' · ' + esc(p.hint) : ''}</span></label>
      <span class="bp-st" aria-live="polite"></span>
      <details><summary class="muted">表清单</summary><div class="bp-tabs">${p.tables.map(esc).join('、')}</div></details></div>`).join('');
    const docLine = doc.files
      ? `上传资料 ${doc.files} 份(${doc.chunks} 块)· 描述到本库 <b>${doc.described}/${doc.tables}</b> 张表`
        + (doc.doc_only_count ? ` · 资料里提到而库里没有的表 ${doc.doc_only_count} 张(如 ${doc.doc_only.slice(0, 3).map(esc).join('、')})` : '')
      : '尚未上传资料;中文语义将只来自库表注释' + (s.has_comments ? '' : '(本库也没有注释,建议上传数据字典或设计文档)');
    // 只预填使用者自己写的诉求;输入框里的默认示例诉求属于示例主库,带到别的数据源会误导建模
    const typed = ($('#bc_q').value || '').trim();
    const goal = typed && typed !== ((BC.def && BC.def.build_query) || '').trim() ? typed : '';
    el.innerHTML = `<div class="bp-h">构建体检 · <b>${esc(d.source.name)}</b></div>
      <div class="bp-sum"><span><b>${s.tables}</b> 张表</span><span><b>${s.columns}</b> 个字段</span><span><b>${s.rows.toLocaleString()}</b> 行</span>
        <span>声明外键 <b>${s.declared_fks}</b></span><span>空表 <b>${d.empty_tables}</b></span>
        <span>${s.has_comments ? `注释 表 ${s.table_comment_pct}% · 字段 ${s.column_comment_pct}%` : '无库表注释'}</span></div>
      <div class="bp-sec">发现</div>${fnd || '<div class="muted">未发现需要特别处理的建模规律</div>'}
      ${hubs ? `<div class="bp-sec">枢纽键(被最多表共用的编号)</div><div>${hubs}</div>` : ''}
      <div class="bp-sec">资料覆盖</div><div class="bp-line">${docLine}</div>
      ${refs ? `<div class="bp-sec">参照建议(能源与碳排放分开处理)</div>${refs}` : ''}
      <div class="bp-sec">分轮计划 <span class="muted">枢纽优先 · 每轮不超过 ${d.max_tables} 张表 · 每轮在上一轮的本体上继续构建;数据验证始终使用全库</span></div>
      ${d.excluded && d.excluded.length ? `<details class="bp-f"><summary><span class="bp-lv info">已排除</span>${d.excluded.length} 张疑似演示/临时/历史分表(不进入计划)</summary><div class="bp-tabs">${d.excluded.map(esc).join('、')}</div></details>` : ''}
      <div class="bp-rounds">${rounds}</div>
      <div class="bp-run">
        <textarea class="bp-goal" aria-label="整体建模目标" placeholder="整体建模目标(每轮都会带上),例:构建能源管理本体,贯通企业—设备—计量—能耗—告警工单">${esc(goal)}</textarea>
        <div class="row" style="gap:6px"><button class="bp-go" onclick="bcPlanRun(this)">按计划逐轮构建</button>
        <button class="ghost bp-stop" style="display:none" onclick="bcPlanStop()">本轮结束后停止</button>
        <span class="muted bp-note"></span></div></div>`;
    if (window.a11yScan) a11yScan();
  }

  function useRef(industry) {
    if (window.bcRefsApply) {
      const cur = window.bcRefsPayload ? bcRefsPayload() : {};
      bcRefsApply({version: 1, industry: {id: industry, mode: 'reference'},
        ontology_standard: cur.ontology_standard || {id: 'bfo_iof', mode: 'reference'}});
      toast('已采用行业参照');
    }
  }

  async function run(btn) {
    const box = btn.closest('.bc-prep'), d = PREP.report;
    if (!d || PREP.running || BC_BUSY) return;
    const picked = [...box.querySelectorAll('input[data-round]')].filter(x => x.checked).map(x => +x.dataset.round);
    const rounds = (d.plan || []).filter(p => picked.includes(p.round));
    if (!rounds.length) { toast('请至少勾选一轮'); return; }
    const goal = (box.querySelector('.bp-goal').value || '').trim() || `为数据源「${d.source.name}」构建业务本体`;
    PREP.running = true; PREP.abort = false;
    btn.disabled = true; box.querySelector('.bp-stop').style.display = '';
    const note = box.querySelector('.bp-note');
    let ok = 0;
    try {
      for (let i = 0; i < rounds.length; i++) {
        const p = rounds[i], st = box.querySelector(`.bp-round[data-r="${p.round}"] .bp-st`);
        if (PREP.abort) { note.textContent = `已在第 ${i} 轮后停止`; break; }
        st.textContent = '构建中…'; note.textContent = `第 ${i + 1}/${rounds.length} 轮`;
        const q = `${goal}\n本轮范围(第 ${p.round} 轮,${p.count} 张表):${p.label}`;
        // 第一轮新建本体时给出明确名称:留空会由模型自拟,实测会照搬提示词里的示例名
        const first = i === 0 && !BC.base;
        // 名称框的默认值(示例主库的默认名)不算使用者填写
        const typedName = ($('#bc_name').value || '').trim();
        const userName = typedName && typedName !== ((BC.def && BC.def.build_name) || '').trim() ? typedName : '';
        const name = first ? (userName || goal.replace(/[,,。;;::\s].*$/, '').slice(0, 24) || `${d.source.name}本体`) : '';
        const done = await bcInquire({q, tables: p.tables, name});
        if (!done) { st.textContent = '✕ 失败'; note.textContent = `第 ${p.round} 轮失败,后续轮次未执行;可修正后只勾选剩余轮次重跑`; break; }
        st.textContent = `✓ ${done.stats ? done.stats.objects + ' 对象 · ' + done.stats.links + ' 关系 · 已验证 ' + done.stats.verified : '完成'}`;
        ok++;
        // 后续轮次在这张本体上继续构建(与「继续构建」同一机制:只增补与升级,不删除既有结论)
        if (!BC.base || BC.base !== done.graph_key) { BC.base = done.graph_key; BC.baseName = done.name || done.graph_key; bcIterSync(); bcHintSync(); }
      }
      if (ok === rounds.length) note.textContent = `全部 ${ok} 轮完成`;
    } finally {
      PREP.running = false; btn.disabled = false; box.querySelector('.bp-stop').style.display = 'none';
    }
  }

  /* ── 外部库只读抽样 ── */
  function sampleDlg(id) {
    const ext = (BC._sources || []).filter(s => s.kind !== 'sqlite' && s.kind !== 'api');
    if (!ext.length) { toast('没有已登记的外部库'); return; }
    const m = $('#bc_sample_modal');
    m.querySelector('.bs-list').innerHTML = ext.map(s => `<label class="bs-row"><input type="checkbox" value="${esc(s.id)}" ${s.id === id ? 'checked' : ''}> ${esc(s.name)}</label>`).join('');
    m.querySelector('.bs-name').value = '';
    m.querySelector('.bs-msg').textContent = '';
    m.querySelector('.bs-go').disabled = false;
    m.style.display = 'flex';
  }

  async function sampleGo() {
    const m = $('#bc_sample_modal'), msg = m.querySelector('.bs-msg'), go = m.querySelector('.bs-go');
    const ids = [...m.querySelectorAll('.bs-list input:checked')].map(x => x.value);
    if (!ids.length) { msg.textContent = '请至少选择一个外部库'; return; }
    go.disabled = true; msg.textContent = '连接中…';
    try {
      const resp = await fetch('/api/build/connect/sample', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'text/event-stream'},
        body: JSON.stringify({ids, name: m.querySelector('.bs-name').value.trim()})});
      if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || ('HTTP ' + resp.status)); }
      const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = '', done = null;
      while (!done) {
        const part = await reader.read(); if (part.done) break;
        buf += dec.decode(part.value, {stream: true});
        let k;
        while ((k = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, k).split('\n').find(x => x.startsWith('data:')); buf = buf.slice(k + 2);
          if (!line) continue;
          const ev = JSON.parse(line.slice(5));
          if (ev.type === 'status') msg.textContent = ev.text;
          else if (ev.type === 'progress') msg.textContent = `「${ev.source}」${ev.done}/${ev.total} 张表 · ${ev.table}`;
          else if (ev.type === 'error') throw new Error(ev.error);
          else if (ev.type === 'done') done = ev;
        }
      }
      if (!done) throw new Error('连接中断,未收到完成结果');
      msg.textContent = `完成:${done.tables} 张表 · ${done.rows.toLocaleString()} 行` + (done.failed.length ? ` · ${done.failed.length} 张读取失败` : '');
      toast('已导入为数据源:' + done.conn.name);
      await bcSources(); bcSelectSrc(done.conn.id);
      setTimeout(() => { m.style.display = 'none'; }, 1200);
    } catch (e) {
      msg.textContent = '抽样失败:' + (e && e.message || e);
    } finally { go.disabled = false; }
  }

  window.bcPrep = bcPrep;
  window.bcPrepUseRef = useRef;
  window.bcPlanRun = run;
  window.bcPlanStop = () => { PREP.abort = true; toast('将在本轮结束后停止'); };
  window.bcSampleDlg = sampleDlg;
  window.bcSampleGo = sampleGo;
})();

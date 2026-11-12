/* Independent ontology application and immutable generation history — DR-069. */
(function(global){
  'use strict';
  const esc=value=>global.RoboticsAssets.escape(value);
  const status=value=>({idle:'未开始',running:'处理中',generated:'已生成',needs_information:'待补齐',needs_clarification:'待明确',unsupported:'暂不支持',error:'未完成'})[value]||value;
  const route=row=>global.RoboticsTask.link(row.ontology_id,row.version_id,row.mode)+'&session='+encodeURIComponent(row.session_id)+(row.kind==='generation'?'&run='+encodeURIComponent(row.run_id):'');
  const time=value=>value&&!Number.isNaN(Date.parse(value))?new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'旧记录';
  let active=null;
  async function read(url,signal){
    const response=await global.fetch(url,{signal});
    const body=await response.json();
    if(!response.ok)throw Error(body.error?.message||`读取失败（HTTP ${response.status}）`);
    return body;
  }
  function page(name){
    active?.dispose();active=null;
    if(name!=='robotics-task')return;
    const root=global.document.getElementById('rt_application'),params=new URLSearchParams(global.location.hash.split('?')[1]||'');
    const abort=new AbortController();
    const view={rows:[],total:0,query:'',sequence:0,timer:null,lastState:'',selected:params.get('run')||params.get('session'),
      dispose(){abort.abort();clearTimeout(this.timer);}};active=view;
    root.innerHTML=`<div class="dq-tabbar"><button class="rta-toggle" type="button" aria-label="折叠或展开任务历史" aria-expanded="true">☰</button><span class="dq-tab">行为树生成</span></div>
      <div class="dq rta-layout"><section class="dq-side rta-side" aria-label="行为树任务历史"><a class="dq-mode on" href="#robotics-task">＋ 新建任务</a>
      <div class="dq-hist-hd"><span>任务与生成记录</span><button type="button" class="ghost" data-history-refresh>刷新</button></div>
      <input type="search" class="rta-search" aria-label="搜索生成记录" placeholder="搜索任务或本体名称"><div class="dq-hist rta-history" data-testid="robotics-task-history" role="navigation" aria-label="历史生成记录"><p class="muted">正在加载记录…</p></div>
      <p class="rta-count" role="status"></p><button type="button" class="ghost" data-history-more hidden>加载更多</button></section>
      <div class="dq-main rta-main"><div id="rt_task"></div></div></div>`;
    const history=root.querySelector('[data-testid="robotics-task-history"]'),host=root.querySelector('#rt_task');
    const more=root.querySelector('[data-history-more]');
    function renderHistory(){
      history.innerHTML=view.rows.length?view.rows.map(row=>`<a class="it rta-record ${view.selected===(row.run_id||row.session_id)?'on':''}" href="${esc(route(row))}" ${row.kind==='generation'?`data-history-run="${esc(row.run_id)}"`:`data-history-session="${esc(row.session_id)}"`}>
        <span class="t"><strong>${esc(row.title)}</strong><span class="rta-record-asset">${esc(row.asset_name)} · 版本 ${esc(row.version_number)}</span><small>${esc(status(row.status))}${row.mode==='demo'?' · 演示':''} · ${esc(time(row.updated_at))}</small></span></a>`).join(''):
        `<p class="rta-empty">${view.query?'没有匹配的记录':'暂无生成记录，选择本体开始第一个任务。'}</p>`;
      root.querySelector('.rta-count').textContent=`已显示 ${view.rows.length} / ${view.total} 条`;
      more.hidden=view.rows.length>=view.total;
    }
    async function loadHistory(append=false){
      const sequence=++view.sequence,offset=append?view.rows.length:0;
      more.disabled=true;
      try{
        const data=await read('/api/robotics/tasks?limit=30&offset='+offset+'&q='+encodeURIComponent(view.query),abort.signal);
        if(active!==view||sequence!==view.sequence)return;
        view.rows=append?[...view.rows,...data.items]:data.items;view.total=data.total;renderHistory();
      }catch(e){if(active!==view||e.name==='AbortError'||sequence!==view.sequence)return;history.innerHTML=`<p role="alert" class="rt-error">历史加载失败：${esc(e.message)}，请点击刷新重试。</p>`;}
      finally{if(active===view&&sequence===view.sequence)more.disabled=false;}
    }
    view.changed=session=>{
      const key=[session.session_id,session.revision,session.status,session.run_id].join(':');
      view.selected=session.status==='generated'?session.run_id:session.session_id;
      if(view.lastState===key)return;view.lastState=key;loadHistory();
    };
    root.querySelector('.rta-toggle').onclick=e=>{const hidden=root.classList.toggle('rta-history-hidden');e.currentTarget.setAttribute('aria-expanded',String(!hidden));};
    root.querySelector('.rta-search').oninput=e=>{view.query=e.target.value;clearTimeout(view.timer);view.timer=setTimeout(()=>loadHistory(),180);};
    root.querySelector('[data-history-refresh]').onclick=()=>loadHistory();more.onclick=()=>loadHistory(true);
    loadHistory();
    const error=e=>{if(active===view&&e.name!=='AbortError')host.innerHTML=`<div class="rt-error" role="alert">${esc(e.message)}</div><a href="#robotics-task">返回行为树生成</a>`;};
    if(params.get('run')){
      host.innerHTML='<p class="rta-empty" role="status">正在读取历史生成结果…</p>';
      (async()=>{try{
        const sid=params.get('session'),rid=params.get('run');
        if(!sid)throw Error('历史结果缺少任务标识，请从记录列表重新打开。');
        const endpoint='/api/robotics/tasks/'+encodeURIComponent(sid)+'/generations/'+encodeURIComponent(rid);
        const saved=await read(endpoint,abort.signal);if(active!==view)return;
        if(saved.session_id!==sid||saved.run_id!==rid)throw Error('历史结果身份不匹配。');
        host.innerHTML=`<section data-testid="robotics-generation-history" data-run-id="${esc(saved.run_id)}"><header class="rt-heading"><div><span class="rt-eyebrow">生成结果 · 历史记录</span><h3>${esc(saved.title)}</h3><p>${esc(saved.asset_name)} · 版本 ${esc(saved.version_number)} · ${esc(time(saved.created_at))}</p></div><a class="ra-button" href="${esc(global.RoboticsTask.link(saved.ontology_id,saved.version_id,saved.mode))}">基于此本体新建任务</a></header>
          <p class="rt-notice">正在查看当时生成的结果。新任务不会覆盖这份行为树。</p><p class="rt-safety">离线候选 · 执行未验证${saved.mode==='demo'?' · 演示数据':''}</p>
          <section class="rt-result dq-card"><div class="rt-section-heading"><h3>行为树预览</h3><a class="dq-send rt-download" data-history-download href="${esc(endpoint+'/candidate')}">下载行为树 XML</a></div><div role="alert" data-history-error hidden></div><div class="rt-tree" data-testid="task-tree" data-run-id="${esc(saved.run_id)}"><ul>${global.RoboticsTask.renderTree(saved.tree)}</ul></div></section>
          <a class="ra-back" href="${esc(global.RoboticsAssets.versionLink(saved.ontology_id,saved.version_id))}">查看对应本体</a></section>`;
        host.querySelector('[data-history-download]').onclick=async e=>{e.preventDefault();const notice=host.querySelector('[data-history-error]');notice.hidden=true;try{
          const response=await global.fetch(endpoint+'/candidate',{signal:abort.signal});if(!response.ok){const body=await response.json();throw Error(body.error?.message||'下载失败');}
          const blob=await response.blob();if(active!==view)return;const url=URL.createObjectURL(blob),a=global.document.createElement('a');a.href=url;a.download='behavior-tree.xml';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        }catch(e){if(active===view&&e.name!=='AbortError'){notice.hidden=false;notice.className='rt-error';notice.textContent=e.message;}}};
      }catch(e){error(e);}})();return;
    }
    if(params.get('session')||params.get('asset')||params.get('version'))return;
    host.innerHTML='<p class="rta-empty" role="status">正在加载可用本体…</p>';
    (async()=>{try{
      const [catalog,cap]=await Promise.all([read('/api/ontology-assets?domain=robotics',abort.signal),read('/api/robotics/task-capabilities',abort.signal)]);
      if(active!==view)return;
      const items=catalog.items.filter(item=>item.domain==='robotics'&&item.version_id);
      host.innerHTML=`<div class="rt-welcome rta-welcome"><div class="rt-welcome-mark" aria-hidden="true">◇</div><h1>从一句任务，到行为树</h1><p>选择机器人本体，描述你的任务。<br>同一本体可以生成多份行为树，每次结果都能从左侧找回。</p></div>
        <section class="dq-inputbar rta-start"><label for="rta_asset">选择机器人本体</label><select id="rta_asset"><option value="">请选择本体</option>${items.map(item=>`<option value="${esc(item.ontology_id)}">${esc(item.name)} · 版本 ${esc(item.version_number)}</option>`).join('')}</select>
        <p class="rv-help">${!cap.enabled?'机器人功能未启用，请在配置中启用后创建任务。':!items.length?'暂无机器人本体，请先构建或导入。':'选择后将固定当前版本，后续任务可继续复用本体知识。'}</p><div class="rta-start-actions"><a href="#build">构建本体</a><button type="button" class="dq-send" data-task-start disabled>开始新任务</button></div></section>`;
      const select=host.querySelector('#rta_asset'),button=host.querySelector('[data-task-start]');
      select.onchange=()=>{button.disabled=!select.value||!cap.enabled;};
      button.onclick=()=>{const item=items.find(i=>i.ontology_id===select.value);if(item&&cap.enabled){const version=item.versions?.find(v=>v.version_id===item.version_id);global.location.hash=global.RoboticsTask.link(item.ontology_id,item.version_id,version?.metadata?.mode||version?.publication_mode||'real');}};
    }catch(e){error(e);}})();
  }
  global.RoboticsTaskApp={page,changed:session=>active?.changed?.(session)};
})(window);

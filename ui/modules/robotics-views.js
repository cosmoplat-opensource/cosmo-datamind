/* Fixed-version read-only relationship and behavior tree canvases — DR-068. */
(function (global) {
  'use strict';
  const escape = value => global.RoboticsAssets.escape(value);
  const link = (oid, vid, view='relations') => '#robotics-view?asset=' + encodeURIComponent(oid) + '&version=' + encodeURIComponent(vid) + '&view=' + encodeURIComponent(view);
  const colors = {business:'#4a5ff3',skills:'#7c3aed',other:'#0891b2'};
  let active = null, token = 0;
  function page(name) {
    const current = ++token;
    active?.dispose(); active = null;
    if (name !== 'robotics-view') return;
    const host = global.document.getElementById('rv_host');
    const params = new URLSearchParams(global.location.hash.split('?')[1] || '');
    const oid = params.get('asset'), vid = params.get('version'), view = params.get('view') || 'relations';
    host.innerHTML = '<p class="ra-empty" role="status">正在读取本体视图…</p>';
    let canvas = null, resize = null, data = null, visibleNodes = [], selectedTree = null;
    const treeHistory = [];
    const abort = new AbortController();
    const disposeCanvas = () => {resize?.disconnect();resize=null;canvas?.destroy();canvas=null;};
    active = {dispose:()=>{abort.abort();disposeCanvas();}};
    const fail = error => {if(current!==token)return;disposeCanvas();host.innerHTML=`<a class="ra-back" href="#library">← 返回本体目录</a><div class="rt-error" role="alert">${escape(error.message || error)}</div>`;};
    const fit = () => {
      if(!canvas)return;
      const nodes=canvas.getNodes().map(item=>item.getModel());
      if(!nodes.length||nodes.some(n=>!Number.isFinite(n.x)||!Number.isFinite(n.y)))return;
      const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y),left=Math.min(...xs),right=Math.max(...xs),top=Math.min(...ys),bottom=Math.max(...ys);
      const plot=host.querySelector('#rv_canvas');
      canvas.zoomTo(Math.max(.02,Math.min(1,(plot.clientWidth-64)/(right-left+172),(plot.clientHeight-64)/(bottom-top+44))));
      const center=canvas.getCanvasByPoint((left+right)/2,(top+bottom)/2);
      canvas.translate(plot.clientWidth/2-center.x,plot.clientHeight/2-center.y);
    };
    function selectNode(id) {
      const node = visibleNodes.find(n=>n.id===id);
      if(!node)return;
      host.querySelector('#rv_node').value=id;
      if(canvas){canvas.getNodes().forEach(item=>canvas.setItemState(item,'selected',item.getID()===id));canvas.zoomTo(Math.max(.7,canvas.getZoom()));canvas.focusItem(id);}
      const side = host.querySelector('#rv_properties');
      const props = view==='relations' ? node.properties : Object.entries(node.attributes).map(([name,value])=>({name,value}));
      side.innerHTML=`<h3>${escape(node.label)}</h3><p class="muted">${escape(view==='relations'?node.type_labels.join(' · '):node.tag)}</p>
        ${props.length?'<dl>'+props.map(p=>`<div><dt>${escape(p.name)}</dt><dd>${escape(p.value)}</dd></div>`).join('')+'</dl>':'<p class="rv-help">此节点没有额外参数。</p>'}
        ${view==='relations'?`<details class="rv-identity"><summary>对象标识</summary><code>${escape(node.id)}</code></details>`:''}`;
      if(view==='relations'){
        const related=data.edges.filter(e=>e.source===id||e.target===id);
        const nodes=new Map(data.nodes.map(n=>[n.id,n]));
        side.innerHTML+='<h4>关联关系</h4>'+(related.length?'<ul>'+related.map(e=>`<li>${escape(nodes.get(e.source)?.label)} → <b>${escape(e.label)}</b> → ${escape(nodes.get(e.target)?.label)}</li>`).join('')+'</ul>':'<p class="rv-help">没有实例间关系。</p>');
      }else if(node.tag==='SubTree' && data.trees.some(t=>t.name===node.attributes.ID)){
        const button=global.document.createElement('button');button.className='ghost';button.textContent='查看子树 '+node.attributes.ID;
        button.onclick=()=>{treeHistory.push({tree:selectedTree,node:id});host.querySelector('#rv_tree').value=node.attributes.ID;selectedTree=node.attributes.ID;draw();};side.append(button);
      }
    }
    function draw() {
      disposeCanvas();
      const back=host.querySelector('#rv_tree_back');
      if(back){back.hidden=!treeHistory.length&&selectedTree===data.main_tree;back.textContent=treeHistory.length?'返回上一级':'返回主流程';}
      const plot=host.querySelector('#rv_canvas'), side=host.querySelector('#rv_properties');
      plot.innerHTML='';side.innerHTML='<div class="rv-side-empty">选择图中的节点，查看对象关系或节点参数。<br>也可以使用上方的节点列表。</div>';
      let edges=[];
      if(view==='relations'){
        const group=host.querySelector('#rv_group').value;
        visibleNodes=data.nodes.filter(n=>group==='all'||n.group===group);
        const ids=new Set(visibleNodes.map(n=>n.id));edges=data.edges.filter(e=>ids.has(e.source)&&ids.has(e.target));
      } else {
        visibleNodes=[];
        const tree=data.trees.find(t=>t.name===selectedTree);
        let leaf=0;
        function walk(node,parent,depth=0){
          const name=node.attributes.name || node.attributes.ID;
          const projected={...node,label:node.tag+(name?' · '+name:''),group:node.tag==='SubTree'?'skills':'business',y:depth*110};
          visibleNodes.push(projected);
          if(parent)edges.push({source:parent,target:node.id,label:''});
          const positions=node.children.map(child=>walk(child,node.id,depth+1));
          projected.x=positions.length?(positions[0]+positions.at(-1))/2:leaf++*200;
          return projected.x;
        }
        if(tree)walk(tree.root,null);
      }
      host.querySelector('#rv_counts').textContent=`${visibleNodes.length} 个节点 · ${edges.length} 条连线`;
      const select=host.querySelector('#rv_node');
      select.innerHTML='<option value="">选择节点查看详情</option>'+visibleNodes.map((n,i)=>`<option value="${escape(n.id)}">${i+1}. ${escape(n.label)}</option>`).join('');
      host.querySelector('#rv_search_result').textContent='';
      if(!visibleNodes.length){plot.innerHTML='<div class="ra-empty">当前范围没有可展示的节点，请切换筛选范围。</div>';return;}
      if(!global.G6)throw Error('图形组件加载失败，请刷新页面重试。');
      canvas=new global.G6.Graph({container:plot,width:plot.clientWidth,height:plot.clientHeight,
        ...(view==='relations'?{layout:{type:'dagre',rankdir:'LR',nodesep:24,ranksep:95}}:{}),
        modes:{default:['drag-canvas','zoom-canvas','drag-node']},animate:false,fitView:false,minZoom:.02,maxZoom:3,
        defaultNode:{type:'rect',size:[172,44],anchorPoints:view==='relations'?[[0,.5],[1,.5]]:[[.5,0],[.5,1]],style:{radius:9,lineWidth:1.3},labelCfg:{style:{fontSize:12,fill:'#243247'}}},
        nodeStateStyles:{selected:{lineWidth:3,shadowColor:'#4a5ff3',shadowBlur:8}},
        defaultEdge:{type:'polyline',style:{stroke:'#b6bfd7',radius:10,offset:20,endArrow:{path:global.G6.Arrow.triangle(6,7,0),fill:'#b6bfd7'}},labelCfg:{style:{fontSize:10,fill:'#64748b',background:{fill:'#fff',padding:[3,4]}}}}});
      // DR-068: G6 canvas dirty-region refresh can retain stale pixels after group
      // transforms. Full redraw keeps zoom/pan and selected-node focus in sync.
      canvas.get('canvas').set('localRefresh',false);
      const drawing=canvas;
      const fitAfterPaint=()=>global.requestAnimationFrame(()=>{if(canvas===drawing)fit();});
      canvas.on('afterlayout',fitAfterPaint);
      canvas.on('node:click',event=>selectNode(event.item.getID()));
      canvas.data({nodes:visibleNodes.map(n=>({id:n.id,...(view==='behavior-tree'?{x:n.x,y:n.y}:{}),label:n.label.length>22?n.label.slice(0,21)+'…':n.label,style:{fill:n.group==='skills'?'#f5efff':n.group==='other'?'#ecfafb':'#eef2ff',stroke:colors[n.group]}})),edges:edges.map(e=>({...e,sourceAnchor:1,targetAnchor:0}))});
      canvas.render();fitAfterPaint();
      resize=new ResizeObserver(()=>{if(canvas&&plot.clientWidth&&(canvas.getWidth()!==plot.clientWidth||canvas.getHeight()!==plot.clientHeight)){canvas.changeSize(plot.clientWidth,plot.clientHeight);}});resize.observe(plot);
    }
    function render() {
      const tree=view==='behavior-tree';selectedTree=tree?data.main_tree:null;
      host.innerHTML=`<a class="ra-back" href="${escape(global.RoboticsAssets.versionLink(oid,vid))}">← 返回本体详情</a>
        <header class="rt-heading"><div><span class="rt-eyebrow">机器人本体 · ${tree?'行为树视图':'关系图'}</span><h2>${escape(data.name)}</h2><p>当前：版本 ${escape(data.version_number)} · 只读浏览</p></div><a class="ra-button" href="${escape(link(oid,vid,tree?'relations':'behavior-tree'))}">${tree?'打开本体关系图':'打开行为树视图'} →</a></header>
        <p class="rv-help">${tree?'本体流程 · 非任务生成结果。显示本体保存的流程与参数绑定；具体任务的参数以生成结果为准。':'展示本体中的命名对象及其关系。筛选可聚焦业务对象或技能流程；OWL 类定义、匿名约束及审计记录不在此图中。'}</p>
        <section class="card rv-toolbar">${tree?`<label>行为树<select id="rv_tree" aria-label="行为树">${data.trees.map(t=>`<option value="${escape(t.name)}">${escape(t.name)}${t.name===data.main_tree?' · 主流程':' · 子树'}</option>`).join('')}</select></label>`:'<label>展示范围<select id="rv_group" aria-label="展示范围"><option value="business">业务对象与配置</option><option value="skills">技能与流程</option><option value="all">全部对象</option></select></label>'}
        <form id="rv_search"><label>查找节点<input id="rv_query" placeholder="输入节点名称" autocomplete="off"></label><button class="ghost" type="submit">查找</button></form><span id="rv_search_result" role="status"></span>
        ${tree?'<button type="button" class="ghost" id="rv_tree_back" hidden>返回上一级</button>':''}<div class="rv-controls"><button type="button" data-zoom="in" aria-label="放大">＋</button><button type="button" data-zoom="out" aria-label="缩小">−</button><button type="button" id="rv_fit">适应画布</button></div></section>
        <div class="rv-workspace"><section class="card rv-graph"><div class="ra-section-heading"><h3>${tree?'流程结构':'对象关联'}</h3><span id="rv_counts"></span></div><div id="rv_canvas" role="img" aria-label="${tree?'行为树流程图':'机器人本体关系图'}"></div><p class="rv-legend">${tree?'从上到下执行，同级节点从左到右排列；点击 SubTree 节点可查看子树。':'蓝色：业务对象与配置　紫色：技能与流程　青色：其他对象'} · 拖动画布移动，滚轮缩放</p></section>
        <aside class="card rv-sidebar"><label for="rv_node">节点详情</label><select id="rv_node"></select><div id="rv_properties"></div></aside></div>`;
      if(tree){
        host.querySelector('#rv_tree').value=selectedTree;
        host.querySelector('#rv_tree').onchange=e=>{treeHistory.length=0;selectedTree=e.target.value;draw();};
        host.querySelector('#rv_tree_back').onclick=()=>{
          const parent=treeHistory.pop();selectedTree=parent?.tree||data.main_tree;
          host.querySelector('#rv_tree').value=selectedTree;draw();if(parent)selectNode(parent.node);
        };
      }
      else {host.querySelector('#rv_group').value=data.nodes.some(n=>n.group==='business')?'business':'all';host.querySelector('#rv_group').onchange=draw;}
      host.querySelector('#rv_node').onchange=e=>selectNode(e.target.value);
      host.querySelector('#rv_fit').onclick=fit;
      host.querySelectorAll('[data-zoom]').forEach(button=>button.onclick=()=>{if(canvas)canvas.zoomTo(Math.max(.02,Math.min(3,canvas.getZoom()*(button.dataset.zoom==='in'?1.25:.8))),{x:canvas.getWidth()/2,y:canvas.getHeight()/2});});
      let searchIndex=0,lastQuery='';
      host.querySelector('#rv_search').onsubmit=e=>{e.preventDefault();const q=host.querySelector('#rv_query').value.trim().toLocaleLowerCase();const matches=q?visibleNodes.filter(n=>n.label.toLocaleLowerCase().includes(q)):[];
        if(q!==lastQuery){searchIndex=0;lastQuery=q;}const index=matches.length?searchIndex++%matches.length:0;
        host.querySelector('#rv_search_result').textContent=matches.length?`找到 ${matches.length} 项 · ${index+1}/${matches.length}`:'当前范围未找到匹配节点';if(matches.length)selectNode(matches[index].id);};
      draw();
    }
    (async()=>{
      try {
        if(!oid||!vid||!['relations','behavior-tree'].includes(view))throw Error('缺少本体、版本或有效视图，请从本体库重新打开。');
        const response=await global.fetch('/api/ontology-assets/'+encodeURIComponent(oid)+'/versions/'+encodeURIComponent(vid)+'/views/'+view,{signal:abort.signal});
        const body=await response.json();if(current!==token)return;
        if(!response.ok)throw Error(body.error?.message||`读取失败（HTTP ${response.status}）`);
        if(body.ontology_id!==oid||body.version_id!==vid||body.view!==view)throw Error('返回的本体版本不匹配。');
        // DR-068: share task-page aliases for historical names. Only display
        // labels change; fixed OWL bytes, identifiers and parameter values stay intact.
        const displayLabel=global.RoboticsTask.displayLabel;
        data=view==='relations'?{...body,nodes:body.nodes.map(node=>({...node,
          label:displayLabel(node.label),properties:node.properties.map(property=>
            property.predicate==='http://www.w3.org/2000/01/rdf-schema#label'
              ? {...property,value:displayLabel(property.value)} : property)
        }))}:body;
        render();
      }catch(error){if(error.name!=='AbortError')fail(error);}
    })();
  }
  global.RoboticsViews={page,link};
})(window);

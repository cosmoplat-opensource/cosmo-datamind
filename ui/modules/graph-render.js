/* 本体图谱渲染(G6):节点/连线样式、布局参数、初始视图与孤立对象排布。
 * 由 index.html 主脚本中的 drawGraph 等调用;依赖主脚本里的 $、GKIND、GEST(调用时已定义)。 */
function _g6build(g,hl){
 // 节点多时连线标签默认隐藏、悬停显示:几十条边汇到同一主表时,标签互相叠压、被连线穿过
 const quietLabels=g.nodes.length>40;
 const nodes=g.nodes.map(n=>{const kk=GKIND[n.kind]||GKIND.object;
  const on=!hl||hl.set.has(n.id);
  const hit=hl&&(n.tables||[]).some(t=>hl.used.has(String(t).toLowerCase()));
  const _lb=String(n.name||n.id), _cjk=/[\u4e00-\u9fa5]/.test(_lb), _cap=_cjk?14:24;
  const label=_lb.length>_cap?_lb.slice(0,_cap-1)+'…':_lb;
  // 框宽随文字:13px 粗体下一个汉字约 14px、西文约 8px;固定 132px 时长名称会溢出框外
  const tw=[...label].reduce((w,ch)=>w+(/[\u2e80-\uffff]/.test(ch)?14:8),0);
  return {id:n.id,label,kind:n.kind,_n:n,size:[Math.max(132,Math.min(240,tw+28)),42],
  style:{fill:on?kk.fill:'#F6F7FA',stroke:on?kk.stroke:'#DDE1EA',lineWidth:hit?3:(on?2:1),radius:8},
  labelCfg:{style:{fill:on?kk.text:'#A9B0BF',fontSize:13,fontWeight:600}}};});
 const edges=g.edges.map(e=>{let st=GEST[e.status]||GEST.gap;if(e.semantic==='fail')st={c:'#dc2626',w:st.w,d:[2,4]};
  if(hl&&!(hl.set.has(e.s)&&hl.set.has(e.t)))st={c:'#E3E6ED',w:1,d:st.d};
  return {source:e.s,target:e.t,label:e.verb||'',_e:e,
  style:{stroke:st.c,lineWidth:st.w,lineDash:st.d,endArrow:{path:G6.Arrow.triangle(7,8,0),fill:st.c}},
  labelCfg:{autoRotate:true,style:{fill:(hl&&!(hl.set.has(e.s)&&hl.set.has(e.t)))?'#C7CBD6':(e.status==='verified'?'#64748b':'#d46b08'),fontSize:12,opacity:quietLabels?0:1,background:{fill:'#fff',padding:[2,4,2,4],radius:3,opacity:quietLabels?0:1}}}};});
 return {nodes,edges};
}
function _g6layout(lay,n){const big=n>40;
 return lay==='dagre'?{type:'dagre',rankdir:'LR',align:'UL',nodesep:big?14:26,ranksep:big?70:52,controlPoints:true}
  :lay==='force'?{type:'force',preventOverlap:true,linkDistance:130,nodeStrength:-70,edgeStrength:.6}
  :lay==='circular'?{type:'circular'}:{type:'radial',unitRadius:130,preventOverlap:true,nodeSize:120};}
function g6render(containerId,g,lay,opts){opts=opts||{};
 const c=$('#'+containerId);const {nodes,edges}=_g6build(g,opts.hl);
 const degrees={};for(const e of edges){degrees[e.source]=(degrees[e.source]||0)+1;degrees[e.target]=(degrees[e.target]||0)+1;}
 // 分层布局会把没有关系的对象都排进同一层,几十个孤立对象叠成一根竖条,并在连通部分之间留下大段空白。
 // 孤立对象不参与分层布局,布局完成后按网格排在连通部分下方;此时关闭初始动画,免得动画把网格位置覆盖掉
 const iso=nodes.filter(n=>!degrees[n.id]);
 const gridIso=lay==='dagre'&&iso.length>=8&&iso.length<nodes.length;
 const graph=new G6.Graph({container:containerId,width:c.offsetWidth,height:c.offsetHeight||480,layout:_g6layout(lay,nodes.length),
  defaultNode:{type:'rect',size:[132,42],style:{radius:8},anchorPoints:[[0,0.5],[1,0.5]]},
 defaultEdge:{type:lay==='dagre'?'polyline':'quadratic',style:{radius:12,offset:18}},
  modes:{default:['drag-canvas','zoom-canvas','drag-node']},fitView:false,animate:!gridIso});
 graph.__focusId=nodes.reduce((best,n)=>(degrees[n.id]||0)>(degrees[best]||0)?n.id:best,nodes[0]&&nodes[0].id);
 let isoAdded=false;
 const addIsolated=()=>{if(!gridIso||isoAdded)return;isoAdded=true;
  const placed=graph.getNodes().map(x=>x.getModel()).filter(m=>Number.isFinite(m.x));
  if(!placed.length)return;
  const left=Math.min(...placed.map(m=>m.x-(m.size?m.size[0]:132)/2)), right=Math.max(...placed.map(m=>m.x+(m.size?m.size[0]:132)/2));
  const y0=Math.max(...placed.map(m=>m.y))+100, colW=Math.max(...iso.map(n=>n.size[0]))+20;
  const cols=Math.max(4,Math.floor((right-left)/colW));
  iso.forEach((n,i)=>graph.addItem('node',{...n,x:left+colW/2+(i%cols)*colW,y:y0+Math.floor(i/cols)*60}));};
 // 关键：在布局完成后再自适应，并夹住过放大(此前 fitView 在异步布局前跑→内容坍缩成小框→放大到 ~7x)。
 // 聚焦不用动画:动画未完时 320 ms 后的第二次 fit 会打断它,视图停在空白处
 const fit=()=>{try{graph.fitView([24,48,24,48]);const z=graph.getZoom();
  if(z>1){graph.zoomTo(1);graph.fitCenter();}
  else if(nodes.length>40&&z<.85){graph.zoomTo(.85);const item=graph.findById(graph.__focusId);
   if(item)graph.focusItem(item,false);else graph.fitCenter();}
 }catch(_){}};
 graph.on('afterlayout',()=>{addIsolated();fit();});
 graph.data({nodes:gridIso?nodes.filter(n=>degrees[n.id]):nodes,edges});graph.render();
 fit();setTimeout(fit,320);
 if(nodes.length>40){const show=(e,v)=>{try{graph.updateItem(e.item,{labelCfg:{style:{opacity:v,background:{fill:'#fff',padding:[2,4,2,4],radius:3,opacity:v}}}});}catch(_){}};
  graph.on('edge:mouseenter',e=>show(e,1));graph.on('edge:mouseleave',e=>show(e,0));}
 if(opts.onNode)graph.on('node:click',e=>opts.onNode(e.item.getModel()._n));
 if(opts.onEdge)graph.on('edge:click',e=>opts.onEdge(e.item.getModel()._e));
 if(typeof ResizeObserver==='function'){let width=c.clientWidth,height=c.clientHeight;
  const observer=new ResizeObserver(()=>{if(graph.get('destroyed')||!c.isConnected){observer.disconnect();return;}
   if(c.clientWidth>0&&c.clientHeight>0&&(c.clientWidth!==width||c.clientHeight!==height)){
    width=c.clientWidth;height=c.clientHeight;graph.changeSize(width,height);fit();}});
  observer.observe(c);graph.on('beforedestroy',()=>observer.disconnect());}
 return graph;
}

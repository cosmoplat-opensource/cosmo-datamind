/* Studio-style graph workspace and read-only details — DR-159 / IR-031 T05. */
import {StudioController} from './controller.js';
import {label} from './model.js';
export function mountPanel(host,{canvasId=null}={}){
  host.className='studio-panel';host.dataset.testid='studio-panel';
  host.innerHTML=`<div class="studio-title"><strong>本体工作台</strong><span class="studio-context"></span></div>
    <div class="studio-toolbar"><form><input aria-label="查找节点" placeholder="搜索类别、关系、实例"><button>查找</button></form>
    <button type="button" data-op="fit">适应画布</button><button type="button" data-op="pause">暂停布局</button>
    <button type="button" data-op="in" aria-label="放大">＋</button><button type="button" data-op="out" aria-label="缩小">−</button>
    <button type="button" data-op="types">显示属性类型</button><label>展示范围<select aria-label="展示范围"><option value="all">全部对象</option><option value="business">业务对象与配置</option><option value="skills">技能与流程</option></select></label></div>
    <p class="studio-notice" role="status"></p><div class="studio-workspace"><div class="studio-plot"><svg aria-label="本体类别与实例图谱"></svg>
    <div class="studio-legend">蓝色圆圈：类别 · 橙色方框：实例 · 绿色：数据值<br>双击类别展开实例，拖动节点联动布局</div></div>
    <aside class="studio-details"><label>节点详情<select aria-label="节点详情"><option value="">选择对象查看详情</option></select></label><div class="studio-entity">点击类别或连线，查看定义、属性和来源。</div></aside></div>
    <p class="studio-counts"></p>`;
  const q=s=>host.querySelector(s),svg=q('svg'),plot=q('.studio-plot'),details=q('.studio-entity'),notice=q('.studio-notice'),nodeSelect=q('select[aria-label="节点详情"]');
  if(canvasId){plot.id=canvasId;nodeSelect.id='rv_node';details.id='rv_properties';}
  let dead=false,scope=null,epoch=0,current=null,last=null,selected=null;
  function text(parent,tag,value){const e=document.createElement(tag);e.textContent=value;parent.append(e);return e;}
  function button(parent,name,fn){const b=text(parent,'button',name);b.type='button';b.onclick=fn;return b;}
  const controller=new StudioController(svg,{onSelect:(_id,entity)=>show(entity),onNotice:message=>{notice.textContent=message;},onChange:()=>{counts();if(selected)show(controller.state.entity(selected));}});
  controller.filters={datatypes:false};host.studioController=controller;
  function counts(){
    if(!current)return;
    const visible=controller.graph.data;
    q('.studio-counts').textContent=`${current.nodes.filter(n=>n.type==='class').length} 个类别 · ${current.properties.length} 个关系与属性 · 当前显示 ${visible.nodes.length} 个节点、${visible.edges.length} 条连线 · ${current.individuals.length} 个显式实例`;
    for(const [id,dom] of controller.graph.nodeElements){
      const change=controller.changes.added.includes(id)?'added':controller.changes.modified.includes(id)?'modified':'';
      dom.setAttribute('data-change',change);
    }
  }
  function show(entity){
    details.replaceChildren();selected=entity?.id||null;nodeSelect.value=selected||'';
    if(!entity){text(details,'p','点击类别或连线查看详情。');return;}
    text(details,'h3',label(entity));
    text(details,'p',({class:'类别',individual:'实例',property:'属性定义',expression:'类别表达式',literal:'数据值'})[entity.type]||'关系');
    if(entity.definition)text(details,'p',entity.definition);
    if(entity.predicate){const prop=controller.state.properties.get(entity.predicate);text(details,'p',`${label(controller.state.entity(entity.source))} → ${label(prop||entity.predicate)} → ${label(controller.state.entity(entity.target))}`);}
    const id=text(details,'details','');text(id,'summary','对象标识');text(id,'code',entity.iri||entity.id);
    if(entity.type==='class'){
      button(details,'仅显示此类关联',()=>{controller.state.focusClass(entity.id);controller.draw();counts();});
      if(controller.state.classFocusId)button(details,'显示完整图谱',()=>{controller.state.clearClassFocus();controller.draw();counts();});
      const total=entity.individuals?.length||0,offset=controller.pages[entity.id]||0,shown=Math.min(controller.expanded[entity.id]||0,total-offset);
      text(details,'p',`显式实例：已显示 ${shown} / ${total}${shown?` · 范围 ${offset+1}～${offset+shown}`:''}`);
      button(details,shown?'收起实例':'展开实例',()=>{controller.toggleClass(entity.id);show(entity);});
      if(shown&&offset)button(details,'上一页实例',()=>{controller.page(entity.id,-1);show(entity);});
      if(shown&&offset+shown<total)button(details,'下一页实例',()=>{controller.page(entity.id);show(entity);});
      if(offset+shown<total&&shown)button(details,'继续显示实例',()=>{controller.loadMore(entity.id);show(entity);});
      const props=current.properties.filter(p=>p.domains.includes(entity.id));
      if(props.length){text(details,'h4','关系与数据属性');for(const prop of props)text(details,'p',`${label(prop)} → ${prop.ranges.map(id=>label(controller.state.entity(id)||id)).join('、')}`);}
    }
    if(entity.type==='individual'){
      button(details,'查看实例关系',()=>{controller.state.toggleRelations();controller.draw();counts();});
      button(details,'展开数据值',()=>{controller.state.toggleData();controller.draw();counts();});
      const dl=document.createElement('dl');details.append(dl);
      for(const value of entity.data||[]){text(dl,'dt',label(controller.state.properties.get(value.predicate)||value.predicate));text(dl,'dd',String(value.value));}
      const ul=document.createElement('ul');details.append(ul);
      for(const edge of current.relations.filter(e=>e.source===entity.id||e.target===entity.id))text(ul,'li',`${label(controller.state.entity(edge.source))} → ${label(controller.state.properties.get(edge.predicate)||edge.predicate)} → ${label(controller.state.entity(edge.target))}`);
    }
    if(controller.graph.positions.has(entity.id))button(details,controller.graph.positions.get(entity.id).pinned?'释放节点':'固定节点',()=>{controller.pin(entity.id,!controller.graph.positions.get(entity.id).pinned);show(entity);});
  }
  function setOptions(){
    const group=q('select[aria-label="展示范围"]').value;
    const skill=iri=>/\b(?:AtomicSkill|CompositeSkill|SkillInvocation|SkillImplementation|TaskProcedure)$/.test(iri.split('#').pop());
    const items=[...current.nodes,...current.individuals].filter(n=>group==='all'||(group==='skills'?(n.types||[]).some(skill):!(n.types||[]).some(skill)));
    nodeSelect.replaceChildren(new Option('选择对象查看详情',''));
    for(const item of items)nodeSelect.add(new Option(label(item),item.id));
  }
  nodeSelect.onchange=()=>{
    const id=nodeSelect.value;
    if(controller.state.individuals.has(id)){
      const entity=controller.state.individuals.get(id);
      const owner=entity.types.find(t=>controller.state.nodes.has(t));
      if(owner&&!controller.expanded[owner])controller.toggleClass(owner);
    }
    if(!controller.graph.data.nodes.length&&controller.state.nodes.get(id)?.type==='class')controller.state.focusClass(id);
    controller.select(id);controller.graph.center(id);
  };
  q('select[aria-label="展示范围"]').onchange=setOptions;
  q('form').onsubmit=e=>{e.preventDefault();if(!current)return;const term=q('input').value.toLocaleLowerCase();const found=[...current.nodes,...current.individuals,...current.properties].find(n=>label(n).toLocaleLowerCase().includes(term));
    if(found){nodeSelect.value=found.id;nodeSelect.onchange();}else notice.textContent='未找到匹配对象。';};
  host.querySelectorAll('[data-op]').forEach(b=>{b.onclick=()=>{
    const op=b.dataset.op;
    if(op==='types'){controller.filters.datatypes=!controller.filters.datatypes;controller.draw();counts();b.textContent=controller.filters.datatypes?'隐藏属性类型':'显示属性类型';}
    else if(op==='fit')controller.graph.fit();
    else if(op==='pause'){controller.pause(!controller.graph.paused);b.textContent=controller.graph.paused?'继续布局':'暂停布局';}
    else controller.graph.zoomBy(op==='in'?1.25:.8);
  };});
  return {controller,present(document,{scope:nextScope=JSON.stringify([document.context.kind,document.context.key])}={}){
    if(dead)return;const fingerprint=JSON.stringify(document);
    if(nextScope!==scope){scope=nextScope;epoch=controller.beginContext(document.context);selected=null;show(null);last=null;}
    host.dataset.stage=document.context.stage;host.dataset.kind=document.context.kind;host.dataset.revision=String(document.context.revision);
    if(fingerprint===last)return;
    if(controller.update(document,{epoch})){
      current=document;last=fingerprint;setOptions();counts();notice.textContent=document.nodes.length&&!controller.graph.data.nodes.length?'当前图谱超出同屏范围，请从节点详情选择类别查看局部关联。':document.warnings.join(' ');
      q('.studio-context').textContent=document.context.status==='historical'?`历史修订 ${document.context.revision} · 只读`:`${document.context.kind==='design'?'设计修订 '+document.context.revision:'固定版本'} · 只读浏览`;
    }
  },error(message){notice.textContent=message;},destroy(){dead=true;controller.destroy();delete host.studioController;host.replaceChildren();delete host.dataset.testid;}};
}

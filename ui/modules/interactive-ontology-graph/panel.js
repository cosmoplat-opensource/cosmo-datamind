/* Interactive ontology graph workspace and read-only details — DR-166 / IR-031 T05. */
import {InteractiveOntologyGraphController} from './controller.js';
import {mountEditor} from './editor.js';
import {label} from './model.js';
export function mountPanel(host,{canvasId=null,embedded=false,editRequest=null,onEditMode=null,openSaved=null}={}){
  host.className='interactive-ontology-graph-panel';host.dataset.testid='interactive-ontology-graph-panel';
  host.innerHTML=`${embedded?'':'<div class="interactive-ontology-graph-title"><strong>机器人交互式本体图谱</strong><span class="interactive-ontology-graph-context"></span></div>'}
    <div class="interactive-ontology-graph-toolbar"><form><input aria-label="查找节点" placeholder="搜索类别、关系、实例"><button>查找</button></form>
    <button type="button" data-op="readable">可读视图</button><button type="button" data-op="fit">适应画布</button><button type="button" data-op="pause">暂停布局</button>
    <button type="button" data-op="in" aria-label="放大">＋</button><button type="button" data-op="out" aria-label="缩小">−</button>
    <button type="button" data-op="types">显示属性类型</button><label>展示范围<select aria-label="展示范围"><option value="all">全部对象</option><option value="business">业务对象与配置</option><option value="skills">技能与流程</option></select></label></div>
    <p class="interactive-ontology-graph-notice" role="status"></p><div class="interactive-ontology-graph-workspace"><div class="interactive-ontology-graph-plot"><svg aria-label="机器人交互式本体图谱"></svg>
    <div class="interactive-ontology-graph-legend">蓝色圆圈：类别 · 橙色方框：实例 · 绿色：数据值<br>双击类别展开实例，拖动节点联动布局；拖动画布查看其它节点</div></div>
    <aside class="interactive-ontology-graph-details"><label>节点详情<select aria-label="节点详情"><option value="">选择对象查看详情</option></select></label><div class="interactive-ontology-graph-entity">点击类别或连线，查看定义、属性和来源。</div></aside></div>
    <p class="interactive-ontology-graph-counts"></p>`;
  const q=s=>host.querySelector(s),svg=q('svg'),plot=q('.interactive-ontology-graph-plot'),details=q('.interactive-ontology-graph-entity'),notice=q('.interactive-ontology-graph-notice'),nodeSelect=q('select[aria-label="节点详情"]');
  if(canvasId){plot.id=canvasId;nodeSelect.id='rv_node';details.id='rv_properties';}
  let dead=false,scope=null,epoch=0,current=null,last=null,selected=null,editor=null;
  function text(parent,tag,value){const e=document.createElement(tag);e.textContent=value;parent.append(e);return e;}
  function button(parent,name,fn){const b=text(parent,'button',name);b.type='button';b.onclick=fn;return b;}
  const controller=new InteractiveOntologyGraphController(svg,{onSelect:(_id,entity)=>show(entity),onNotice:message=>{notice.textContent=message;},onChange:()=>{counts();if(selected)show(controller.state.entity(selected));}});
  controller.filters={datatypes:false};controller.graph.options.readableMinimum=.9;host.interactiveGraphController=controller;
  function counts(){
    if(!current)return;
    const visible=controller.graph.data;
    q('.interactive-ontology-graph-counts').textContent=`${current.nodes.filter(n=>n.type==='class').length} 个类别 · ${current.properties.length} 个关系与属性 · 当前显示 ${visible.nodes.length} 个节点、${visible.edges.length} 条连线 · ${current.individuals.length} 个显式实例`;
    for(const [id,dom] of controller.graph.nodeElements){
      const change=controller.changes.added.includes(id)?'added':controller.changes.modified.includes(id)?'modified':'';
      dom.setAttribute('data-change',change);
    }
  }
  function setClassScope(id){
    const previous=controller.state.captureView();
    if(id)controller.state.focusClass(id);
    else controller.state.clearClassFocus();
    if(!controller.draw()){
      controller.state.restoreView(previous);
      nodeSelect.value=selected||'';
      return false;
    }
    notice.textContent=current.warnings.join(' ');
    counts();
    show(controller.state.entity(controller.state.selectedId));
    return true;
  }
  function show(entity){
    details.replaceChildren();selected=entity?.id||null;nodeSelect.value=selected||'';
    if(!entity){text(details,'p','点击类别或连线查看详情。');return;}
    text(details,'h3',label(entity));
    text(details,'p',({class:'类别',individual:'实例',property:'属性定义',expression:'类别表达式',literal:'数据值'})[entity.type]||'关系');
    if(entity.definition)text(details,'p',entity.definition);
    if(entity.predicate){const prop=controller.state.properties.get(entity.predicate);text(details,'p',`${label(controller.state.entity(entity.source))} → ${label(prop||entity.predicate)} → ${label(controller.state.entity(entity.target))}`);}
    const id=text(details,'details','');text(id,'summary','对象标识');
    if(entity.design_classes?.length)text(id,'p','应用类别');
    text(id,'code',entity.iri||entity.id);
    if(entity.design_classes?.length){
      text(id,'p','对应的设计类别');
      for(const design of entity.design_classes)text(id,'code',design.iri);
    }
    if(entity.type==='class'){
      button(details,'仅显示此类关联',()=>setClassScope(entity.id));
      if(controller.state.classFocusId)button(details,'显示完整图谱',()=>setClassScope(null));
      const total=entity.individuals?.length||0,offset=controller.pages[entity.id]||0,shown=Math.min(controller.expanded[entity.id]||0,total-offset);
      text(details,'p',`显式实例：已显示 ${shown} / ${total}${shown?` · 范围 ${offset+1}～${offset+shown}`:''}`);
      button(details,shown?'收起实例':'展开实例',()=>{controller.toggleClass(entity.id);show(entity);});
      if(shown&&offset)button(details,'上一页实例',()=>{controller.page(entity.id,-1);show(entity);});
      if(shown&&offset+shown<total)button(details,'下一页实例',()=>{controller.page(entity.id);show(entity);});
      if(offset+shown<total&&shown)button(details,'继续显示实例',()=>{controller.loadMore(entity.id);show(entity);});
      const props=current.properties.filter(p=>p.domains.includes(entity.id));
      if(props.length){text(details,'h4','关系与数据属性');for(const prop of props)text(details,'p',`${label(prop)} → ${prop.ranges.map(id=>label(controller.state.entity(id)||id)).join('、')}`);}
    }
    if(controller.state.properties.has(entity.id)){
      text(details,'p',`作用对象：${entity.domains.map(id=>label(controller.state.entity(id)||id)).join('、')||'未声明'}`);
      text(details,'p',`目标或类型：${entity.ranges.map(id=>label(controller.state.entity(id)||id)).join('、')||'未声明'}`);
    }
    if(entity.type==='individual'){
      button(details,'查看实例关系',()=>{controller.state.toggleRelations();controller.draw();counts();});
      button(details,'展开数据值',()=>{controller.state.toggleData();controller.draw();counts();});
      const dl=document.createElement('dl');details.append(dl);
      for(const value of entity.data||[]){text(dl,'dt',label(controller.state.properties.get(value.predicate)||value.predicate));const row=text(dl,'dd',String(value.value));editor?.value(row,entity.id,value);}
      const ul=document.createElement('ul');details.append(ul);
      for(const edge of current.relations.filter(e=>e.source===entity.id||e.target===entity.id)){const row=text(ul,'li',`${label(controller.state.entity(edge.source))} → ${label(controller.state.properties.get(edge.predicate)||edge.predicate)} → ${label(controller.state.entity(edge.target))}`);editor?.edge(row,edge);}
    }
    editor?.details(details,entity);
    if(controller.graph.positions.has(entity.id))button(details,controller.graph.positions.get(entity.id).pinned?'释放节点':'固定节点',()=>{controller.pin(entity.id,!controller.graph.positions.get(entity.id).pinned);show(entity);});
  }
  function setOptions(){
    const group=q('select[aria-label="展示范围"]').value;
    const skill=iri=>/\b(?:AtomicSkill|CompositeSkill|SkillInvocation|SkillImplementation|TaskProcedure)$/.test(iri.split('#').pop());
    const items=[...current.nodes,...current.individuals,...current.properties].filter(n=>group==='all'||(group==='skills'?(n.types||[]).some(skill):!(n.types||[]).some(skill)));
    nodeSelect.replaceChildren(new Option('选择对象查看详情',''));
    const unclassified=document.createElement('optgroup');
    unclassified.label='未分类实例';
    for(const item of items){
      const option=new Option(label(item),item.id);
      if(current.context.kind==='edit-copy' && item.type==='individual' && !item.types.length)unclassified.append(option);
      else nodeSelect.add(option);
    }
    if(unclassified.childElementCount)nodeSelect.append(unclassified);
  }
  function chooseObject(id,fromSearch=false){
    const grouped=current.nodes.find(n=>n.design_classes?.some(c=>c.id===id));
    if(grouped)id=grouped.id;
    if(controller.overviewLimited && controller.state.nodes.get(id)?.type==='class'){
      if(setClassScope(id))controller.graph.center(id);
      return;
    }
    if(fromSearch)controller.state.returnToClasses();
    if(controller.state.individuals.has(id)){
      const entity=controller.state.individuals.get(id);
      const owner=entity.types.find(t=>controller.state.nodes.has(t));
      if(owner&&!controller.expanded[owner])controller.toggleClass(owner);
    }
    controller.select(id);controller.graph.center(id);counts();
  }
  nodeSelect.onchange=()=>chooseObject(nodeSelect.value);
  q('select[aria-label="展示范围"]').onchange=setOptions;
  q('form').onsubmit=e=>{e.preventDefault();if(!current)return;const term=q('input').value.toLocaleLowerCase();const found=[...current.nodes,...current.individuals,...current.properties].find(n=>label(n).toLocaleLowerCase().includes(term));
    if(found){q('select[aria-label="展示范围"]').value='all';setOptions();chooseObject(found.id,true);}else notice.textContent='未找到匹配对象。';};
  host.querySelectorAll('[data-op]').forEach(b=>{b.onclick=()=>{
    const op=b.dataset.op;
    if(op==='types'){controller.filters.datatypes=!controller.filters.datatypes;controller.draw();counts();b.textContent=controller.filters.datatypes?'隐藏属性类型':'显示属性类型';}
    else if(op==='fit'){controller.graph.options.readableMinimum=0;controller.graph.fit();}
    else if(op==='readable'){controller.graph.options.readableMinimum=.9;controller.graph.fit();}
    else if(op==='pause'){controller.pause(!controller.graph.paused);b.textContent=controller.graph.paused?'继续布局':'暂停布局';}
    else controller.graph.zoomBy(op==='in'?1.25:.8);
  };});
  function present(document,{scope:nextScope=JSON.stringify([document.context.kind,document.context.key])}={}){
    if(dead)return;const fingerprint=JSON.stringify(document);
    if(nextScope!==scope){scope=nextScope;epoch=controller.beginContext(document.context);controller.graph.options.readableMinimum=.9;selected=null;show(null);last=null;}
    if(fingerprint===last)return true;
    if(controller.update(document,{epoch})){
      host.dataset.stage=document.context.stage;host.dataset.kind=document.context.kind;host.dataset.revision=String(document.context.revision);
      current=document;last=fingerprint;setOptions();counts();show(controller.state.entity(controller.state.selectedId));
      if(document.context.kind==='version')editor?.source(document);
      notice.textContent=controller.overviewLimited && !controller.graph.data.nodes.length
        ? '完整图谱超出同屏范围，请选择或搜索类别查看局部关联。' : document.warnings.join(' ');
      const caption=q('.interactive-ontology-graph-context');
      if(caption)caption.textContent=document.context.kind==='edit-copy'?'编辑副本':document.context.status==='historical'?`历史修订 ${document.context.revision} · 只读`:`${document.context.kind==='design'?'设计修订 '+document.context.revision:'固定版本 · '+({candidate:'候选',draft:'草案',published:'已发布'}[document.context.status]||document.context.status)} · 只读浏览 · 执行未验证`;
      return true;
    }
    return false;
  }
  if(editRequest)editor=mountEditor(host,{
    request:editRequest, openSaved,
    present:view=>{
      const applied=present(view);
      if(applied)onEditMode?.(view.context.kind==='edit-copy');
      return applied;
    },
    select:(id,{data=false}={})=>{
      if(!id){controller.select(null);return;}
      chooseObject(id,true);
      if(data && controller.state.focusId===id && !controller.state.showData){
        controller.state.toggleData();
        controller.draw();counts();
      }
    },
    notice:message=>{notice.textContent=message;},
    capture:()=>({selection:controller.state.captureView(),expanded:{...controller.expanded},
      pages:{...controller.pages},layout:controller.graph.getView()}),
    restore:view=>{
      if(!view)return;
      controller.expanded={...view.expanded};controller.pages={...view.pages};
      controller.state.restoreView(view.selection);controller.draw();controller.graph.restoreView(view.layout);
      show(controller.state.entity(controller.state.selectedId));counts();
    }
  });
  return {controller,present,error(message){notice.textContent=message;},destroy(){dead=true;editor?.destroy();controller.destroy();delete host.interactiveGraphController;host.replaceChildren();delete host.dataset.testid;}};
}

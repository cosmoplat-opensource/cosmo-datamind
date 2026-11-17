/* Shared read-only revision and expansion controller — DR-159 §§4–9. */
import {GraphView} from './graph.js';
import {GraphState} from './model.js';
const copy = value => JSON.parse(JSON.stringify(value));
export class StudioController {
  constructor(svg, callbacks={}) {
    this.svg=svg;this.callbacks=callbacks;this.epoch=0;this.dead=false;this.context=null;
    this.expanded={};this.pages={};this.changes={added:[],modified:[],removed:[]};this.state=new GraphState();this.makeGraph();
  }
  makeGraph(){this.graph=new GraphView(this.svg,{onSelect:id=>this.select(id),onDoubleClick:id=>{
    if(this.state.nodes.get(id)?.type==='class')this.toggleClass(id);
    else if(this.state.individuals.has(id)){this.state.select(id);this.state.toggleRelations();this.draw();this.callbacks.onSelect?.(id,this.state.entity(id));}
  }});}
  beginContext(context){
    if(this.dead)return this.epoch;
    this.graph.destroy();this.makeGraph();this.state=new GraphState();this.document=null;
    this.context=copy(context);this.expanded={};this.pages={};this.changes={added:[],modified:[],removed:[]};return ++this.epoch;
  }
  update(document,{epoch}={}){
    if(this.dead||epoch!==this.epoch||document.schema!=='datamind-studio/1')return false;
    const incoming=document.context;
    if(!this.context||incoming.kind!==this.context.kind||incoming.key!==this.context.key)return false;
    if(typeof incoming.revision==='number'&&incoming.revision<this.context.revision)return false;
    if(typeof incoming.revision==='string'&&incoming.revision!==this.context.revision)return false;
    const previous=new Map((this.document?.nodes||[]).map(n=>[n.id,n])),next=new Map(document.nodes.map(n=>[n.id,n]));
    this.changes={added:[...next.keys()].filter(id=>!previous.has(id)),modified:[...next.keys()].filter(id=>previous.has(id)&&JSON.stringify(previous.get(id))!==JSON.stringify(next.get(id))),removed:[...previous.keys()].filter(id=>!next.has(id))};
    const view=this.graph.getView(),selection=this.state.captureView();
    this.document=copy(document);this.context=copy(incoming);this.state.load(copy(document));this.state.restoreView(selection);
    for(const id of Object.keys(this.expanded)){
      if(!next.has(id)){delete this.expanded[id];delete this.pages[id];}
      else if((this.pages[id]||0)>=(next.get(id).individuals||[]).length)this.pages[id]=0;
    }
    const valid=new Set([...document.nodes,...document.individuals].map(n=>n.id));
    for(const id of this.graph.positions.keys())if(!valid.has(id))this.graph.positions.delete(id);
    this.state.expanded=new Set(Object.keys(this.expanded));this.draw();
    if(previous.size)this.graph.restoreView(view);
    this.callbacks.onChange?.(this.capture());return true;
  }
  projected(){
    const view=this.state.view(this.filters||{}),allowed=new Set();
    for(const [id,count] of Object.entries(this.expanded))for(const item of (this.state.nodes.get(id)?.individuals||[]).slice(this.pages[id]||0,(this.pages[id]||0)+count))allowed.add(item);
    if(this.state.focusId)allowed.add(this.state.focusId);
    if(!this.state.local){
      view.nodes=view.nodes.filter(n=>n.type!=='individual'||allowed.has(n.id));
      const ids=new Set(view.nodes.map(n=>n.id));view.edges=view.edges.filter(e=>ids.has(e.source)&&ids.has(e.target));
      for(const edge of this.document?.relations||[])if(ids.has(edge.source)&&ids.has(edge.target))view.edges.push({...edge,kind:'relation'});
    }
    return view;
  }
  draw(){
    const view=this.projected();
    if(view.nodes.length>300||view.edges.length>600){this.callbacks.onNotice?.('当前范围过大，请收起其它类别或选择局部范围。');return false;}
    this.graph.setData(view);this.graph.setSelection(this.state.selectedId);return true;
  }
  select(id){if(this.dead)return;this.state.select(id);this.draw();this.callbacks.onSelect?.(id,this.state.entity(id));}
  toggleClass(id){
    if(this.dead)return;
    const node=this.state.nodes.get(id);
    if(!node?.individuals?.length){this.callbacks.onNotice?.(this.context?.kind==='design'?'当前设计阶段尚未构建实例。':'该类别没有显式归属实例。');return;}
    const old={...this.expanded};
    if(this.expanded[id]){delete this.expanded[id];delete this.pages[id];}else this.expanded[id]=50;
    this.state.expanded=new Set(Object.keys(this.expanded));
    if(!this.draw()){this.expanded=old;this.state.expanded=new Set(Object.keys(old));}
    this.callbacks.onChange?.(this.capture());
  }
  loadMore(id){
    if(this.dead||!this.expanded[id])return;const old=this.expanded[id];this.expanded[id]+=50;
    if(!this.draw())this.expanded[id]=old;this.callbacks.onChange?.(this.capture());
  }
  page(id,direction=1){
    if(this.dead||!this.expanded[id])return;
    const total=this.state.nodes.get(id)?.individuals?.length||0,old={offset:this.pages[id]||0,count:this.expanded[id]};
    const offset=direction>0?old.offset+old.count:Math.max(0,old.offset-50);if(offset>=total)return;
    this.pages[id]=offset;this.expanded[id]=50;
    if(!this.draw()){this.pages[id]=old.offset;this.expanded[id]=old.count;}
    this.callbacks.onChange?.(this.capture());
  }
  pause(value){if(!this.dead)this.graph.pause(value);}
  pin(id,value){if(this.dead)return;const point=this.graph.positions.get(id);if(point){point.pinned=value;this.graph.paint();}}
  capture(){return copy({context:this.context,epoch:this.epoch,selectedId:this.state.selectedId,expanded:this.expanded,pages:this.pages,changes:this.changes,...this.graph.getView()});}
  destroy(){if(this.dead)return;this.dead=true;this.epoch++;this.graph.destroy();}
}

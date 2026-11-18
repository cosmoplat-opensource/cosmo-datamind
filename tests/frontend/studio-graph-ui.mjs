/* Shared graph controller behavioral oracles — DR-161 / IR-031 T02 RED. */
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import path from 'node:path';

class Element {
  constructor(tag) {this.tag=tag;this.children=[];this.attributes={};this.style={};this.events=new Map();this.clientWidth=900;this.clientHeight=650;}
  setAttribute(k,v){this.attributes[k]=String(v);}
  getAttribute(k){return this.attributes[k];}
  append(...items){for(const e of items){e.parent=this;this.children.push(e);}}
  replaceChildren(...items){this.children=[];this.append(...items);}
  addEventListener(type,fn){if(!this.events.has(type))this.events.set(type,new Set());this.events.get(type).add(fn);}
  removeEventListener(type,fn){this.events.get(type)?.delete(fn);}
  getBoundingClientRect(){return {left:0,top:0,width:900,height:650};}
  setPointerCapture(id){this.captured=id;}
  hasPointerCapture(id){return this.captured===id;}
  releasePointerCapture(){this.captured=null;}
  remove(){this.parent.children=this.parent.children.filter(x=>x!==this);}
}
const observers=new Set(),frames=new Map();let frame=0;
globalThis.document={createElementNS:(_ns,tag)=>new Element(tag)};
globalThis.ResizeObserver=class {observe(){observers.add(this);}disconnect(){observers.delete(this);}};
globalThis.requestAnimationFrame=fn=>{frames.set(++frame,fn);return frame;};
globalThis.cancelAnimationFrame=id=>frames.delete(id);
const ns='https://studio.test/robot#';
const node=(id,label=id)=>({id:ns+id,iri:ns+id,type:'class',labels:[{value:label,language:'zh'}],definition:'',individuals:[]});
function fixture(revision=1,key='design_A'){
  return {schema:'datamind-studio/1',context:{kind:'design',key,revision,stage:'classes',status:'awaiting_confirmation',snapshot_sha256:'a'.repeat(64)},
    metadata:{read_only:true,name:'fixture'},warnings:[],nodes:[node('A'),node('B')],properties:[],individuals:[],relations:[],
    edges:[{id:'sub',source:ns+'B',target:ns+'A',predicate:'http://www.w3.org/2000/01/rdf-schema#subClassOf',kind:'subclass'}]};
}
const modulePath=path.resolve('ui/modules/studio/controller.js');
assert(existsSync(modulePath),'DR-161 requires the shared StudioController; product module is not implemented');
const {StudioController}=await import(pathToFileURL(modulePath));
const position=(c,id)=>new Map(c.capture().positions).get(ns+id);
const visit=(element,fn)=>{fn(element);element.children.forEach(e=>visit(e,fn));};
const ids=svg=>{const result=[];visit(svg,e=>{if(e.getAttribute('data-node-id'))result.push(e.getAttribute('data-node-id'));});return result;};
const cases={
  oversized_updates_are_transactional(){
    for(const replace of [false,true]){
      const notices=[],c=new StudioController(new Element('svg'),{onNotice:n=>notices.push(n)}),d=fixture();
      const epoch=c.beginContext(d.context);c.update(d,{epoch});c.pause(true);c.select(ns+'A');c.pin(ns+'A',true);
      const before=c.capture(),data=JSON.stringify(c.graph.data),next=fixture(2);
      next.nodes=Array.from({length:301},(_,i)=>node(replace?'new'+i:i<2?['A','B'][i]:'n'+i));next.edges=[];
      assert.equal(c.update(next,{epoch}),false,'an oversized revision must be rejected');
      assert.deepEqual(c.capture(),before);assert.equal(JSON.stringify(c.graph.data),data);
      assert.equal(c.document.context.revision,1);assert.match(notices.at(-1),/范围|上限/);
      const valid=fixture(3);assert.equal(c.update(valid,{epoch}),true);assert.equal(c.context.revision,3);c.destroy();
    }
  },
  revision_updates_preserve_view_and_reject_stale_context(){
    const c=new StudioController(new Element('svg'),{}),first=fixture();
    const epoch=c.beginContext(first.context);assert.equal(c.update(first,{epoch}),true);
    c.pause(true);c.pin(ns+'A',true);c.select(ns+'A');
    const before=c.capture(),p=position(c,'A');
    const next=fixture(2);next.nodes[0].labels[0].value='updated';next.nodes.push(node('C'));
    assert.equal(c.update(next,{epoch}),true);
    assert.deepEqual(position(c,'A'),p);assert.deepEqual(c.capture().transform,before.transform);
    assert.equal(c.capture().selectedId,ns+'A');assert.equal(c.capture().paused,true);
    assert.deepEqual(c.capture().changes.added,[ns+'C']);assert.deepEqual(c.capture().changes.modified,[ns+'A']);
    assert.equal(c.update(first,{epoch}),false);assert.equal(c.capture().context.revision,2);
    const other=fixture(1,'design_B'),newEpoch=c.beginContext(other.context);
    assert.equal(c.update(next,{epoch}),false);assert.equal(c.update(other,{epoch:newEpoch}),true);
    assert.equal(c.capture().selectedId,null);c.destroy();
  },
  multiple_canvases_dispose_resources_and_scope_events(){
    const svgs=[new Element('svg'),new Element('svg')],controllers=svgs.map(svg=>new StudioController(svg,{}));
    controllers.forEach(c=>{const d=fixture();c.update(d,{epoch:c.beginContext(d.context)});});
    const defs=svgs.map(svg=>{const result=[];visit(svg,e=>{if(e.getAttribute('id'))result.push(e.getAttribute('id'));});return result;});
    assert(defs.every(d=>d.length>0));assert.equal(defs[0].filter(id=>defs[1].includes(id)).length,0);
    controllers[0].destroy();assert(ids(svgs[1]).includes(ns+'A'));
    assert.equal(controllers[0].update(fixture(),{epoch:0}),false);controllers[1].destroy();
    assert.equal(frames.size,0);assert.equal(observers.size,0);
    for(const svg of svgs)for(const handlers of svg.events.values())assert.equal(handlers.size,0);
  },
  paged_expansion_deduplicates_shared_membership(){
    const svg=new Element('svg'),c=new StudioController(svg,{}),d=fixture();
    d.individuals=Array.from({length:51},(_,i)=>({id:ns+'i'+String(i).padStart(3,'0'),iri:ns+'i'+String(i).padStart(3,'0'),labels:[],types:[ns+'A',ns+'B'],data:[]}));
    d.nodes.forEach(n=>{n.individuals=d.individuals.map(i=>i.id);});
    c.update(d,{epoch:c.beginContext(d.context)});c.pause(true);
    c.toggleClass(ns+'A');assert.equal(ids(svg).length,52);
    c.loadMore(ns+'A');assert.equal(ids(svg).length,53);
    c.toggleClass(ns+'B');assert.equal(ids(svg).length,53);
    c.toggleClass(ns+'A');assert.equal(ids(svg).length,52); // B still references first 50.
    c.toggleClass(ns+'B');assert.equal(ids(svg).length,2);c.destroy();
  },
  deleting_node_clears_selection_and_marks_removal(){
    const c=new StudioController(new Element('svg'),{}),d=fixture();c.update(d,{epoch:c.beginContext(d.context)});c.select(ns+'B');
    const next=fixture(2);next.nodes.pop();next.edges=[];
    c.update(next,{epoch:c.capture().epoch});
    assert.equal(c.capture().selectedId,null);assert.deepEqual(c.capture().changes.removed,[ns+'B']);c.destroy();
  },
  empty_classes_notice_and_updates_do_not_trigger_writes(){
    const svg=new Element('svg'),selected=[],notices=[],c=new StudioController(svg,{onSelect:(...x)=>selected.push(x),onNotice:x=>notices.push(x)});
    const d=fixture();c.update(d,{epoch:c.beginContext(d.context)});
    assert.equal(selected.length,0);c.toggleClass(ns+'A');assert.equal(notices.length,1);assert.equal(ids(svg).length,2);
    c.select(ns+'A');assert.equal(selected.length,1);assert.equal(selected[0][0],ns+'A');
    assert.equal(selected[0][1].id,ns+'A');c.destroy();
  },
  snapshot_is_detached_and_pause_pin_are_preserved(){
    const c=new StudioController(new Element('svg'),{}),d=fixture();c.update(d,{epoch:c.beginContext(d.context)});c.pause(true);c.pin(ns+'A',true);
    const snap=c.capture();assert.equal(position(c,'A').pinned,true);snap.context.key='wrong';snap.positions.length=0;
    assert.equal(c.capture().context.key,'design_A');assert(position(c,'A'));
    c.update(fixture(2),{epoch:c.capture().epoch});assert.equal(position(c,'A').pinned,true);assert.equal(c.capture().paused,true);
    c.pin(ns+'A',false);assert.equal(position(c,'A').pinned,false);c.destroy();
  }
};
assert(Object.hasOwn(cases,process.argv[2]),'unknown test case');
cases[process.argv[2]]();

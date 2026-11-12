'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm'), path=require('node:path');
const root=path.resolve(__dirname,'../..');
function fixture(){
 const elements=new Map(),saved=new Map(),messages=[];
 const $=id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',style:{},disabled:false,querySelectorAll:()=>[]});return elements.get(id);};
 const c={console,Headers,URL,Response,document:{getElementById:id=>$('#'+id)},$,localStorage:{getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)},
  location:{href:'http://localhost/#catalog',assign(url){this.assigned=url;}},toast:(m)=>messages.push(m),
  esc:x=>String(x??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x])),
  fetch:async()=>{throw Error('unexpected network');},J:async()=>({}),DQ_CONV:null,dqRenderHist:()=>{},DQ_DS:{graphs:[],tables:[]},BC:{},EG_Q:[],DQ_SKILL:'',
  mk:()=>{},AX:{},echarts:{getInstanceByDom:()=>null},COLZ:['blue'],cell:String};
 c.window=c;const ctx=vm.createContext(c);
 vm.runInContext(fs.readFileSync(path.join(root,'ui/modules/database-scope.js'),'utf8'),ctx);
 vm.runInContext(fs.readFileSync(path.join(root,'ui/modules/qa-history.js'),'utf8'),ctx);
 const ui=fs.readFileSync(path.join(root,'ui/index.html'),'utf8');
 vm.runInContext(ui.slice(ui.indexOf('let VZ_DATA=null'),ui.indexOf('async function skills()')),ctx);
 return {c,ctx,$,saved,messages,run:s=>vm.runInContext(s,ctx)};
}
const cases={
 async scalar_kpi_does_not_invent_a_period(){
  const f=fixture();f.run("VZ_DATA={columns:['n'],rows:[{n:200}]}");
  f.$('#vz_type').value='kpi';f.$('#vz_x').value='n';f.$('#vz_y').selectedOptions=[{value:'n'}];
  f.c.vzRender();assert.ok(f.$('#vz_prev').innerHTML.includes('200'));
  assert.ok(!f.$('#vz_prev').innerHTML.includes('末期'));
 },
 async large_graph_opens_in_overview_and_preserves_readable_mode(){
  const f=fixture(),select=f.$('#g_layout');select.dataset={};
  f.c.appGraphInitialLayout({nodes:Array(121)});assert.equal(select.value,'force');
  select.dataset.userSelected='true';select.value='dagre';
  f.c.appGraphInitialLayout({nodes:Array(121)});assert.equal(select.value,'dagre');
  f.c.GKIND={object:{fill:'white',stroke:'blue',text:'blue'}};f.c.GEST={};
  f.c.setTimeout=()=>{};f.$('#canvas').offsetWidth=1200;f.$('#canvas').offsetHeight=800;
  f.c.G6={Arrow:{triangle:()=>''},Graph:class{
   constructor(){this.handlers={};this.zoom=1;}on(event,fn){this.handlers[event]=fn;}data(d){this.nodes=d.nodes;}
   render(){}fitView(){this.zoom=.1;}getZoom(){return this.zoom;}zoomTo(z){this.zoom=z;}fitCenter(){}
   getNodes(){return this.nodes.map(n=>({getModel:()=>n}));}findById(){return {};}focusItem(){}
  }};
  vm.runInContext(fs.readFileSync(path.join(root,'ui/modules/graph-render.js'),'utf8'),f.ctx);
  const graph=f.c.g6render('canvas',{nodes:Array.from({length:121},(_,i)=>({id:String(i),kind:'object'})),edges:[]},'dagre');
  assert.equal(graph.__view,'all');assert.equal(graph.getZoom(),.1);
  graph.__view='readable';graph.handlers.afterlayout();assert.equal(graph.getZoom(),.85);
 },
 async request_scope_preserves_headers_and_signal(){
  const f=fixture();f.c.APP_DATA.id='conn_b';const signal={};
  const opts=f.c.appRequestOptions('/api/query',{method:'POST',signal,headers:{'Content-Type':'application/json','Cache-Control':'no-cache'},body:'{}'});
  assert.equal(opts.headers.get('X-DataMind-Source'),'conn_b');assert.equal(opts.headers.get('Content-Type'),'application/json');assert.equal(opts.signal,signal);
  assert.equal(f.c.appRequestOptions('/api/viz/run',{headers:{'X-DataMind-Source':'conn_a'}}).headers.get('X-DataMind-Source'),'conn_a');
  assert.equal(f.c.appRequestOptions('https://example.invalid/',{}).headers,undefined);
 },
 async switch_retains_page_and_recovers_without_storage(){
  const f=fixture();f.c.APP_DATA.id='conn_a';f.c.localStorage.setItem=()=>{throw Error('denied');};
  f.c.appSourceChange('conn_b');const url=new URL(f.c.location.assigned);
  assert.equal(url.searchParams.get('source'),'conn_b');assert.equal(url.hash,'#catalog');
 },
 async saved_history_is_separate_for_each_database(){
  const f=fixture();const a={id:'a',turns:[],scope:{}},b={id:'b',turns:[],scope:{}};
  f.c.APP_DATA.id='conn_a';f.c.dqSaveConv(a);f.c.APP_DATA.id='conn_b';f.c.dqSaveConv(b);
  assert.equal(f.c.dqAllConv().length,1);assert.equal(f.c.dqAllConv()[0].id,'b');
  f.c.APP_DATA.id='conn_a';assert.equal(f.c.dqAllConv()[0].id,'a');
  f.c.APP_DATA.id='demo';assert.equal(f.c.appSourceStorageKey(),'dq_convs');
 },
 async changed_visual_source_discards_late_result(){
  const f=fixture();let release;const wait=new Promise(r=>release=r);
  f.$('#vz_src').value='conn_a';f.$('#vz_sql').value='SELECT value FROM shared';
  f.c.J=async u=>u==='/api/viz/run'?wait:{tables:[]};
  const pending=f.c.vzRun();f.$('#vz_src').value='conn_b';f.c.vzSrcChange();
  release({rows:[{value:3}],columns:['value']});await pending;
  assert.equal(f.run('VZ_DATA'),null);assert.equal(f.$('#vz_sql').value,'');
  f.c.vzAdd();assert.equal(f.run('VZ_BOARD.length'),0);
 },
 async all_200_tables_can_be_selected(){
  const f=fixture();f.$('#vz_src').value='conn_b';
  f.c.J=async()=>({tables:Array.from({length:200},(_,i)=>({name:'table_'+i,rows:1}))});
  await f.c.vzQuickOpts();assert.match(f.$('#vz_quick').innerHTML,/table_199/);
 },
 async stale_source_is_reported_and_bootstrap_is_unscoped(){
  const f=fixture();f.saved.set('datamind_source','removed');let options;
  f.c.fetch=async(_u,o)=>{options=o;return new Response(JSON.stringify({sources:[{id:'demo',name:'A',kind:'sqlite',ready:true,tables:2}]}),{headers:{'Content-Type':'application/json'}});};
  f.c.J=async()=>({tables:2,graphs:['demo'],default_graph:'demo'});
  assert.equal(await f.c.appSourceInit(),true);assert.equal(options,undefined);
  assert.equal(f.c.APP_DATA.id,'demo');assert.match(f.$('#app_source_status').textContent,/原数据源不可用/);
 },
 async drafts_are_restored_only_for_their_database(){
  const f=fixture(),drafts=new Map();
  f.c.sessionStorage={setItem:(k,v)=>drafts.set(k,v),getItem:k=>drafts.get(k)||null};
  f.c.APP_DATA.id='conn_a';f.$('#sd_q').value='SELECT 7 AS a';f.c.appSaveDraft();
  f.c.APP_DATA.id='conn_b';f.$('#sd_q').value='SELECT 31 AS b';f.c.appSaveDraft();
  f.$('#sd_q').value='';f.c.APP_DATA.id='conn_a';f.c.appRestoreDraft();
  assert.equal(f.$('#sd_q').value,'SELECT 7 AS a');
  f.c.APP_DATA.id='conn_b';f.c.appRestoreDraft();assert.equal(f.$('#sd_q').value,'SELECT 31 AS b');
 }
};
const name=process.argv[2];if(!cases[name])throw Error('unknown test case');
cases[name]().then(()=>console.log('PASS '+name)).catch(e=>{console.error(e);process.exitCode=1;});

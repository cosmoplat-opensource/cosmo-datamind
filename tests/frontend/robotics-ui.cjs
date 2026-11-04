/* Deterministic controller contracts, synthetic HTTP only — DR-063 / DR-064 / DR-065. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ROOT = path.resolve(__dirname, '../..');
const clone = value => JSON.parse(JSON.stringify(value));
const response = (data, status=200) => new Response(JSON.stringify(data), {status, headers:{'Content-Type':'application/json'}});
const defer = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
const CAPS = {enabled:true,recipes:[{id:'bt_xml',profiles:['ur_pick_place'],input_roles:['behavior_tree','node_models']}],
  limits:{file_bytes:1024,pending:4,timeout_seconds:300}};
const run = (id='run_a', status='queued') => ({run_id:id,operation:'build_ontology',status,build_input:{name:'候选',
  recipe:'bt_xml',profile_id:'ur_pick_place',sources:[]},output_asset_ref:null,result:null,artifacts:[]});
function fixture(module='build') {
  const source = path.join(ROOT, 'ui/modules', module==='build'?'robotics-build.js':'robotics-assets.js');
  const context = vm.createContext({console,URL,URLSearchParams,FormData,File,Blob,Response,AbortController,
    setTimeout,clearTimeout,crypto:require('node:crypto').webcrypto});
  context.window=context;
  vm.runInContext(fs.readFileSync(source,'utf8'),context,{filename:source});
  const api=context[module==='build'?'RoboticsBuild':'RoboticsAssets'];
  assert.equal(typeof api?.createController,'function','Module must expose the reviewed controller boundary');
  const calls=[], emissions=[], timers=new Map();let next=0,key=0;
  const f={calls,emissions,timers,handler:async()=>{throw Error('Unexpected HTTP request');}};
  f.controller=api.createController({
    fetch:async(url,options={})=>{calls.push({url:String(url),options});return f.handler(String(url),options);},
    onChange:s=>emissions.push(s),requestId:()=>`ui-request-${++key}`,
    schedule:(fn,ms)=>{assert.ok(ms>=250 && ms<=5000);timers.set(++next,fn);return next;},
    cancelSchedule:id=>timers.delete(id),
  });
  f.state=()=>f.controller.snapshot();
  f.posts=()=>calls.filter(c=>c.options.method==='POST');
  f.draft={name:'候选',profile:'ur_pick_place',behavior_tree:new File(['<root/>'],'same.xml'),node_models:new File(['<models/>'],'same.xml')};
  f.ready=async()=>{f.handler=async()=>response(CAPS);await f.controller.init();f.controller.setDraft(f.draft);f.calls.length=0;};
  return f;
}
const cases={
  async disabled_feature() {
    const f=fixture();f.handler=async()=>response({...CAPS,enabled:false});await f.controller.init();
    f.controller.setDraft(f.draft);await f.controller.submit();assert.equal(f.state().enabled,false);assert.equal(f.posts().length,0);
  },
  async capability_failure() {
    const f=fixture();f.handler=async()=>response({error:{code:'unavailable',message:'能力加载失败'}},503);
    await f.controller.init();f.controller.setDraft(f.draft);await f.controller.submit();
    assert.equal(f.state().enabled,false);assert.ok(f.state().error);assert.equal(f.posts().length,0);
  },
  async required_input() {
    const f=fixture();await f.ready();f.controller.setDraft({[process.argv[3]]:process.argv[3]==='name'?'  ':null});await f.controller.submit();
    assert.equal(f.posts().length,0);assert.ok(f.state().error);assert.equal(f.state().busy,false);
  },
  async empty_file() {
    const f=fixture();await f.ready();f.controller.setDraft({behavior_tree:new File([], 'empty.xml')});
    await f.controller.submit();assert.equal(f.posts().length,0);assert.ok(f.state().error);
  },
  async size_limit() {
    const f=fixture();await f.ready();f.controller.setDraft({node_models:new File(['x'.repeat(1025)],'large.xml')});
    await f.controller.submit();assert.equal(f.posts().length,0);assert.ok(f.state().error);
  },
  async unsupported_profile() {
    const f=fixture();await f.ready();f.controller.setDraft({profile:'unregistered'});await f.controller.submit();
    assert.equal(f.posts().length,0);assert.ok(f.state().error);
  },
  async exact_multipart_roles() {
    const f=fixture();await f.ready();f.handler=async()=>response(run(),202);await f.controller.submit();
    const sent=f.posts()[0];assert.equal(sent.url,'/api/robotics/builds');assert.ok(sent.options.body instanceof FormData);
    assert.deepEqual([...sent.options.body.keys()].sort(),['behavior_tree','name','node_models','profile','recipe','request_id']);
    assert.equal(sent.options.body.get('recipe'),'bt_xml');assert.equal(sent.options.body.get('profile'),'ur_pick_place');
    assert.equal(await sent.options.body.get('behavior_tree').text(),'<root/>');
    assert.equal(await sent.options.body.get('node_models').text(),'<models/>');
    assert.equal(sent.options.body.get('behavior_tree').name,'same.xml');assert.equal(sent.options.body.get('node_models').name,'same.xml');
    assert.ok(!Object.keys(sent.options.headers||{}).some(k=>k.toLowerCase()==='content-type'),'Browser owns multipart boundary');
    assert.equal(f.state().run.status,'queued');assert.equal(f.state().run.output_asset_ref,null);
  },
  async duplicate_submit() {
    const f=fixture();await f.ready();const gate=defer();f.handler=()=>gate.promise;
    const first=f.controller.submit();await f.controller.submit();assert.equal(f.posts().length,1);assert.equal(f.state().busy,true);
    gate.resolve(response(run(),202));await first;assert.equal(f.state().busy,false);
  },
  async transport_retry_identity() {
    const f=fixture();await f.ready();f.handler=async()=>{throw Error('connection lost after admission');};
    await f.controller.submit();const id=f.posts()[0].options.body.get('request_id');assert.ok(f.state().error);
    assert.equal(f.state().draft.behavior_tree,f.draft.behavior_tree);
    f.handler=async()=>response(run(),202);await f.controller.submit();assert.equal(f.posts()[1].options.body.get('request_id'),id);
  },
  async changed_input_new_identity() {
    const f=fixture();await f.ready();f.handler=async()=>response({error:{code:'queue_full',message:'队列已满'}},429);
    await f.controller.submit();const id=f.posts()[0].options.body.get('request_id');
    f.controller.setDraft({name:'另一个候选'});await f.controller.submit();assert.notEqual(f.posts()[1].options.body.get('request_id'),id);
  },
  async changed_file_new_identity() {
    const f=fixture();await f.ready();f.handler=async()=>{throw Error('Unknown admission outcome');};
    await f.controller.submit();const id=f.posts()[0].options.body.get('request_id');
    f.controller.setDraft({behavior_tree:new File(['<next/>'],'same.xml',{lastModified:f.draft.behavior_tree.lastModified})});
    await f.controller.submit();assert.notEqual(f.posts()[1].options.body.get('request_id'),id);
  },
  async preserve_new_draft() {
    const f=fixture();await f.ready();const gate=defer();f.handler=()=>gate.promise;
    const first=f.controller.submit();f.controller.setDraft({name:'用户刚输入的新名称'});
    gate.resolve(response({error:{code:'queue_full',message:'队列已满'}},429));await first;
    assert.equal(f.state().draft.name,'用户刚输入的新名称');assert.equal(f.state().busy,false);
  },
  async http_error() {
    const f=fixture();await f.ready();const status=Number(process.argv[3]);
    f.handler=async()=>response({error:{code:'request_rejected',message:'请求未受理'}},status);
    await f.controller.submit();assert.equal(f.state().error.status,status);assert.equal(f.state().error.code,'request_rejected');
    assert.equal(f.state().error.message,'请求未受理');assert.equal(f.state().busy,false);assert.equal(f.state().run,null);
    assert.equal(f.state().draft.node_models,f.draft.node_models);
  },
  async non_json_error() {
    const f=fixture();await f.ready();f.handler=async()=>new Response('<html>proxy unavailable</html>',{status:502});
    await f.controller.submit();assert.equal(f.state().busy,false);assert.ok(f.state().error.message);assert.equal(f.state().run,null);
  },
  async cursor_deduplication() {
    const f=fixture();await f.ready();let round=0;
    f.handler=async url=>url.includes('/events?')?response({items:round++===0?
      [{seq:1,kind:'state',payload:{status:'queued'}},{seq:2,kind:'state',payload:{status:'running'}}]:
      [{seq:2,kind:'state',payload:{status:'running'}},{seq:3,kind:'stage',payload:{stage:'computing'}}]}):response(run('run_a','running'));
    await f.controller.openRun('run_a');await f.controller.poll();
    assert.deepEqual(Array.from(f.state().events,e=>e.seq),[1,2,3]);assert.equal(f.state().after,3);
    assert.ok(f.calls.some(c=>c.url.endsWith('/events?after=2')));assert.ok(f.timers.size<=1);
  },
  async poll_single_flight() {
    const f=fixture();await f.ready();f.handler=async url=>response(url.includes('/events?')?{items:[]}:run());
    await f.controller.openRun('run_a');f.calls.length=0;const gate=defer();
    f.handler=async url=>{await gate.promise;return response(url.includes('/events?')?{items:[]}:run());};
    const first=f.controller.poll(),second=f.controller.poll();gate.resolve();await Promise.all([first,second]);
    assert.equal(f.calls.filter(c=>c.url==='/api/domain-runs/run_a').length,1);
  },
  async refresh_never_posts() {
    const f=fixture();await f.ready();f.handler=async url=>response(url.includes('/events?')?{items:[]}:run());
    await f.controller.openRun('run_a');assert.equal(f.posts().length,0);assert.equal(f.state().run.run_id,'run_a');
  },
  async late_run_response() {
    const f=fixture();await f.ready();const gate=defer();
    f.handler=async url=>{if(url.includes('run_a'))await gate.promise;return response(url.includes('/events?')?{items:[]}:
      run(url.includes('run_a')?'run_a':'run_b'));};
    const first=f.controller.openRun('run_a');await f.controller.openRun('run_b');gate.resolve();await first;
    assert.equal(f.state().run.run_id,'run_b');
  },
  async disposal_stops_polling_and_late_render() {
    const f=fixture();await f.ready();f.handler=async url=>response(url.includes('/events?')?{items:[]}:run());
    await f.controller.openRun('run_a');const scheduled=[...f.timers.values()];const gate=defer();
    f.handler=async url=>{await gate.promise;return response(url.includes('/events?')?{items:[]}:run('run_a','running'));};
    const pending=f.controller.poll();f.controller.dispose();const count=f.emissions.length;
    gate.resolve();await pending;const calls=f.calls.length;for(const fn of scheduled)await fn();
    assert.equal(f.timers.size,0);assert.equal(f.emissions.length,count);assert.equal(f.calls.length,calls);
  },
  async terminal_stops_polling() {
    const f=fixture();await f.ready();const status=process.argv[3];
    f.handler=async url=>response(url.includes('/events?')?{items:[]}:run('run_a',status));
    await f.controller.openRun('run_a');assert.equal(f.state().run.status,status);assert.equal(f.timers.size,0);
  },
  async poll_error_preserves_last_state() {
    const f=fixture();await f.ready();f.handler=async url=>response(url.includes('/events?')?{items:[]}:run('run_a','running'));
    await f.controller.openRun('run_a');f.handler=async()=>response({error:{code:'offline',message:'暂不可读'}},503);
    await f.controller.poll();assert.equal(f.state().run.status,'running');assert.ok(f.state().error);assert.equal(f.posts().length,0);
  },
  async explicit_retry_new_run() {
    const f=fixture();await f.ready();f.handler=async url=>response(url.includes('/events?')?{items:[]}:run('run_a','failed'));
    await f.controller.openRun('run_a');assert.equal(f.posts().length,0);
    f.handler=async()=>response({...run('run_retry'),retry_of:'run_a'},202);await f.controller.retry();
    const sent=f.posts()[0];assert.equal(sent.url,'/api/domain-runs/run_a/retry');
    assert.deepEqual(Object.keys(JSON.parse(sent.options.body)),['request_id']);
    assert.equal(f.state().run.run_id,'run_retry');assert.equal(f.state().run.retry_of,'run_a');
  },
  async retry_guards() {
    const f=fixture();await f.ready();f.handler=async url=>response(url.includes('/events?')?{items:[]}:run());
    await f.controller.openRun('run_a');await f.controller.retry();assert.equal(f.posts().length,0);
  },
  async assets_catalog_roles() {
    const f=fixture('assets');const items=[{entry_kind:'legacy_reference',domain:'data',legacy_key:'built_old',name:'旧图'},
      {entry_kind:'asset',domain:'robotics',ontology_id:'ont_a',version_id:'ver_a',publication_state:'candidate'}];
    f.handler=async()=>response({items});await f.controller.loadCatalog();assert.deepEqual(clone(f.state().items),items);
    await f.controller.loadCatalog({domain:'robotics'});assert.ok(f.calls.at(-1).url.includes('domain=robotics'));
  },
  async assets_catalog_error_not_empty_success() {
    const f=fixture('assets');f.handler=async()=>response({error:{code:'unavailable',message:'目录不可读'}},503);
    await f.controller.loadCatalog();assert.ok(f.state().error);assert.equal(f.state().error.status,503);
  },
  async assets_catalog_late_filter() {
    const f=fixture('assets'),gate=defer();f.handler=async url=>{if(url.includes('domain=data'))await gate.promise;
      return response({items:[{domain:url.includes('domain=data')?'data':'robotics'}]});};
    const first=f.controller.loadCatalog({domain:'data'});await f.controller.loadCatalog({domain:'robotics'});
    gate.resolve();await first;assert.equal(f.state().items[0].domain,'robotics');
  },
  async assets_fixed_version() {
    const f=fixture('assets');f.handler=async()=>response({ontology_id:'ont_a',version_id:'ver_old',publication_state:'candidate'});
    await f.controller.openVersion('ont_a','ver_old');assert.equal(f.calls[0].url,'/api/ontology-assets/ont_a/versions/ver_old');
    assert.equal(f.state().detail.version_id,'ver_old');assert.equal(f.calls.length,1,'Never resolve requested version through latest catalog');
  },
  async assets_late_response() {
    const f=fixture('assets'),gate=defer();f.handler=async url=>{if(url.endsWith('ver_a'))await gate.promise;
      return response({ontology_id:'ont_a',version_id:url.endsWith('ver_a')?'ver_a':'ver_b'});};
    const first=f.controller.openVersion('ont_a','ver_a');await f.controller.openVersion('ont_a','ver_b');gate.resolve();await first;
    assert.equal(f.state().detail.version_id,'ver_b');
  },
  async assets_error_no_old_detail() {
    const f=fixture('assets');f.handler=async()=>response({ontology_id:'ont_a',version_id:'ver_a'});
    await f.controller.openVersion('ont_a','ver_a');f.handler=async()=>response({error:{code:'not_found',message:'版本不存在'}},404);
    await f.controller.openVersion('ont_a','missing');assert.equal(f.state().detail,null);assert.ok(f.state().error);
    assert.ok(!f.calls.some(c=>c.url.includes('/api/graph/')||c.url.includes('demo')));
  },
  async assets_wrong_identity_rejected() {
    const f=fixture('assets');f.handler=async()=>response({ontology_id:'another_asset',version_id:'ver_a'});
    await f.controller.openVersion('ont_a','ver_a');assert.equal(f.state().detail,null);assert.ok(f.state().error);
  },
  async assets_dispose_ignores_pending() {
    const f=fixture('assets'),gate=defer();f.handler=()=>gate.promise;
    const request=f.controller.openVersion('ont_a','ver_a');f.controller.dispose();const count=f.emissions.length;
    gate.resolve(response({ontology_id:'ont_a',version_id:'ver_a'}));await request;assert.equal(f.emissions.length,count);
  },
};
(async()=>{const test=cases[process.argv[2]];assert.ok(test,'Unknown case');await test();console.log('PASS '+process.argv.slice(2).join(' '));})()
  .catch(error=>{console.error(error);process.exitCode=1;});

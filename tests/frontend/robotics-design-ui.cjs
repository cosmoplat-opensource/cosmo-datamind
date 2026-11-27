/* Design controller contracts; synthetic transport only — DR-152 / T08 RED. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ROOT=path.resolve(__dirname,'../..'),clone=x=>JSON.parse(JSON.stringify(x));
const response=(x,status=200)=>new Response(JSON.stringify(x),{status,headers:{'Content-Type':'application/json'}});
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const state=(id='s1',extra={})=>({session_id:id,session_ref:{namespace:'robotics_design',id},method:'llm_tbox_abox',
  model:{provider:'openai',model:'synthetic',configuration_id:'fixed'},stage:'classes',revision:1,
  status:'awaiting_confirmation',run_id:'r1',snapshot_sha256:'digest-1',class_confirmation:null,schema_confirmation:null,
  result:{classes:[{id:'Skill',label:'技能'}],clarifications:[]},output_asset_ref:null,...extra});
function fixture(build=false){
  const file=path.join(ROOT,'ui/modules',build?'robotics-build.js':'robotics-design.js');
  assert.ok(fs.existsSync(file),'Reviewed design controller module is missing');
  const context=vm.createContext({console,URL,URLSearchParams,FormData,File,Blob,Response,AbortController,
    setTimeout,clearTimeout,crypto:require('node:crypto').webcrypto});context.window=context;
  vm.runInContext(fs.readFileSync(path.join(ROOT,'ui/modules/robotics-assets.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(file,'utf8'),context,{filename:file});
  const api=context[build?'RoboticsBuild':'RoboticsDesign'];assert.equal(typeof api?.createController,'function');
  const f={calls:[],emissions:[],timers:new Map(),delays:[],handler:async url=>response(url.includes('/events?')?{items:[]}:
    url.includes('/api/domain-runs/')?{run_id:'r1',status:'completed'}:state())};let id=0,timer=0;
  f.c=api.createController({fetch:async(url,options={})=>{f.calls.push({url:String(url),options});return f.handler(String(url),options);},
    onChange:s=>f.emissions.push(s),requestId:()=>`request-${++id}`,
    schedule:(fn,ms)=>{assert.ok(ms>=250&&ms<=5000);f.delays.push(ms);f.timers.set(++timer,fn);return timer;},
    cancelSchedule:key=>f.timers.delete(key)});
  f.s=()=>f.c.snapshot();f.tick=async()=>{const [key,fn]=[...f.timers][0];f.timers.delete(key);await fn();};f.posts=()=>f.calls.filter(x=>x.options.method==='POST');
  f.open=async extra=>{f.handler=async()=>response(state('s1',extra));await f.c.openSession('s1');f.calls.length=0;};
  return f;
}
const cases={
 async build_description_and_session(){
  const f=fixture(true);f.handler=async()=>response({enabled:true,build_ready:true,building_method:'llm_tbox_abox',model:state().model,
    recipes:[{id:'bt_xml',profiles:['ur_pick_place']}],limits:{file_bytes:1024}});await f.c.init();
  f.c.setDraft({name:'候选',profile:'ur_pick_place',description:'整理技能',behavior_tree:new File(['<root/>'],'a.xml'),node_models:new File(['<models/>'],'b.xml')});
  f.handler=async()=>response(state('s1',{revision:0,status:'needs_description',run_id:null}),202);await f.c.submit();
  assert.equal(f.posts()[0].options.body.get('description'),'整理技能');assert.equal(f.s().session.session_id,'s1');
  assert.ok(!f.calls.some(c=>c.url.includes('/domain-runs/null')));
 },
 async configuration_problem_blocks_submit(){
  const f=fixture(true);f.handler=async()=>response({enabled:true,build_ready:false,building_method:null,
    configuration_issue:{code:'configuration_error',message:'模型配置不完整'},recipes:[{id:'bt_xml',profiles:['ur_pick_place']}],limits:{file_bytes:1024}});
  await f.c.init();f.c.setDraft({name:'候选',profile:'ur_pick_place',behavior_tree:new File(['x'],'a.xml'),node_models:new File(['x'],'b.xml')});
  await f.c.submit();assert.equal(f.posts().length,0);assert.ok(f.s().error);
 },
 async restore_get_only(){const f=fixture();await f.open();assert.equal(f.s().session.session_id,'s1');assert.equal(f.posts().length,0);assert.equal(f.timers.size,0);},
 async message_exact_revision_stage(){const f=fixture();await f.open();f.c.setMessage('进一步设计');
  f.handler=async()=>response(state('s1',{status:'queued',run_id:'r2'}),202);await f.c.sendMessage();
  const sent=f.posts()[0];assert.equal(sent.url,'/api/robotics/design-sessions/s1/messages');
  const body=JSON.parse(sent.options.body);assert.deepEqual(Object.keys(body).sort(),['expected_revision','expected_stage','request_id','text']);
  assert.equal(body.expected_revision,1);assert.equal(body.expected_stage,'classes');assert.equal(body.text,'进一步设计');assert.ok(body.request_id);
 },
 async confirmation_snapshot(){const f=fixture();await f.open();f.handler=async()=>response(state('s1',{stage:'properties',revision:2,status:'awaiting_input'}));
  await f.c.confirmClasses();const sent=f.posts()[0];assert.equal(sent.url,'/api/robotics/design-sessions/s1/confirm-classes');
  assert.deepEqual({...JSON.parse(sent.options.body),request_id:'id'},{request_id:'id',expected_revision:1,snapshot_sha256:'digest-1'});
  assert.equal(f.s().session.stage,'properties');assert.equal(f.timers.size,0);
 },
 async schema_confirmation(){const f=fixture();await f.open({stage:'properties',revision:3,snapshot_sha256:'digest-3'});
  f.handler=async()=>response(state('s1',{stage:'abox',revision:4,status:'queued'}),202);await f.c.confirmSchema();
  assert.equal(f.posts()[0].url,'/api/robotics/design-sessions/s1/confirm-schema');assert.equal(JSON.parse(f.posts()[0].options.body).snapshot_sha256,'digest-3');
 },
 async edit_clears_current_output(){const f=fixture();await f.open({stage:'abox',revision:4,status:'completed',output_asset_ref:{ontology_id:'o',version_id:'v'}});
  f.handler=async()=>response(state('s1',{revision:5,status:'awaiting_input',run_id:null}));await f.c.editClasses();
  assert.equal(f.posts()[0].url,'/api/robotics/design-sessions/s1/edit-classes');assert.equal(f.s().session.output_asset_ref,null);
 },
 async duplicate_message(){const f=fixture();await f.open();f.c.setMessage('设计');const gate=defer();f.handler=()=>gate.promise;
  const first=f.c.sendMessage();await f.c.sendMessage();assert.equal(f.posts().length,1);assert.equal(f.s().busy,true);
  gate.resolve(response(state('s1',{status:'queued'}),202));await first;assert.equal(f.s().busy,false);
 },
 async transport_retry_identity(){const f=fixture();await f.open();f.c.setMessage('设计');f.handler=async()=>{throw Error('lost after admission');};
  await f.c.sendMessage();const id=JSON.parse(f.posts()[0].options.body).request_id;assert.equal(f.s().messageText,'设计');
  f.handler=async()=>response(state('s1',{status:'queued'}),202);await f.c.sendMessage();assert.equal(JSON.parse(f.posts()[1].options.body).request_id,id);
 },
 async changed_input_new_identity(){const f=fixture();await f.open();f.c.setMessage('设计');f.handler=async()=>{throw Error('lost');};await f.c.sendMessage();
  f.c.setMessage('新设计');await f.c.sendMessage();assert.notEqual(JSON.parse(f.posts()[0].options.body).request_id,JSON.parse(f.posts()[1].options.body).request_id);
 },
 async new_text_survives_late_success(){const f=fixture();await f.open();f.c.setMessage('旧输入');const gate=defer();f.handler=()=>gate.promise;
  const first=f.c.sendMessage();f.c.setMessage('新输入');gate.resolve(response(state('s1',{status:'queued'}),202));await first;assert.equal(f.s().messageText,'新输入');
 },
 async failure_preserves_input_and_design(){const f=fixture();await f.open();f.c.setMessage('保留输入');const old=clone(f.s().session);
  f.handler=async()=>response({error:{code:'revision_conflict',message:'请重新读取'}},409);await f.c.sendMessage();
  assert.equal(f.s().error.code,'revision_conflict');assert.equal(f.s().messageText,'保留输入');assert.deepEqual(clone(f.s().session),old);assert.equal(f.posts().length,1);
 },
 async non_json_error(){const f=fixture();await f.open();f.c.setMessage('设计');f.handler=async()=>new Response('<html>proxy failure</html>',{status:502});
  await f.c.sendMessage();assert.equal(f.s().busy,false);assert.ok(f.s().error.message);assert.equal(f.s().session.revision,1);
 },
 async wrong_session_identity(){const f=fixture();f.handler=async()=>response(state('another'));await f.c.openSession('s1');assert.equal(f.s().session,null);assert.ok(f.s().error);},
 async late_session_response(){const f=fixture(),gate=defer();f.handler=async url=>{if(url.endsWith('s1'))await gate.promise;return response(state(url.endsWith('s1')?'s1':'s2'));};
  const first=f.c.openSession('s1');await f.c.openSession('s2');gate.resolve();await first;assert.equal(f.s().session.session_id,'s2');
 },
 async dispose_ignores_pending(){const f=fixture();await f.open();const gate=defer();f.handler=()=>gate.promise;
  const pending=f.c.openSession('s2');f.c.dispose();const count=f.emissions.length;gate.resolve(response(state('s2')));await pending;
  assert.equal(f.emissions.length,count);assert.equal(f.timers.size,0);
 },
 async polling_single_flight(){const f=fixture();await f.open({status:'running'});f.calls.length=0;const gate=defer();
  f.handler=async url=>{await gate.promise;return response(url.includes('/events?')?{items:[]}:url.includes('/domain-runs/')?{run_id:'r1',status:'running'}:state('s1',{status:'running'}));};
  const a=f.c.poll(),b=f.c.poll();gate.resolve();await Promise.all([a,b]);assert.equal(f.calls.filter(c=>c.url==='/api/robotics/design-sessions/s1').length,1);assert.ok(f.timers.size<=1);
 },
 async stopped_status_does_not_poll(){const f=fixture();await f.open({status:process.argv[3]});assert.equal(f.timers.size,0);assert.equal(f.posts().length,0);},
 async history_does_not_change_writable_revision(){const f=fixture();await f.open({revision:3,stage:'properties'});
  f.handler=async()=>response({session_ref:{namespace:'robotics_design',id:'s1'},revision:1,state:{stage:'classes',result:{classes:[]}}});
  await f.c.openRevision(1);assert.equal(f.calls[0].url,'/api/robotics/design-sessions/s1/revisions/1');
  assert.equal(f.s().session.revision,3);assert.equal(f.s().selectedRevision.revision,1);assert.equal(f.posts().length,0);
 },
 async explicit_retry_bound_to_failure(){const f=fixture();await f.open({status:'interrupted',run_id:'failed'});
  f.handler=async()=>response(state('s1',{status:'queued',run_id:'retry'}),202);await f.c.retry();
  assert.equal(f.posts()[0].url,'/api/robotics/design-sessions/s1/retry');const body=JSON.parse(f.posts()[0].options.body);
  assert.deepEqual({...body,request_id:'id'},{request_id:'id',expected_revision:1,failed_run_id:'failed'});
 },
 async history_blocks_design_writes(){
  const [status, phase] = process.argv.slice(3);
  const f = fixture(), gate = defer();
  await f.open({revision:2, status, run_id:'failed-run'});
  f.c.setMessage('保留当前设计草稿');
  f.handler = async (url, options) => {
    if (options.method === 'POST') return response({error:{message:'unexpected write'}},409);
    await gate.promise;
    return response({revision:1,state:{stage:'classes'}});
  };
  const reading = f.c.openRevision(1);
  if (phase === 'loaded') { gate.resolve(); await reading; }
  try {
    for (const op of ['retry','sendMessage','editClasses','editProperties','confirmClasses','confirmSchema']) {
      await f.c[op]();
      assert.equal(f.posts().length, 0, op+' sent a write while history was '+phase);
    }
  } finally {
    gate.resolve();
    await reading;
  }
  assert.equal(f.s().selectedRevision.revision, 1);
  f.handler = async () => response({error:{message:'历史读取失败'}},404);
  await f.c.openRevision(2);
  assert.equal(f.s().selectedRevision.revision, 1);
  await f.c.retry();
  assert.equal(f.posts().length, 0);
  f.c.showCurrent();
  assert.equal(f.s().messageText, '保留当前设计草稿');
  f.handler = async () => response(state('s1',{revision:2,status:'queued'}),202);
  await f.c.retry();
  assert.equal(f.posts().length, 1);
  const body = JSON.parse(f.posts()[0].options.body);
  assert.equal(body.expected_revision, 2);
  assert.equal(body.failed_run_id, 'failed-run');
 },
 async retry_guard(){const f=fixture();await f.open();await f.c.retry();assert.equal(f.posts().length,0);},
 async history_selection_survives_pending_write(){
  const f=fixture(), write=defer(), history=defer();
  await f.open({revision:2,status:'failed',run_id:'failed-run'});
  f.handler=(url,options)=>options.method==='POST'?write.promise:response({revision:1,state:{stage:'classes'}});
  const retry=f.c.retry();
  await f.c.openRevision(1);
  write.resolve(response(state('s1',{revision:2,status:'queued',run_id:'retried-run'}),202));
  await retry;
  assert.equal(f.s().session.run_id,'retried-run');
  assert.equal(f.s().selectedRevision.revision,1);
  await f.c.editClasses();
  assert.equal(f.posts().length,1);
  f.handler=()=>history.promise;
  const reading=f.c.openRevision(2);
  f.c.showCurrent();
  history.resolve(response({revision:2,state:{stage:'properties'}}));
  await reading;
  assert.equal(f.s().selectedRevision,null);
  assert.equal(f.posts().length,1);
  f.c.dispose();
 },
 // DR-189: failed reads retry within a fixed bound, then wait for an explicit reread.
 async failed_poll_retries_with_bounded_backoff(){const f=fixture();await f.open({status:'running'});
  assert.equal(f.timers.size,1);f.delays.length=0;
  f.handler=async()=>{throw Object.assign(Error('网络中断'),{name:'TypeError'});};
  for(let attempt=1;attempt<=5;attempt++){
   await f.tick();
   assert.equal(f.s().error.message,'网络中断');
   assert.deepEqual(clone(f.s().readRetry),{attempt,max:5,waiting:true});
   assert.equal(f.timers.size,1);
  }
  assert.deepEqual(f.delays,[2000,4000,5000,5000,5000]);
  await f.tick();
  assert.deepEqual(clone(f.s().readRetry),{attempt:6,max:5,waiting:false});
  assert.equal(f.timers.size,0);assert.equal(f.s().session.status,'running');
  const reads=f.calls.length;
  f.handler=async()=>response(state('s1',{status:'running'}));
  await f.c.reread();
  assert.equal(f.calls.length,reads+1);assert.equal(f.s().error,null);assert.equal(f.s().readRetry,null);
  assert.equal(f.timers.size,1);assert.equal(f.delays.at(-1),1000);assert.equal(f.posts().length,0);
  f.handler=async()=>{throw Error('再次中断');};await f.tick();
  assert.deepEqual(clone(f.s().readRetry),{attempt:1,max:5,waiting:true},'a successful reread resets the bound');
 },
 async non_transient_poll_error_waits_for_reread(){const f=fixture();await f.open({status:'queued'});
  f.handler=async()=>response({error:{code:'not_found',message:'设计会话不存在。'}},404);await f.tick();
  assert.equal(f.s().error.code,'not_found');assert.deepEqual(clone(f.s().readRetry),{attempt:1,max:5,waiting:false});
  assert.equal(f.timers.size,0);
  f.handler=async()=>response(state('s1',{status:'completed',output_asset_ref:{ontology_id:'o',version_id:'v'}}));
  await f.c.reread();assert.equal(f.s().session.status,'completed');assert.equal(f.s().error,null);assert.equal(f.timers.size,0);
 },
 async initial_read_failure_offers_reread(){const f=fixture();f.handler=async()=>{throw Error('网络中断');};await f.c.openSession('s1');
  assert.equal(f.s().session,null);assert.deepEqual(clone(f.s().readRetry),{attempt:1,max:5,waiting:false});assert.equal(f.timers.size,0);
  f.handler=async()=>response(state());await f.c.reread();assert.equal(f.s().session.session_id,'s1');assert.equal(f.s().readRetry,null);
 },
 async write_timeout_is_pending_confirmation_and_not_resent(){const f=fixture();await f.open();f.c.setMessage('设计');
  f.handler=async()=>{throw Object.assign(Error('读取超时，请稍后重试。'),{name:'TimeoutError',code:'request_timeout'});};
  await f.c.sendMessage();
  assert.equal(f.s().error.message,'请求超时，结果待确认。请重新读取设计状态后再操作。');
  assert.equal(f.s().readRetry,null);assert.equal(f.timers.size,0);assert.equal(f.posts().length,1);
  const id=JSON.parse(f.posts()[0].options.body).request_id;
  f.handler=async()=>response(state('s1',{status:'awaiting_confirmation'}));await f.c.reread();
  assert.equal(f.posts().length,1,'reread never resends the write');assert.equal(f.s().error,null);
  f.handler=async()=>response(state('s1',{status:'queued'}),202);await f.c.sendMessage();
  assert.equal(JSON.parse(f.posts()[1].options.body).request_id,id,'an explicit resend keeps the idempotency key');
 },
 async dispose_stops_read_retry(){const f=fixture();await f.open({status:'running'});f.handler=async()=>{throw Error('网络中断');};
  await f.tick();assert.equal(f.timers.size,1);f.c.dispose();assert.equal(f.timers.size,0);await f.c.reread();assert.equal(f.calls.length,1);
 },
 async snapshot_is_detached(){const f=fixture();await f.open();const s=f.s();s.session.result.classes[0].label='mutated';
  assert.equal(f.s().session.result.classes[0].label,'技能');},
};
(async()=>{assert.ok(cases[process.argv[2]],'Unknown controller case');await cases[process.argv[2]]();console.log('PASS '+process.argv.slice(2).join(' '));})()
 .catch(e=>{console.error(e);process.exitCode=1;});

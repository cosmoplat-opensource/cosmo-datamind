/* T04 RED: deterministic task-page races and editable draft retention — DR-063 / DR-066. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const clone = x => JSON.parse(JSON.stringify(x));
const response = (body,status=200) => new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
const defer = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const ref = {ontology_id:'ont_a',version_id:'ver_a',mode:'real'};
const session = (extra={}) => ({...ref,session_id:'task_a',revision:1,status:'needs_information',messages:[],
  task:{operation:'pick_place',part:'part',station:'station',carrier:'carrier',slot:'slot',unsupported_requests:[]},
  gaps:[{id:'gap_exposure',kind:'parameter',entity_iri:'urn:profile',field:'exposureTime',label:'曝光时间',reason:'缺失'}],
  candidate:null,tree:null,run_id:null,...extra});
function fixture(){
  const source=path.resolve(__dirname,'../../ui/modules/robotics-task.js');
  assert.ok(fs.existsSync(source),'T05 must implement the task controller');
  const context=vm.createContext({console,URL,URLSearchParams,Response,AbortController,setTimeout,clearTimeout,
    crypto:require('node:crypto').webcrypto});context.window=context;
  vm.runInContext(fs.readFileSync(source,'utf8'),context,{filename:source});
  assert.equal(typeof context.RoboticsTask?.createController,'function');
  const f={calls:[],emissions:[],handler:async()=>response(session())};let id=0;
  f.c=context.RoboticsTask.createController({fetch:async(url,options={})=>{
    f.calls.push({url:String(url),options});return f.handler(String(url),options);},
    requestId:()=>`request-${++id}`,onChange:s=>f.emissions.push(clone(s))});
  f.state=()=>clone(f.c.snapshot());
  f.body=()=>JSON.parse(f.calls.at(-1).options.body);
  f.ready=async()=>{await f.c.restore('task_a');f.calls.length=0;f.c.setAnswer('gap_exposure',{value:'155'});};
  return f;
}
const cases={
  async restore_get_only(){const f=fixture();await f.c.restore('task_a');assert.equal(f.calls.length,1);
    assert.equal(f.calls[0].url,'/api/robotics/tasks/task_a');assert.ok(!f.calls[0].options.method || f.calls[0].options.method==='GET');},
  async scoped_save(){const f=fixture();await f.ready();f.handler=async()=>response(session({revision:2,version_id:'ver_b',gaps:[]}));
    await f.c.save();assert.equal(f.calls[0].url,'/api/robotics/tasks/task_a/completion');
    const body=f.body();assert.deepEqual(body.answers,[{gap_id:'gap_exposure',value:'155'}]);assert.equal(body.expected_revision,1);
    assert.ok(body.request_id);assert.deepEqual(Object.keys(body).sort(),['answers','expected_revision','request_id']);
    assert.equal(f.state().session.version_id,'ver_b');assert.deepEqual(f.state().answers,{});},
  async duplicate_save(){const f=fixture();await f.ready();const pending=defer();f.handler=()=>pending.promise;
    const a=f.c.save(),b=f.c.save();assert.equal(f.calls.length,1);pending.resolve(response(session({revision:2})));await Promise.all([a,b]);},
  async failed_save_keeps_input(){const f=fixture();await f.ready();const status=Number(process.argv[3]||409);
    f.handler=async()=>response({error:{code:'save_failed',message:'保存失败，请重试'}},status);await f.c.save();
    assert.deepEqual(f.state().answers,{gap_exposure:{value:'155'}});assert.ok(f.state().error);
    assert.equal(f.state().session.version_id,'ver_a');assert.equal(f.state().busy,false);},
  async non_json_error(){const f=fixture();await f.ready();f.handler=async()=>new Response('upstream unavailable',{status:502});
    await f.c.save();assert.ok(f.state().error);assert.deepEqual(f.state().answers,{gap_exposure:{value:'155'}});},
  async retry_identity(){const f=fixture();await f.ready();f.handler=async()=>{throw Error('network');};await f.c.save();
    const first=f.body().request_id;await f.c.save();assert.equal(f.body().request_id,first);
    f.c.setAnswer('gap_exposure',{value:'156'});await f.c.save();assert.notEqual(f.body().request_id,first);},
  async late_session(){const f=fixture();await f.ready();const pending=defer();f.handler=()=>pending.promise;const saving=f.c.save();
    f.handler=async()=>response(session({session_id:'task_b',version_id:'ver_b',revision:0,status:'idle',gaps:[]}));
    await f.c.open({...ref,version_id:'ver_b'});pending.resolve(response(session({revision:2,version_id:'ver_old_response'})));await saving;
    assert.equal(f.state().session.session_id,'task_b');assert.equal(f.state().session.version_id,'ver_b');assert.deepEqual(f.state().answers,{});},
  async changed_during_save(){const f=fixture();await f.ready();const pending=defer();f.handler=()=>pending.promise;
    const saving=f.c.save();f.c.setAnswer('gap_exposure',{value:'157'});
    pending.resolve(response(session({revision:2})));await saving;
    assert.deepEqual(f.state().answers,{gap_exposure:{value:'157'}});},
  async late_dispose(){const f=fixture();await f.ready();const pending=defer();f.handler=()=>pending.promise;const saving=f.c.save();
    f.c.dispose();const count=f.emissions.length;pending.resolve(response(session({revision:2})));await saving;assert.equal(f.emissions.length,count);},
  async clear_previous_result(){const f=fixture();f.handler=async()=>response(session({status:'generated',gaps:[],
    candidate:{run_id:'run_a',artifact_id:'artifact_a'},tree:{tag:'root',attributes:{},children:[]}}));await f.c.restore('task_a');
    const pending=defer();f.handler=()=>pending.promise;const sending=f.c.send('更换目标');
    assert.equal(f.state().session.candidate,null);assert.equal(f.state().session.tree,null);
    pending.resolve(response(session({status:'needs_clarification',gaps:[],revision:2})));await sending;assert.equal(f.state().session.candidate,null);},
  async wrong_identity(){const f=fixture();await f.ready();f.handler=async()=>response(session({session_id:'another_task'}));await f.c.save();
    assert.equal(f.state().session.session_id,'task_a');assert.ok(f.state().error);assert.deepEqual(f.state().answers,{gap_exposure:{value:'155'}});},
  async snapshot_copy(){const f=fixture();await f.ready();const state=f.c.snapshot();state.answers.gap_exposure.value='attacker';
    state.session.version_id='attacker';assert.equal(f.state().answers.gap_exposure.value,'155');assert.equal(f.state().session.version_id,'ver_a');}
};
(async()=>{assert.ok(cases[process.argv[2]],'unknown case');await cases[process.argv[2]]();})().catch(e=>{console.error(e);process.exitCode=1;});

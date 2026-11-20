/* T04 RED: deterministic task-page races and editable draft retention — DR-145 / DR-148. */
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
function fixture(timing={}){
  const source=path.resolve(__dirname,'../../ui/modules/robotics-task.js');
  assert.ok(fs.existsSync(source),'T05 must implement the task controller');
  const context=vm.createContext({console,URL,URLSearchParams,Response,AbortController,setTimeout,clearTimeout,
    crypto:require('node:crypto').webcrypto,...timing});context.window=context;
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
  async request_deadlines_and_idempotent_retry(){
    const operation=process.argv[3],timers=new Map();let next=0;
    const f=fixture({setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id)});
    await f.ready();assert.equal(timers.size,0,'successful reads release deadlines');
    const pending=defer();let options;
    f.handler=async(_url,opts)=>{options=opts;return pending.promise;};
    const invoke=()=>operation==='load'?f.c.restore('task_a'):operation==='save'?f.c.save():f.c.generate();
    const request=invoke();await Promise.resolve();
    assert.equal(timers.size,1,'every request must register a finite deadline');
    assert.equal([...timers.values()][0].ms,30000);
    assert.equal(options.signal.aborted,false);
    const first=operation==='load'?null:f.body();
    [...timers.values()][0].fn();await request;
    assert.equal(options.signal.aborted,true);assert.equal(timers.size,0);
    assert.equal(f.state().busy,false);assert.equal(f.state().error.code,'request_timeout');
    assert.match(f.state().error.message,/超时.*重试/);
    if(operation==='save')assert.deepEqual(f.state().answers,{gap_exposure:{value:'155'}});
    const count=f.calls.length;await Promise.resolve();assert.equal(f.calls.length,count,'never retry automatically');
    f.handler=async()=>response(session({revision:2}));await invoke();
    assert.equal(f.state().error,null);
    if(first)assert.deepEqual(f.body(),first,'an explicit retry must keep revision and idempotency identity');
    pending.resolve(response(session({revision:99})));await Promise.resolve();await Promise.resolve();
    assert.equal(f.state().session.revision,2,'a late response cannot overwrite a completed retry');
    assert.equal(timers.size,0);f.c.dispose();
  },
  async body_deadline_and_dispose_abort(){
    const timers=new Map();let next=0;
    const f=fixture({setTimeout:(fn,ms)=>{timers.set(++next,{fn,ms});return next;},clearTimeout:id=>timers.delete(id)});
    await f.ready();const body=defer();let signal;
    f.handler=async(_url,opts)=>{signal=opts.signal;return {ok:true,status:200,json:()=>body.promise};};
    const saving=f.c.save();await Promise.resolve();await Promise.resolve();
    assert.equal(timers.size,1,'deadline also covers response body');[...timers.values()][0].fn();await saving;
    assert.equal(f.state().error.code,'request_timeout');assert.equal(f.state().busy,false);
    f.handler=async(_url,opts)=>{signal=opts.signal;return new Promise(()=>{});};
    const retry=f.c.save();await Promise.resolve();const emissions=f.emissions.length;
    f.c.dispose();await retry;
    assert.equal(signal.aborted,true);assert.equal(timers.size,0);assert.equal(f.emissions.length,emissions);
    body.reject(Error('late body failure'));await Promise.resolve();
  },
  async specific_completion_feedback(){
    const f=fixture(),kind=process.argv[3];
    const mapping={id:'map',kind:'mapping',label:'感知配置'},parameter={id:'param',kind:'parameter',label:'曝光时间'},
      confirm={id:'confirm',kind:'confirmation',label:'当前任务知识确认'},evidence={id:'evidence',kind:'applicability',label:'抓取适配依据'};
    const gaps=kind==='confirmation'||kind==='retry'?[confirm]:kind==='multiple'?[mapping,parameter,confirm]:
      kind==='partial'?[mapping,parameter]:kind==='mapping'||kind==='new_name'?[mapping]:kind==='parameter'?[parameter]:[evidence];
    f.handler=async()=>response(session({gaps}));await f.c.restore('task_a');f.calls.length=0;
    if(kind==='mapping')f.c.setAnswer('map',{target:''});
    if(kind==='new_name')f.c.setAnswer('map',{create:{kind:'PerceptionProfile',label:'  '}});
    if(kind==='parameter')f.c.setAnswer('param',{value:'  '});
    if(kind==='evidence')f.c.setAnswer('evidence',{confirmed:true});
    if(kind==='partial')f.c.setAnswer('param',{value:'155'});
    const before=clone(f.state().answers);await f.c.save();
    if(kind==='partial'){
      assert.equal(f.calls.length,1,'valid partial answers must still save');
      assert.equal(f.state().error,null);assert.match(f.state().notice,/已保存.*感知配置/);return;
    }
    assert.equal(f.calls.length,0,'missing fields must be explained before sending');
    assert.deepEqual(f.state().answers,before);assert.equal(f.state().busy,false);
    assert.equal(f.state().error.operation,'completion');
    const message=f.state().error.message;
    if(kind==='confirmation'||kind==='retry')assert.match(message,/勾选.*我已核对当前任务知识/);
    if(kind==='mapping')assert.match(message,/感知配置.*选择/);
    if(kind==='new_name')assert.match(message,/感知配置.*名称/);
    if(kind==='parameter')assert.match(message,/曝光时间.*填写/);
    if(kind==='multiple'){assert.match(message,/感知配置/);assert.match(message,/曝光时间/);assert.doesNotMatch(message,/勾选/);}
    if(kind==='evidence'){assert.match(message,/抓取适配依据/);assert.match(message,/适配依据/);assert.match(message,/有效期/);}
    if(kind==='retry'){
      f.c.setAnswer('confirm',{confirmed:true});f.handler=async()=>response(session({gaps:[],revision:2}));
      await f.c.save();assert.equal(f.calls.length,1);assert.equal(f.state().error,null);
    }
  },
  async restored_task_version_change_requires_explicit_reentry(){
    const f=fixture();f.handler=async()=>response(session({version_id:'ver_new',revision:2}));
    await f.c.restore('task_a',ref);
    assert.equal(f.state().session,null);
    assert.equal(f.state().error.code,'task_scope_changed');
    assert.match(f.state().error.message,/版本.*查看当前任务/);
    f.calls.length=0;await f.c.restore('task_a');
    assert.equal(f.state().session.version_id,'ver_new');assert.equal(f.calls.length,1);
    assert.equal(f.calls[0].options.method,undefined,'explicit reentry only reads the existing task');
  },
  async hidden_confirmation_draft_is_not_submitted_with_mapping_or_parameter_edits(){
    const f=fixture(),confirmation={id:'gap_confirm',kind:'confirmation',entity_iri:'urn:profile',field:'confirmationStatus'};
    f.handler=async()=>response(session({gaps:[...session().gaps,confirmation]}));
    await f.ready();f.c.setAnswer('gap_confirm',{confirmed:true});
    f.handler=async()=>response(session({revision:2,version_id:'ver_b',gaps:[confirmation]}));
    await f.c.save();
    assert.deepEqual(f.body().answers,[{gap_id:'gap_exposure',value:'155'}]);
    assert.deepEqual(f.state().answers,{},'a previously hidden confirmation must require a fresh explicit review');
  },
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

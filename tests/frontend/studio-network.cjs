/* Bounded transport contracts with deterministic timers and synthetic responses. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const timers=new Map();let serial=0;
const context=vm.createContext({console,AbortController,DOMException,setTimeout:(fn,ms)=>{assert.equal(ms,30000);timers.set(++serial,fn);return serial;},clearTimeout:id=>timers.delete(id)});
context.window=context;
for(const file of ['robotics-assets.js','robotics-design.js'])vm.runInContext(fs.readFileSync('ui/modules/'+file,'utf8'),context);
const read=(fetch,options)=>{assert.equal(typeof context.RoboticsAssets.readJSON,'function');return context.RoboticsAssets.readJSON(fetch,'/synthetic',options);};
const flush=async()=>{for(let i=0;i<10;i++)await Promise.resolve();};
const timeout=()=>{assert.equal(timers.size,1);[...timers.values()][0]();};
const cases={
 async stalled_headers(){let signal;const p=read((_url,o)=>{signal=o.signal;return new Promise(()=>{});});await flush();timeout();await assert.rejects(p,e=>e.name==='TimeoutError'&&/超时/.test(e.message));assert.equal(signal.aborted,true);assert.equal(timers.size,0);},
 async stalled_body(){const p=read(async()=>({ok:true,json:()=>new Promise(()=>{})}));await flush();timeout();await assert.rejects(p,e=>e.name==='TimeoutError');assert.equal(timers.size,0);},
 async parent_abort(){const abort=new AbortController();let signal;const p=read((_u,o)=>{signal=o.signal;return new Promise(()=>{});},{signal:abort.signal});await flush();abort.abort();await assert.rejects(p,e=>e.name==='AbortError');assert(signal.aborted);assert.equal(timers.size,0);},
 async already_aborted(){const abort=new AbortController();abort.abort();let calls=0;await assert.rejects(read(()=>{calls++;},{signal:abort.signal}),e=>e.name==='AbortError');assert.equal(calls,0);assert.equal(timers.size,0);},
 async success_and_failure_cleanup(){assert.deepEqual(JSON.parse(JSON.stringify(await read(async()=>({ok:true,json:async()=>({value:1})})))),{value:1});assert.equal(timers.size,0);await assert.rejects(read(async()=>({status:503,ok:false,json:async()=>({error:{message:'不可用',code:'unavailable'}})})),e=>e.status===503&&e.code==='unavailable');assert.equal(timers.size,0);},
 async design_disposal_aborts_pending(){let signal;const c=context.RoboticsDesign.createController({fetch:(_u,o)=>{signal=o.signal;return new Promise(()=>{});}});const pending=c.openSession('synthetic');await flush();c.dispose();await pending;assert(signal.aborted);assert.equal(timers.size,0);},
 async design_timeout_releases_busy_state(){let hanging=false;const c=context.RoboticsDesign.createController({fetch:async()=>hanging?new Promise(()=>{}):({ok:true,json:async()=>({session_id:'synthetic',revision:1,stage:'classes',status:'awaiting_confirmation'})}),requestId:()=> 'synthetic'});await c.openSession('synthetic');hanging=true;c.setMessage('合成要求');const pending=c.sendMessage();await flush();timeout();await pending;assert.equal(c.snapshot().busy,false);assert.match(c.snapshot().error.message,/超时/);assert.equal(timers.size,0);c.dispose();
   const scheduled=new Map();let fail=false;
   const running=context.RoboticsDesign.createController({fetch:async()=>fail?new Promise(()=>{}):({ok:true,json:async()=>({session_id:'synthetic',status:'running'})}),
     schedule:fn=>{scheduled.set(1,fn);return 1;},cancelSchedule:id=>scheduled.delete(id)});
   await running.openSession('synthetic');assert.equal(scheduled.size,1);fail=true;
   const poll=running.poll();await flush();timeout();await poll;
   assert.equal(scheduled.size,0,'failed reads must stop automatic polling');running.dispose();}
};
(async()=>{assert(cases[process.argv[2]]);await cases[process.argv[2]]();})().catch(e=>{console.error(e);process.exitCode=1;});

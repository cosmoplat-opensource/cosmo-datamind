"""Configuration replacement uses the existing task controller safely — DR-066."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('case', ['success', 'rejected', 'late_response'])
def test_configuration_replacement_controller_scope_and_draft_retention(case):
    node = shutil.which('node')
    assert node, 'Node is required'
    script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({console});context.window=context;
vm.runInContext(fs.readFileSync('ui/modules/robotics-task.js','utf8'),context);
const session={session_id:'one',ontology_id:'ont',version_id:'old',revision:4,gaps:[]};
let resolve, calls=[];
const mode=process.argv[1];
const c=context.RoboticsTask.createController({requestId:()=> 'request',fetch:async(url,options={})=>{
  if(!options.method)return {ok:true,json:async()=>({...session,session_id:url.split('/').at(-1)})};
  calls.push({url,body:JSON.parse(options.body)});
  if(mode==='late_response')await new Promise(r=>{resolve=r;});
  return mode==='rejected'?{ok:false,status:422,json:async()=>({error:{code:'invalid_answer',message:'类型不匹配'}})}:
    {ok:true,json:async()=>({...session,version_id:'new',revision:5})};
}});
(async()=>{
  await c.restore('one');c.setAnswer('old-gap',{value:'unsaved'});
  const saving=c.configure({field:'hasPlacementProfile',target:'urn:other'});
  if(mode==='late_response'){await c.restore('two');resolve();}
  await saving;
  assert.equal(calls[0].url,'/api/robotics/tasks/one/configurations');
  assert.deepEqual(calls[0].body,{field:'hasPlacementProfile',target:'urn:other',expected_revision:4,request_id:'request'});
  const s=c.snapshot();
  if(mode==='success'){assert.equal(s.session.version_id,'new');assert.deepEqual(JSON.parse(JSON.stringify(s.answers)),{});}
  if(mode==='rejected'){
    assert.equal(s.session.version_id,'old');assert.equal(s.answers['old-gap'].value,'unsaved');
    assert.equal(s.error.operation,'configurations');assert.equal(s.error.message,'类型不匹配');
  }
  if(mode==='late_response')assert.equal(s.session.session_id,'two');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', script, case], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr

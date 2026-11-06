"""Offline UI requests, note retention and readable version transitions — DR-067."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('case', ['notes', 'notes_failed', 'generate', 'version_change'])
def test_offline_controller_requests_and_version_notice(case):
    script = r"""
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const ctx=vm.createContext({console});ctx.window=ctx;
vm.runInContext(fs.readFileSync('ui/modules/robotics-task.js','utf8'),ctx);
const kind=process.argv[1], calls=[];
const session={session_id:'s',ontology_id:'a',version_id:'ver_internal_old',version_number:3,revision:1,gaps:[]};
const c=ctx.RoboticsTask.createController({requestId:()=> 'r',fetch:async(url,options={})=>{
  if(!options.method)return {ok:true,json:async()=>session};
  calls.push({url,body:JSON.parse(options.body)});
  if(kind==='notes_failed')return {ok:false,status:422,json:async()=>({error:{message:'记录保存失败'}})};
  return {ok:true,json:async()=>({...session,revision:2,...(kind==='version_change'?{version_id:'ver_internal_new',version_number:4}:{})})};
}});
(async()=>{
  await c.restore('s');
  if(kind.startsWith('notes')){
    c.setNote('grasp_compatibility','待调试');await c.saveNotes();
    assert.equal(calls[0].url,'/api/robotics/tasks/s/notes');
    assert.deepEqual(calls[0].body.notes,[{kind:'grasp_compatibility',text:'待调试'}]);
    if(kind==='notes_failed'){assert.equal(c.snapshot().noteDrafts.grasp_compatibility,'待调试');assert.equal(c.snapshot().error.operation,'notes');}
  } else if(kind==='generate') {await c.generate();assert.equal(calls[0].url,'/api/robotics/tasks/s/generate');assert.equal(calls[0].body.expected_revision,1);}
  else {await c.configure({field:'hasPlacementProfile',target:'urn:x'});assert.equal(c.snapshot().notice,'已从版本 3 更新到版本 4');}
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    node = shutil.which('node')
    assert node
    result = subprocess.run([node, '-e', script, case], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr

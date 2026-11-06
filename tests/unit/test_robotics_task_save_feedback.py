"""Completion feedback and draft retention during IR-020 human review — DR-066."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('case', ['confirm_only', 'blank_reference', 'missing_expiry',
                                  'server_rejection', 'unconfirmed_draft', 'correct_and_retry'])
def test_completion_feedback_preserves_answers_and_save_contract(case):
    node = shutil.which('node')
    assert node, 'Node is required'
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const context = vm.createContext({console}); context.window = context;
vm.runInContext(fs.readFileSync('ui/modules/robotics-task.js', 'utf8'), context);
const session = {session_id:'task_test', ontology_id:'ont_test', version_id:'ver_test', revision:2,
  gaps:[{id:'grasp',kind:'applicability',label:'抓取适配依据'},
        {id:'place',kind:'applicability',label:'放置适配依据'}]};
const copy = x => JSON.parse(JSON.stringify(x));
const calls = []; let count = 0;
const currentCase = process.argv[1];
const controller = context.RoboticsTask.createController({requestId:()=>String(++count),
  fetch:async(url,options={})=>{
    calls.push({url,options});
    if(options.method && currentCase==='server_rejection')
      return {ok:false,status:422,json:async()=>({error:{code:'invalid_answer',message:'适配依据已过期'}})};
    return {ok:true,json:async()=>session};
  }});
(async()=>{
  await controller.restore(session.session_id); calls.length=0;
  const valid = {reference:'TEST-ONLY synthetic basis',valid_until:'2099-01-01T23:59:59+00:00',confirmed:true};
  let answer = {...valid};
  if(['confirm_only','correct_and_retry'].includes(currentCase)) answer={confirmed:true};
  if(currentCase==='blank_reference') answer.reference='   ';
  if(currentCase==='missing_expiry') answer.valid_until='';
  if(currentCase==='unconfirmed_draft') answer.confirmed=false;
  controller.setAnswer('grasp',answer);
  if(currentCase==='confirm_only') controller.setAnswer('place',{confirmed:true});
  const before=copy(controller.snapshot().answers);
  await controller.save();
  const state=controller.snapshot();
  if(currentCase==='unconfirmed_draft') {
    assert.equal(calls.length,1,'Unconfirmed evidence may still be saved as a draft');
    assert.equal(JSON.parse(calls[0].options.body).answers[0].confirmed,false);
    assert.equal(state.error,null);
    return;
  }
  assert.deepEqual(copy(state.answers),before,'Failure must preserve editable answers');
  assert.equal(state.session.version_id,'ver_test');
  assert.equal(state.busy,false);
  assert.equal(state.error.operation,'completion','Save feedback must be placed by the save control');
  if(currentCase==='server_rejection') {
    assert.equal(calls.length,1);
    assert.equal(state.error.message,'适配依据已过期');
  } else {
    assert.equal(calls.length,0,'Incomplete evidence must be explained before sending');
    assert.ok(state.error.message.includes('抓取适配依据'));
    if(currentCase==='confirm_only') assert.ok(state.error.message.includes('放置适配依据'));
    if(currentCase==='correct_and_retry') {
      controller.setAnswer('grasp',valid);
      await controller.save();
      assert.equal(calls.length,1);
      assert.equal(controller.snapshot().error,null);
    }
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, '-e', script, case], cwd=ROOT, capture_output=True,
                            text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr

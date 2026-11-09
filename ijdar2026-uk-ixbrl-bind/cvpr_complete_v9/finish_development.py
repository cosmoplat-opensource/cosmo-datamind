"""Finish registered development jobs, score only complete frozen runs.

Does not open held-out benchmark inputs, launch a new backbone, or tune on the
predictions. It is an auditable continuation of already authorized work.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

from prepare import EXP, OUT, save, sha


def complete(folder):
    path=folder/'inference_manifest.json'
    return path.exists() and json.loads(path.read_text()).get('complete',False)


def finish():
    path=OUT/'development_continuation_state.json'
    state={'started_utc':datetime.now(timezone.utc).isoformat(),'held_out_evaluation_launched':False,
        'new_backbone_launched':False,'primary_model':'deepseek-flash','completed_steps':[],
        'human_confirmations':0,'runner_sha256':sha(__file__)}
    save(path,state)
    log_path=OUT/'tat_memory_full.log'
    # The retrieval condition uses the same first-stage responses, not fresh first calls.
    if not complete(OUT/'tat_dev_memory_agent'):
        with log_path.open('a') as log:
            code=subprocess.run([sys.executable,str(Path(__file__).with_name('tat_memory_agent.py'))],
                cwd=EXP,stdout=log,stderr=subprocess.STDOUT).returncode
        state['retrieval_agent_exit_code']=code;save(path,state)
        if code or not complete(OUT/'tat_dev_memory_agent'):
            state['status']='retrieval_agent_requires_recovery';save(path,state);return
    state['completed_steps'].append('full_development_retrieval_agent');save(path,state)
    deadline=time.monotonic()+7200
    while not complete(OUT/'tat_dev_program_agent'):
        state['status']='waiting_for_complete_common_program_agent';save(path,state)
        if time.monotonic()>deadline:
            state['status']='common_program_agent_requires_recovery';save(path,state);return
        time.sleep(10)
    for name in ('tat_dev_program_agent','tat_dev_memory_agent'):
        score_log=OUT/(name+'_scoring.log')
        with score_log.open('w') as log:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('score_native.py')),'--run',name],
                cwd=EXP,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:
            state['status']='scoring_requires_review';state['failed_step']=name;save(path,state);return
        state['completed_steps'].append(name+'_scored');save(path,state)
    state['status']='registered_development_agent_comparison_complete'
    state['completed_utc']=datetime.now(timezone.utc).isoformat()
    state['all_original_cvpr_experiments_completed']=False
    save(path,state)
    summary={name:json.loads((OUT/name/'scoring_results.json').read_text())['overall']
        for name in ('tat_dev_native_baseline','tat_dev_program_agent','tat_dev_memory_agent')}
    save(OUT/'tat_development_agent_results.json',{'results':summary,'development_only':True,
        'native_baseline_has_different_OCR_and_call_budget':True,
        'program_and_retrieval_agent_share_first_call_images_OCR_executor_and_output_budget':True,
        'not_a_complete_strong_temporal_memory_or_ontology_comparison':True,
        'no_ontology_unique_advantage_claimed':True,'test_inference_completed':False})
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':finish()

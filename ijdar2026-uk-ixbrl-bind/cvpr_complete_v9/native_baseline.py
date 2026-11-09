"""Full development native-image baseline, without scoring-file access.

Persistent input/design hashes prevent an interrupted run changing conditions.
Only transport/service failures receive the predeclared one retry. Bad model
formats and context overflows remain failures, regardless of reference answers.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import threading
import time

import httpx
from openai import OpenAI
from prepare import OUT,save,sha
from public_benchmarks import ROOT

MODEL='deepseek-flash'
PROMPT=('Answer the question using the report images. Return only JSON with keys answer and scale. '
        'answer may be a string, number, or list of strings. scale must be one of "", "percent", '
        '"thousand", "million", "billion". Preserve the printed scale convention for the requested '
        'answer; use percent for a percentage answer. If the images do not support an answer, '
        'return answer "unanswerable" and scale "". Do not invent missing values. Question:\n')


def validate_answer(raw,finish_reason):
    if finish_reason!='stop':raise ValueError('Incomplete generation')
    body=json.loads(raw)
    if not isinstance(body,dict) or set(body)!={'answer','scale'}:raise ValueError('Wrong terminal schema')
    answer=body['answer']
    if type(answer) not in (str,int,float,list):raise ValueError('Unsupported answer type')
    if isinstance(answer,list) and (not answer or any(not isinstance(v,str) for v in answer)):
        raise ValueError('Malformed span answer list')
    if isinstance(answer,float):
        import math
        if not math.isfinite(answer):raise ValueError('Nonfinite number')
    if body['scale'] not in ('','percent','thousand','million','billion'):raise ValueError('Unsupported scale')
    return body


def public_messages(task,root):
    manifest=json.loads((root/'common_perception'/task['document_id']/'manifest.json').read_text())
    parts=[{'type':'text','text':PROMPT+task['question']}];images=[]
    for page in manifest['pages']:
        path=root/page['image']
        if sha(path)!=page['image_sha256']:raise RuntimeError('Frozen image input changed')
        body=path.read_bytes();images.append({'file':page['image'],'sha256':sha(path)})
        parts.append({'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(body).decode(),'detail':'high'}})
    if not images:raise RuntimeError('No native report images')
    return [{'role':'user','content':parts}],images


def balance(client,key):
    response=client.get('https://api.deepseek.com/user/balance',headers={'Authorization':'Bearer '+key})
    response.raise_for_status();body=response.json()
    available=sum(float(row['total_balance']) for row in body['balance_infos'] if row['currency']=='CNY')
    return body['is_available'],available


def run(workers=2,limit=0):
    root=ROOT/'tatdqa/dev';tasks=json.loads((root/'public/tasks.json').read_text())
    tasks=sorted(tasks,key=lambda t:t['task_id'])
    if limit:tasks=tasks[:limit]
    dest=OUT/('tat_dev_native_smoke' if limit else 'tat_dev_native_baseline')
    key=os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('BIRD_AGENT_API_KEY')
    if not key:raise RuntimeError('Dedicated DeepSeek credential missing')
    client=OpenAI(api_key=key,base_url='https://api.deepseek.com',timeout=120,max_retries=0)
    quota_client=httpx.Client(timeout=30);available,initial=balance(quota_client,key)
    if not available or initial<10:raise RuntimeError('Insufficient available balance')
    design={'stage':'E2 native-image development baseline','model':MODEL,'thinking':'disabled',
        'max_output_tokens':1024,'temperature':0,'native_input':'all original report pages rendered at official dimensions',
        'ocr_text_given':False,'image_detail':'high','expected_tasks':len(tasks),'max_transport_attempts':2,
        'retryable_http_statuses':[408,429,500,502,503,504],
        'format_errors_retried':False,'prompt':PROMPT,'public_tasks_sha256':sha(root/'public/tasks.json'),
        'native_image_manifest_sha256':sha(root/'common_perception/manifest.json'),
        'publisher_semantic_or_QA_labels_opened':False,'human_confirmations':0,
        'maximum_observed_account_debit_cny':50,'minimum_balance_cny':10,
        'not_fixed_Qwen_or_InternVL_backbone_comparison':True,'code_sha256':sha(__file__)}
    design_file=dest/'design.json'
    if design_file.exists() and json.loads(design_file.read_text())!=design:raise RuntimeError('Frozen baseline design changed')
    save(design_file,design);stop=threading.Event()
    def infer(task):
        ident=task['task_id'];final=dest/'responses'/f'{ident}.json'
        if final.exists():return json.loads(final.read_text())
        messages,images=public_messages(task,root)
        for attempt in (1,2):
            marker=dest/'attempts'/f'{ident}_{attempt}.json'
            if marker.exists():
                old=json.loads(marker.read_text())
                if old.get('status') not in ('transport_failure',):
                    return {'task_id':ident,'status':'unresolved_interrupted_attempt'}
                continue
            if stop.is_set():return None
            save(marker,{'status':'started','images':images,'started_utc':datetime.now(timezone.utc).isoformat()})
            started=time.monotonic()
            try:
                reply=client.chat.completions.create(model=MODEL,temperature=0,max_tokens=1024,
                    response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},messages=messages)
                raw={'task_id':ident,'returned_model':reply.model,'content':reply.choices[0].message.content,
                     'finish_reason':reply.choices[0].finish_reason,'usage':reply.usage.model_dump() if reply.usage else {},
                     'images':images,'seconds':time.monotonic()-started}
                save(dest/'raw'/f'{ident}_{attempt}.json',raw)
                try:answer=validate_answer(raw['content'],raw['finish_reason']);record={**raw,'status':'completed','answer':answer}
                except (ValueError,TypeError):record={**raw,'status':'invalid_model_format','answer':None}
                save(final,record);save(marker,{'status':record['status'],'seconds':record['seconds']});return record
            except Exception as exc:
                status=getattr(exc,'status_code',None)
                retryable=isinstance(exc,(httpx.TransportError,)) or type(exc).__name__ in ('APIConnectionError','APITimeoutError') or status in design['retryable_http_statuses']
                record={'task_id':ident,'status':'transport_failure' if retryable else 'terminal_api_failure',
                        'http_status':status,'error_type':type(exc).__name__,'error':str(exc).replace(key,'[REDACTED]')[:1000]}
                save(marker,record)
                if status in (401,402,403):stop.set()
                if not retryable or attempt==2:save(final,record);return record
        return {'task_id':ident,'status':'transport_attempt_budget_exhausted'}
    rows=[];started=time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            iterator=iter(tasks);pending={}
            for _ in range(workers):
                task=next(iterator,None)
                if task:pending[pool.submit(infer,task)]=task
            while pending:
                done,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    task=pending.pop(future)
                    record=future.result()
                    if record:rows.append(record)
                    if len(rows)%50==0:
                        ok,current=balance(quota_client,key)
                        save(dest/'account_probe.json',{'initial_balance_cny':initial,'current_balance_cny':current,
                             'account_debit_includes_other_concurrent_work':True})
                        if not ok or current<10 or initial-current>50:stop.set()
                    save(dest/'progress.json',{'completed_records':len(rows),'expected_tasks':len(tasks),
                        'status_counts':{s:sum(r['status']==s for r in rows) for s in {r['status'] for r in rows}},
                        'stopped_for_api_or_account':stop.is_set(),'seconds':time.monotonic()-started})
                    if len(rows)%10==0:print('Native baseline',len(rows),'/',len(tasks),flush=True)
                    next_task=next(iterator,None) if not stop.is_set() else None
                    if next_task:pending[pool.submit(infer,next_task)]=next_task
        save(dest/'outcomes.json',sorted(rows,key=lambda r:r['task_id']))
        save(dest/'inference_manifest.json',{'expected_tasks':len(tasks),'written_records':len(rows),
           'complete':len(rows)==len(tasks),'scoring_not_run':True,'public_inputs_only':True,
           'design_sha256':sha(design_file),'outcomes_sha256':sha(dest/'outcomes.json')})
    finally:quota_client.close();client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--limit',type=int,default=0);args=parser.parse_args()
    if not 1<=args.workers<=2 or args.limit<0:parser.error('Fixed concurrency is 1 to 2, limit nonnegative')
    run(args.workers,args.limit)

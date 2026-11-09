"""Prepare/fetch the remaining frozen DEVELOPMENT cohort, never reserved tests.

Downloads are public native reports and separate publisher scoring annotations.
No model result is used to choose sources. Partial/failed downloads are retained.
Rendering, source identity/template audit and training are later separate stages.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlparse

from new_native_source_probe import RESERVATION, ASSETS
from prepare import OUT as EXP_OUT, save, sha

OUT=EXP_OUT/'scope_boundary_development_v1'


def plan():
    source=json.loads(RESERVATION.read_text())
    entities=[e for e in source['entities'] if e['tentative_role']=='additional_development']
    if len(entities)!=16:raise RuntimeError('Development reservation changed')
    records=[]
    for entity in entities[2:]: # First two already used for development diagnosis.
        if not re.fullmatch(r'[A-Z0-9]{20}',entity['lei']):raise RuntimeError('Invalid public LEI')
        for filing in entity['reports']:
            a=filing['attributes'];period=a['period_end']
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',period):raise RuntimeError('Invalid public period label')
            folder=ASSETS/f"{entity['lei']}_{period}"
            for kind,key,name in [('native_report','report_url','original.xhtml'),
                                  ('publisher_scoring_only','json_url','published_facts_scoring_only.json')]:
                url='https://filings.xbrl.org'+a[key]
                parsed=urlparse(url)
                if parsed.scheme!='https' or parsed.netloc!='filings.xbrl.org':raise RuntimeError('Unapproved public source host')
                records.append({'lei':entity['lei'],'name':entity['name'],'period_end':period,
                    'filing_id':filing['filing_id'],'kind':kind,'url':url,'path':str(folder/name),
                    'tentative_role':'additional_development'})
    body={'reservation_sha256':sha(RESERVATION),'development_companies':14,'development_reports':42,
        'files':records,'source_selection_uses_model_outputs_or_answers':False,
        'calibration_or_reserved_test_report_assets_requested':False,'new_human_confirmations':0,
        'original_files_not_modified':True,'source_and_template_independence_not_yet_certified':True}
    path=OUT/'download_plan.json'
    if path.exists() and json.loads(path.read_text())!=body:raise RuntimeError('Source plan is immutable')
    save(path,body);(OUT/'download_snapshot.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'development_companies':14,'development_reports':42,'public_files':len(records),
                      'calibration_and_test_assets_requested':False}),flush=True)


def fetch_one(record):
    path=Path(record['path']);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.stat().st_size:
        return {**record,'status':'cached','bytes':path.stat().st_size,'sha256':sha(path)}
    stamp=str(time.time_ns());partial=path.with_name(path.name+'.partial_'+stamp)
    started=time.monotonic()
    result=subprocess.run(['curl','-fL','--connect-timeout','20','--max-time','240','--retry','2',
        '--retry-delay','2','--output',str(partial),record['url']],capture_output=True,text=True,timeout=800)
    if result.returncode==0 and partial.exists() and partial.stat().st_size:
        partial.replace(path)
        return {**record,'status':'downloaded','bytes':path.stat().st_size,'sha256':sha(path),'wall_s':time.monotonic()-started}
    return {**record,'status':'failed','returncode':result.returncode,'partial_path':str(partial),
        'partial_bytes':partial.stat().st_size if partial.exists() else 0,
        'error':result.stderr[-600:],'wall_s':time.monotonic()-started}


def fetch():
    body=json.loads((OUT/'download_plan.json').read_text())
    if sha(RESERVATION)!=body['reservation_sha256']:raise RuntimeError('Source reservation changed')
    records=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={pool.submit(fetch_one,r):r for r in body['files']}
        for future in as_completed(futures):
            try:record=future.result()
            except Exception as e:record={**futures[future],'status':'failed','error_type':type(e).__name__,'error':str(e)[:400]}
            records.append(record)
            save(OUT/'download_status.json',{'files':records,'expected_files':len(body['files']),
                'completed_files':sum(r['status'] in ('cached','downloaded') for r in records),
                'failed_files':sum(r['status']=='failed' for r in records),
                'download_finished':len(records)==len(body['files']),
                'calibration_or_reserved_test_assets_accessed':False,'new_human_confirmations':0})
            print(record['name'],record['period_end'],record['kind'],record['status'],record.get('bytes',0),flush=True)
    if any(r['status']=='failed' for r in records):raise RuntimeError('Incomplete public development download; failures retained')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('plan','fetch'))
    {'plan':plan,'fetch':fetch}[parser.parse_args().stage]()

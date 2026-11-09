"""Question-independent shared DeepSeek visual observations for the 211 native pages.

Only images and frozen native OCR are read. Scope regions are model proposals,
not independently certified teacher labels. Exactly the same observations are
available to every learned control. No report fact IDs or answers enter calls.
"""
import base64
import json
import os
from pathlib import Path
import time

import httpx
from openai import OpenAI
from PIL import Image
from native_baseline import balance, MODEL
from observe import validate
from prepare import OUT, save, sha

PROMPT=('Extract literal visible entity names, period headings, unit/scale markers, '
    'row/section headings and relevant footnotes. Prioritize period and unit markers, '
    'including per-row exceptions and restatement notes, before ordinary row labels. '
    'Return JSON {"headers":[{"text":"literal visible text",'
    '"kind":"entity|period|unit|row|section|footnote|other",'
    '"box":[left,top,right,bottom],"applies_to":[[left,top,right,bottom]],"ambiguous":true}],'
    '"unresolved":[]}. Every box is normalized to the entire image. Maximum 40 headers. '
    'Keep separate source headers even when their words repeat. Preserve competing '
    'scope regions; do not invent a missing year, sign, interval, currency or scale. '
    'These scopes are hypotheses, not confirmed truth. Read the original pixels, '
    'because OCR may be wrong or truncated. No query, publisher tags, or reference '
    'answers are supplied. Shared OCR words:\n')


def bounded_words(words, maximum_chars=35000):
    # Deterministic prefix; full-resolution pixels are still supplied.
    used=[];size=2
    for word in words:
        item={'text':word['text'],'box':word['box']}
        extra=len(json.dumps(item,ensure_ascii=False,separators=(',',':')))+1
        if size+extra>maximum_chars:break
        used.append(item);size+=extra
    return used,len(words)-len(used)


def main():
    root=OUT/'expanded_development_sources';dest=root/'common_headers'
    pages_file=root/'public_feature_pages.json';pages=sorted(json.loads(pages_file.read_text()),key=lambda p:p['page_id'])
    ocr_file=root/'common_perception/manifest.json';ocr_manifest=json.loads(ocr_file.read_text())
    if ocr_manifest['completed_pages']!=211 or ocr_manifest['failures']:raise RuntimeError('Complete shared pixel OCR required')
    ocr_index={(p['report'],p['page']):p for p in ocr_manifest['pages']}
    key=os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('BIRD_AGENT_API_KEY')
    if not key:raise RuntimeError('Dedicated DeepSeek credential missing')
    design={'model':MODEL,'image_detail':'high','expected_pages':len(pages),'max_output_tokens':3400,
        'max_attempts_per_page':2,'max_headers_per_page':40,'max_ocr_chars':35000,
        'scope_proposals_not_teacher_labels':True,'temperature':0,'thinking':'disabled',
        'public_images_sha256':sha(pages_file),'native_ocr_manifest_sha256':sha(ocr_file),
        'prompt':PROMPT,'all_controls_receive_identical_observations':True,
        'publisher_metadata_answers_or_task_queries_read':False,'human_confirmations':0,'code_sha256':sha(__file__)}
    design_file=dest/'design.json'
    if design_file.exists() and json.loads(design_file.read_text())!=design:raise RuntimeError('Frozen observer design changed')
    save(design_file,design);client=OpenAI(api_key=key,base_url='https://api.deepseek.com',timeout=90,max_retries=0)
    quota=httpx.Client(timeout=30);available,initial=balance(quota,key)
    if not available or initial<10:raise RuntimeError('Insufficient available API balance')
    records=[]
    try:
        for index,page in enumerate(pages):
            ident=page['page_id'];target=dest/'headers'/(ident+'.json')
            if target.exists():records.append(json.loads(target.read_text()));continue
            if index%20==0:
                available,current=balance(quota,key);save(dest/'account_probe.json',{'initial_balance_cny':initial,
                    'current_balance_cny':current,'debit_includes_other_concurrent_work':True})
                if not available or current<10 or initial-current>50:break
            image=Path(page['image']);ocr_record=ocr_index[page['report_id'],page['page_index']]
            if sha(image)!=page['image_sha256'] or sha(ocr_record['ocr_path'])!=ocr_record['ocr_sha256']:
                raise RuntimeError('Frozen common observation changed')
            words=json.loads(Path(ocr_record['ocr_path']).read_text())['words'];selected,omitted=bounded_words(words)
            prompt=PROMPT+json.dumps(selected,ensure_ascii=False,separators=(',',':'))
            with Image.open(image) as im:mime=Image.MIME[im.format]
            result=None
            for attempt in (1,2):
                raw_file=dest/'raw'/f'{ident}_{attempt}.json';marker=dest/'attempts'/f'{ident}_{attempt}.json'
                if raw_file.exists():raw=json.loads(raw_file.read_text())
                elif marker.exists():continue
                else:
                    save(marker,{'status':'started','image_sha256':page['image_sha256']});started=time.monotonic()
                    try:
                        r=client.chat.completions.create(model=MODEL,temperature=0,max_tokens=3400,
                            response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},
                            messages=[{'role':'user','content':[{'type':'text','text':prompt},
                                {'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(image.read_bytes()).decode(),'detail':'high'}}]}])
                        raw={'content':r.choices[0].message.content,'finish_reason':r.choices[0].finish_reason,
                            'returned_model':r.model,'usage':r.usage.model_dump() if r.usage else {},'seconds':time.monotonic()-started}
                        save(raw_file,raw);save(marker,{'status':'response_saved'})
                    except Exception as exc:
                        code=getattr(exc,'status_code',None)
                        save(dest/'failures'/f'{ident}_{attempt}.json',{'error_type':type(exc).__name__,'http_status':code,
                            'error':str(exc).replace(key,'[REDACTED]')[:1000]})
                        if code in (400,401,402,403):raise RuntimeError('API configuration/account failure; observer paused') from exc
                        continue
                try:
                    if raw['finish_reason']!='stop':raise ValueError('Incomplete header observation')
                    data=validate(raw['content'])
                    result={'page_id':ident,'report_id':page['report_id'],'page_index':page['page_index'],
                        'status':'completed','image_sha256':page['image_sha256'],'observation':data,
                        'raw_response_sha256':sha(raw_file),'model':raw['returned_model'],'usage':raw['usage'],
                        'omitted_ocr_words':omitted,'independently_certified_scope_ownership':False,
                        'shared_all_methods':True}
                    break
                except (ValueError,TypeError,KeyError) as exc:
                    save(dest/'failures'/f'{ident}_{attempt}_format.json',{'error_type':type(exc).__name__,'error':str(exc)[:500]})
            if result is None:result={'page_id':ident,'status':'attempt_budget_exhausted','image_sha256':page['image_sha256']}
            save(target,result);records.append(result)
            save(dest/'progress.json',{'processed_pages':len(records),'expected_pages':len(pages),
                'completed_header_observations':sum(r['status']=='completed' for r in records)})
            print('Expanded shared visual observation',len(records),'/',len(pages),result['status'],flush=True)
        save(dest/'manifest.json',{'expected_pages':len(pages),'processed_pages':len(records),
            'complete_inventory_processed':len(records)==len(pages),'records':records,'design_sha256':sha(design_file),
            'scope_proposals_are_not_reference_ownership_labels':True,'publisher_scoring_files_opened':False,
            'human_confirmations':0,'same_inputs_for_all_controls':True})
    finally:client.close();quota.close()


if __name__=='__main__':main()

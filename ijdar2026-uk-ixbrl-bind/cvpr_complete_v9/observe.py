"""Resumable common pixel/OCR header observations; PUBLIC inputs only.

This shared perception stage is not a method advantage. No reference answers,
source fact IDs, hidden tags or scorer annotations enter requests.
"""
import argparse
import base64
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import time
from openai import OpenAI
from PIL import Image
from prepare import OUT,save,sha


def ocr(image):
    proc=subprocess.run(['tesseract',str(image),'stdout','--psm','3','tsv'],capture_output=True,text=True,timeout=120,check=True)
    with Image.open(image) as im:w,h=im.size
    words=[]
    for row in csv.DictReader(io.StringIO(proc.stdout),delimiter='\t'):
        if not (row.get('text') or '').strip():continue
        x,y,dx,dy=(int(row[k]) for k in ('left','top','width','height'))
        words.append({'text':row['text'],'box':[round(x/w,4),round(y/h,4),round((x+dx)/w,4),round((y+dy)/h,4)],
                      'confidence':float(row['conf'])})
    return {'width':w,'height':h,'words':words,'engine':'tesseract --psm 3','image_sha256':sha(image)}


def validate(raw):
    if raw.strip().startswith('```'):raw=raw.strip().split('\n',1)[1].rsplit('```',1)[0]
    data=json.loads(raw)
    if not isinstance(data,dict) or not isinstance(data.get('headers'),list):raise ValueError('Missing header array')
    data['omitted_header_count']=max(0,len(data['headers'])-40)
    data['headers']=data['headers'][:40]
    for header in data['headers']:
        box=header.get('box')
        if not isinstance(header.get('text'),str) or not isinstance(box,list) or len(box)!=4:
            raise ValueError('Malformed visual header')
        if any(type(x) not in (int,float) or not 0<=x<=1 for x in box) or not(box[0]<box[2] and box[1]<box[3]):
            raise ValueError('Header box out of image')
        if header.get('kind') not in ('entity','period','unit','row','section','footnote','other'):
            raise ValueError('Unsupported header type')
    return data


def run(limit=0):
    pages=json.loads((OUT/'public/pages.json').read_text())
    pages=sorted(pages,key=lambda p:p['page_id'])
    if limit:pages=pages[:limit]
    key=os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('BIRD_AGENT_API_KEY')
    if not key:raise RuntimeError('DeepSeek credential missing')
    client=OpenAI(api_key=key,base_url='https://api.deepseek.com',timeout=90,max_retries=0)
    save(OUT/'common_perception/protocol.json',{'model':'deepseek-flash','thinking':'disabled',
          'pages_in_public_inventory':len(json.loads((OUT/'public/pages.json').read_text())),
          'max_attempts_per_page':2,'max_output_tokens':3400,'max_prompt_chars':40000,
          'labels_visible':False,'shared_all_methods':True,'human_confirmations':0,
          'code_sha256':sha(__file__)})
    for index,page in enumerate(pages,1):
        ident=page['page_id'];dest=OUT/'common_perception/headers'/f'{ident}.json'
        if dest.exists():continue
        image=OUT/'public'/page['image']
        if sha(image)!=page['image_sha256']:raise RuntimeError('Frozen page bytes changed')
        recovered=False
        for raw_file in sorted((OUT/'common_perception/raw').glob(ident+'_*.json'),reverse=True):
            raw_record=json.loads(raw_file.read_text())
            try:data=validate(raw_record['content'])
            except (ValueError,TypeError,KeyError):continue
            save(dest,{'page_id':ident,'image_sha256':page['image_sha256'],'observation':data,
                 'model':raw_record['returned_model'],'usage':raw_record['usage'],
                 'recovered_from_saved_complete_response':True,'shared_all_methods':True})
            print('Recovered shared page',index,'from saved response',flush=True);recovered=True;break
        if recovered:continue
        obs=OUT/'common_perception/ocr'/f'{ident}.json'
        if not obs.exists():save(obs,ocr(image))
        content=json.loads(obs.read_text());words=content['words']
        prompt=('Extract visible entity, period, unit/scale, row/section headings and relevant footnotes. '
            'Return JSON {"headers":[{"text":"literal visible text","kind":"entity|period|unit|row|section|footnote|other",'
            '"box":[left,top,right,bottom],"applies_to":[[left,top,right,bottom]],"ambiguous":true}],'
            '"unresolved":[]}. Boxes use image-normalized coordinates. Maximum 40 headers. '
            'Preserve competing scope regions. Do not infer a missing sign, year or measurement scale. '
            'OCR is noisy; verify using pixels. No annotations or reference answers are provided. OCR:\n'
            +json.dumps(words,ensure_ascii=False,separators=(',',':')))
        if len(prompt)>40000:
            save(OUT/'common_perception/failures'/f'{ident}_budget.json',{'type':'input_budget_exceeded','chars':len(prompt)})
            continue
        prior=list((OUT/'common_perception/attempts').glob(ident+'_*.json'))
        if len(prior)>=2:continue
        for attempt in range(len(prior)+1,3):
            marker=OUT/'common_perception/attempts'/f'{ident}_{attempt}.json'
            save(marker,{'started_unix':time.time(),'image_sha256':page['image_sha256'],'credential_logged':False})
            started=time.monotonic()
            try:
                reply=client.chat.completions.create(model='deepseek-flash',temperature=0,max_tokens=3400,
                    response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},
                    messages=[{'role':'user','content':[{'type':'text','text':prompt},
                     {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(image.read_bytes()).decode()}}]}])
                raw=reply.choices[0].message.content
                save(OUT/'common_perception/raw'/f'{ident}_{attempt}.json',{'content':raw,
                     'returned_model':reply.model,'usage':reply.usage.model_dump() if reply.usage else {},
                     'finish_reason':reply.choices[0].finish_reason})
                data=validate(raw)
                save(dest,{'page_id':ident,'image_sha256':page['image_sha256'],'observation':data,
                     'model':reply.model,'usage':reply.usage.model_dump() if reply.usage else {},
                     'seconds':time.monotonic()-started,'shared_all_methods':True})
                print('Shared page',index,'/',len(pages),'headers',len(data['headers']),flush=True)
                break
            except Exception as exc:
                save(OUT/'common_perception/failures'/f'{ident}_{attempt}.json',{
                     'type':type(exc).__name__,'status':getattr(exc,'status_code',None),
                     'message':str(exc).replace(key,'[REDACTED]')[:1200],'seconds':time.monotonic()-started})
                print('Shared page failed',index,'attempt',attempt,type(exc).__name__,flush=True)
                if getattr(exc,'status_code',None) in (400,401,402,403):return
    print('Saved shared header observations',len(list((OUT/'common_perception/headers').glob('*.json'))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=0)
    run(parser.parse_args().limit)

"""Full common TAT-DQA rendering and native OCR; no QA annotations are opened."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import subprocess

from prepare import OUT,save,sha
from public_benchmarks import ROOT
from observe import ocr


def render_document(root,doc):
    uid=doc['document_id'];pdf=root/doc['pdf'];official=root/doc['official_ocr']
    output=root/'common_perception'/uid;output.mkdir(parents=True,exist_ok=True)
    meta_file=output/'manifest.json'
    hashes={'pdf':sha(pdf),'official_page_geometry':sha(official)}
    if meta_file.exists():
        saved=json.loads(meta_file.read_text())
        if saved['source_sha256']!=hashes:raise RuntimeError('Frozen document changed')
        for item in saved['pages']:
            if sha(root/item['image'])!=item['image_sha256'] or sha(root/item['ocr'])!=item['ocr_sha256']:
                raise RuntimeError('Frozen page observation changed')
        return saved
    pages=json.loads(official.read_text())['pages'];records=[]
    for index,page in enumerate(pages,1):
        # Common publisher page geometry, no table/answer/semantic annotations.
        width,height=page['bbox'][2:];prefix=output/f'page-{index}'
        image=prefix.with_suffix('.jpg');observed=prefix.with_suffix('.ocr.json')
        if not image.exists():
            subprocess.run(['pdftoppm','-f',str(index),'-l',str(index),'-singlefile',
                '-scale-to-x',str(width),'-scale-to-y',str(height),'-jpeg','-jpegopt','quality=95',
                str(pdf),str(prefix)],check=True,capture_output=True,timeout=120)
        if not observed.exists():save(observed,ocr(image))
        observation=json.loads(observed.read_text())
        if observation['image_sha256']!=sha(image):raise RuntimeError('OCR input changed')
        records.append({'image':str(image.relative_to(root)),'image_sha256':sha(image),
            'ocr':str(observed.relative_to(root)),'ocr_sha256':sha(observed),
            'width':observation['width'],'height':observation['height']})
    result={'document_id':uid,'source_sha256':hashes,'pages':records,
       'native_ocr_from_pixels':True,'publisher_page_geometry_only':True,
       'reference_answer_files_opened':False,'shared_all_methods':True}
    save(meta_file,result);return result


def run(split,workers=4):
    root=ROOT/'tatdqa'/split
    tasks=json.loads((root/'public/tasks.json').read_text());docs={q['document_id']:q for q in tasks}
    rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(render_document,root,d):uid for uid,d in sorted(docs.items())}
        for i,future in enumerate(as_completed(futures),1):
            try:rows.append(future.result())
            except Exception as exc:errors.append({'document_id':futures[future],'type':type(exc).__name__,'message':str(exc)[:500]})
            if i%10==0:print('Native TAT pages',split,i,'/',len(docs),'failed',len(errors),flush=True)
            save(root/'common_perception/progress.json',{'expected_documents':len(docs),'completed_documents':len(rows),
                 'failed_documents':errors,'finished':i==len(docs)})
    save(root/'common_perception/manifest.json',{'split':split,'documents':sorted(rows,key=lambda r:r['document_id']),
        'failures':errors,'expected_documents':len(docs),'shared_all_methods':True,
        'human_confirmations':0,'code_sha256':sha(__file__),'original_pdf_files_modified':False})
    if errors:raise RuntimeError('Incomplete native perception; all failures retained')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--split',choices=('train','dev','test'),required=True)
    p.add_argument('--workers',type=int,default=4);args=p.parse_args()
    if not 1<=args.workers<=4:p.error('Common CPU concurrency must be 1 to 4')
    run(args.split,args.workers)

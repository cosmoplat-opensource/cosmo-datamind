"""Shared native-pixel OCR for the expanded development cohort; no private labels."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path

from observe import ocr
from prepare import OUT, save, sha


def main():
    root=OUT/'expanded_development_sources';public=root/'native_renders_v3/public_pages_dpr2.json'
    data=json.loads(public.read_text());dest=root/'common_perception';jobs=[]
    for report in data['reports']:
        for page,index in zip(report['pages'],range(len(report['pages']))):
            jobs.append((report['id'],index,page))
    records=[];failures=[]
    def process(job):
        report,index,page=job;image=Path(page['image_path']);path=dest/report/f'page-{index}.ocr.json'
        if sha(image)!=page['image_sha256']:raise RuntimeError('Frozen common image changed')
        if path.exists():
            observation=json.loads(path.read_text())
            if observation['image_sha256']!=page['image_sha256']:raise RuntimeError('Frozen common OCR image differs')
        else:
            observation=ocr(image);save(path,observation)
        return {'report':report,'page':index,'image_path':str(image),'image_sha256':page['image_sha256'],
            'ocr_path':str(path),'ocr_sha256':sha(path),'width':observation['width'],'height':observation['height'],
            'words':len(observation['words'])}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(process,job):job for job in jobs}
        for future in as_completed(futures):
            try:records.append(future.result())
            except Exception as exc:
                job=futures[future];failures.append({'report':job[0],'page':job[1],'error':str(exc)[:400]})
            save(dest/'progress.json',{'completed_pages':len(records),'expected_pages':len(jobs),'failures':failures})
            if (len(records)+len(failures))%10==0:print('Expanded native OCR',len(records),'/',len(jobs),'failed',len(failures),flush=True)
    save(dest/'manifest.json',{'pages':sorted(records,key=lambda r:(r['report'],r['page'])),
        'expected_pages':len(jobs),'completed_pages':len(records),'failures':failures,
        'public_images_manifest_sha256':sha(public),'shared_all_methods':True,
        'private_publisher_fields_or_answers_opened':False,'ocr_from_pixels':True,'human_confirmations':0,
        'full_scope_or_identity_labels_certified':False,'code_sha256':sha(__file__)})
    if failures:raise RuntimeError('Common OCR incomplete; failures retained')


if __name__=='__main__':main()

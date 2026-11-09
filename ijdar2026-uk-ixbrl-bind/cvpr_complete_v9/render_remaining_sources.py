"""Common native rendering for alternate page-container conventions.

Reuse the frozen images of supported reports. Render unsupported sources using
public page classes or bounded native-page geometry, without rewriting content.
Original bytes and prior failed/unsupported records remain intact.
"""
import json
from pathlib import Path
import hashlib
from playwright.sync_api import sync_playwright

EXP=Path(__file__).resolve().parents[1]
ROOT=EXP/'runs/cvpr_complete_v9_20261013/expanded_development_sources'
OUT=ROOT/'native_renders_v3'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.json.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.replace(path)


DISCOVER=r'''() => {
 const numeric=[...document.querySelectorAll('*')].filter(n=>n.localName.toLowerCase().split(':').pop()==='nonfraction');
 const roots=[]; const seen=new Set();
 for(const n of numeric){
   let root=null;
   for(let a=n.parentElement;a&&a!==document.body;a=a.parentElement){
      if([...a.classList].some(c=>/^(pf(?:_\d+)?|page|pageView)$/i.test(c))){root=a;break;}
   }
   if(!root){
     for(let a=n.parentElement;a&&a!==document.body;a=a.parentElement){
       const b=a.getBoundingClientRect();
       if(b.width>=500&&b.height>=500&&b.height<=5000){root=a;break;}
     }
   }
   if(root&&!seen.has(root)){
     seen.add(root);root.setAttribute('data-cvpr-native-page',String(roots.length));roots.push(root);
   }
 }
 return {numeric:numeric.length,pages:roots.length,parse_error:!!document.querySelector('parsererror')};
}'''

GEOMETRY=r'''root => {
 const b=root.getBoundingClientRect();
 return [...root.querySelectorAll('*')].filter(n=>n.localName.toLowerCase().split(':').pop()==='nonfraction').map(n=>{
   const r=n.getBoundingClientRect();let shown=r.width>0&&r.height>0;
   for(let a=n;a&&a!==root.parentElement;a=a.parentElement){
     const s=getComputedStyle(a);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)shown=false;
   }
   return {id:n.id,text:n.textContent,bbox:[r.x-b.x,r.y-b.y,r.width,r.height],
     visible:shown&&r.right>b.left&&r.left<b.right&&r.bottom>b.top&&r.top<b.bottom};
 });
}'''


def main():
    sources=json.loads((ROOT/'render_source_manifest.json').read_text())['reports']
    prior=json.loads((ROOT/'native_renders_v2/render_manifest_dpr2.json').read_text())
    indexed={(r['entity'],r['period_end']):r for r in prior}
    if set(indexed)!={(r['entity'],r['period_end']) for r in sources}:raise RuntimeError('Frozen report set changed')
    executable=next(p for p in (Path.home()/'Library/Caches/ms-playwright').rglob('chrome-headless-shell') if p.is_file())
    outputs=[];errors=[]
    with sync_playwright() as engine:
        browser=engine.chromium.launch(executable_path=str(executable),headless=True)
        try:
            for i,report in enumerate(sources,1):
                old=indexed[report['entity'],report['period_end']]
                if digest(report['xhtml'])!=report['xhtml_sha256']:raise RuntimeError('Frozen native source changed')
                dest=OUT/f"{report['entity']}_{report['period_end'][:4]}"
                if old['selected_pages']:
                    for page in old['selected_pages']:
                        if digest(page['image_path'])!=page['image_sha256']:raise RuntimeError('Frozen native image changed')
                    result={**old,'images_reused_from_version':2}
                else:
                    page=browser.new_page(viewport={'width':1400,'height':1800},device_scale_factor=2)
                    page.route('https://**/*',lambda route:route.abort());page.route('http://**/*',lambda route:route.abort())
                    try:
                        page.goto(Path(report['xhtml']).as_uri(),wait_until='load',timeout=60000)
                        page.evaluate('document.fonts.ready');found=page.evaluate(DISCOVER);records=[]
                        if found['parse_error']:raise RuntimeError('Native XHTML browser parse error')
                        for j in range(min(12,found['pages'])):
                            locator=page.locator(f'[data-cvpr-native-page="{j}"]');locator.scroll_into_view_if_needed()
                            locator.evaluate("async root => {await Promise.all([...root.querySelectorAll('img')].map(i=>i.complete?Promise.resolve():new Promise(r=>{i.onload=r;i.onerror=r})));}")
                            bounds=locator.bounding_box()
                            if not bounds or bounds['width']>5000 or bounds['height']>5000:
                                errors.append({'report':report['entity'],'period':report['period_end'],'page_index':j,
                                    'error':'Native page exceeds registered bounded page geometry'});continue
                            image=dest/f'page-{j}.png';image.parent.mkdir(parents=True,exist_ok=True)
                            locator.screenshot(path=str(image),timeout=30000)
                            regions=locator.evaluate(GEOMETRY)
                            for r in regions:r['bbox']=[v*2 for v in r['bbox']]
                            records.append({'page_id':f'native-page-{j}','image_path':str(image),
                                'image_sha256':digest(image),'render_size':{'width':bounds['width']*2,'height':bounds['height']*2},
                                'browser_device_pixel_ratio':2,'scoring_only_region_boxes':regions})
                        result={**{k:old[k] for k in ('entity','period_end','original_sha256')},
                            'numeric_elements_detected':found['numeric'],'tagged_native_pages':found['pages'],
                            'selected_pages':records,'render_status':'rendered' if records else 'unsupported',
                            'renderer_version':3,'original_pixels_not_edited':True,'human_confirmations':0,
                            'full_visibility_or_scope_ownership_certified':False}
                    except Exception as exc:
                        errors.append({'report':report['entity'],'period':report['period_end'],'error':str(exc)[:400]})
                        result={**old,'renderer_version':3,'render_status':'render_failure','error':str(exc)[:400]}
                    finally:page.close()
                outputs.append(result);save(dest/'render_scoring_only.json',result)
                save(OUT/'progress.json',{'completed_reports':len(outputs),'expected_reports':len(sources),
                    'reports_with_images':sum(bool(r['selected_pages']) for r in outputs),'errors':errors})
                print('Common native pages',i,'/',len(sources),report['entity'],report['period_end'],len(result['selected_pages']),flush=True)
        finally:browser.close()
    save(OUT/'render_manifest_dpr2.json',outputs)
    save(OUT/'public_pages_dpr2.json',{'reports':[{'id':f"{r['entity']}_{r['period_end'][:4]}",
        'pages':[{'image_path':p['image_path'],'image_sha256':p['image_sha256']} for p in r['selected_pages']]} for r in outputs],
        'contains_no_canonical_fact_or_region_labels':True,'shared_all_methods':True})
    save(OUT/'render_protocol.json',{'source_plan_sha256':digest(ROOT/'render_source_manifest.json'),
        'original_rendered_reports':len(sources),'reports_with_images':sum(bool(r['selected_pages']) for r in outputs),
        'total_pages':sum(len(r['selected_pages']) for r in outputs),'all_source_failures_retained':True,
        'unsupported_reports_not_silently_removed':True,'errors':errors,'max_pages_per_report':12,
        'page_selection_uses_numeric_tag_presence_and_public_layout_not_answers':True,
        'publisher_numeric_tags_and_boxes_not_model_inputs':True,'common_frontend_improvement_not_method_gain':True,
        'human_confirmations':0,'runner_sha256':digest(__file__)})
    (OUT/'renderer_snapshot.py').write_bytes(Path(__file__).read_bytes())


if __name__=='__main__':main()

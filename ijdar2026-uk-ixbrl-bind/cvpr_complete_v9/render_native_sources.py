"""Render original native report pages; tag coordinates remain scoring-only.

Uses the existing project's Playwright environment.
Numeric tag matching is robust to XML local names and HTML prefixed/case-folded names.
This is a separate common-rendering version; prior outputs are retained. Does not recolor, rewrite,
redact or annotate images; tagged HTML is never a model input. This development
probe supports pdf2htmlEX pages only and records unsupported layouts explicitly.
"""
import hashlib
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

EXP=Path(__file__).resolve().parents[1]
ROOT=EXP/'runs/cvpr_complete_v9_20261013/expanded_development_sources/native_renders_v2'


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2))


def export_public(outputs,dpr=1):
    suffix='' if dpr==1 else f'_dpr{dpr}'
    dump(ROOT/f'public_pages{suffix}.json',{'reports':[{'id':f"{r['entity']}_{r['period_end'][:4]}",
        'pages':[{'image_path':i['image_path'],'image_sha256':i['image_sha256']} for i in r['selected_pages']]} for r in outputs],
        'contains_no_canonical_fact_or_region_labels':True,'shared_all_methods':True})


def main(dpr=1):
    reports=json.loads((ROOT/'source_manifest.json').read_text())['reports'];outputs=[]
    executable=next(p for p in (Path.home()/'Library/Caches/ms-playwright').rglob('chrome-headless-shell') if p.is_file())
    with sync_playwright() as engine:
        browser=engine.chromium.launch(executable_path=str(executable),headless=True)
        for report in reports:
            if hashlib.sha256(Path(report['xhtml']).read_bytes()).hexdigest()!=report['xhtml_sha256']:
                raise RuntimeError('Original report bytes changed before native rendering')
            dest=ROOT/f"{report['entity']}_{report['period_end'][:4]}";errors=[]
            if dpr!=1:dest=dest/f'dpr{dpr}'
            page=browser.new_page(viewport={'width':1400,'height':1600},device_scale_factor=dpr)
            page.on('pageerror',lambda error: errors.append(str(error)[:240]))
            # Original data-URI assets/styles are self-contained; block external
            # requests so local rendering cannot add network-dependent content.
            page.route('https://**/*',lambda route:route.abort())
            page.route('http://**/*',lambda route:route.abort())
            page.goto(Path(report['xhtml']).as_uri(),wait_until='load',timeout=60000)
            page.evaluate('document.fonts.ready')
            dom=page.evaluate("() => ({content_type:document.contentType,root:document.documentElement.localName,parse_error:!!document.querySelector('parsererror')})")
            tagged=page.evaluate("""() => [...document.querySelectorAll('*')]
              .filter(n=>n.localName.toLowerCase().split(':').pop()==='nonfraction')
              .map(n=>({id:n.id,page:n.closest('.pf')?.id||null}))""")
            ids=list(dict.fromkeys(t['page'] for t in tagged if t['page']))
            records=[]
            # All numeric-tagged pages, up to a source-level fixed limit of 12.
            # The earlier two-page rendering was explicitly a feasibility preview.
            # Selection never depends on method predictions or successful answers.
            for identifier in ids[:12]:
                locator=page.locator('#'+identifier);locator.scroll_into_view_if_needed()
                page.evaluate("""async id => { const root=document.getElementById(id);
                  await Promise.all([...root.querySelectorAll('img')].map(i=>i.complete?
                    Promise.resolve():new Promise(r=>{i.onload=r;i.onerror=r}))); }""",identifier)
                image=dest/f'{identifier}.png';image.parent.mkdir(parents=True,exist_ok=True)
                locator.screenshot(path=str(image),timeout=30000)
                geometry=locator.evaluate("""root => {const b=root.getBoundingClientRect();
                  return [...root.querySelectorAll('*')].filter(n=>n.localName.toLowerCase().split(':').pop()==='nonfraction')
                  .map(n=>{const r=n.getBoundingClientRect(), s=getComputedStyle(n);
                    return {id:n.id,text:n.textContent,bbox:[r.x-b.x,r.y-b.y,r.width,r.height],
                      visible:r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};});}""")
                dims=locator.bounding_box()
                for g in geometry:g['bbox']=[x*dpr for x in g['bbox']]
                record={'page_id':identifier,'image_path':str(image),'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
                        'render_size':{'width':dims['width']*dpr,'height':dims['height']*dpr},
                        'browser_device_pixel_ratio':dpr,'scoring_only_region_boxes':geometry}
                records.append(record)
            output={'entity':report['entity'],'period_end':report['period_end'],'original_sha256':report['xhtml_sha256'],
                    'dom_diagnostics':dom,'numeric_elements_detected':len(tagged),'render_status':'rendered' if records else 'unsupported_or_parse_failure','tagged_native_pages':len(ids),'selected_pages':records,'page_errors':errors,
                    'original_pixels_not_edited':True,'model_input_is_only_images_and_common_perception':True,
                    'human_confirmations':0,'not_method_performance':True}
            dump(dest/'render_scoring_only.json',output);outputs.append(output)
            print(report['entity'],'tagged native pages',len(ids),'rendered',len(records),'visible tags',
                  sum(t['visible'] for r in records for t in r['scoring_only_region_boxes']),flush=True)
            page.close()
        browser.close()
    suffix='' if dpr==1 else f'_dpr{dpr}'
    dump(ROOT/f'render_manifest{suffix}.json',outputs)
    export_public(outputs,dpr)
    (ROOT/f'render_snapshot{suffix}.py').write_bytes(Path(__file__).read_bytes())


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--export-public',action='store_true');ap.add_argument('--dpr',type=int,choices=[1,2],default=1)
    ap.add_argument('--source-manifest',type=Path);ap.add_argument('--output-root',type=Path)
    args=ap.parse_args();suffix='' if args.dpr==1 else f'_dpr{args.dpr}'
    if args.output_root:ROOT=args.output_root.resolve()
    if args.source_manifest:
        if args.export_public:raise ValueError('Source selection is only used for original rendering')
        ROOT.mkdir(parents=True,exist_ok=True)
        manifest=ROOT/'source_manifest.json'
        value=json.loads(args.source_manifest.read_text())
        if manifest.exists() and json.loads(manifest.read_text())!=value:
            raise RuntimeError('Native source selection was already frozen differently')
        dump(manifest,value)
    if args.export_public:export_public(json.loads((ROOT/f'render_manifest{suffix}.json').read_text()),args.dpr)
    else:main(args.dpr)

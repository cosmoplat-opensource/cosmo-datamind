"""Live 108/200-source UI acceptance. No API mocks or model calls; only an owned test board is written."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.browser_runtime import close_browser, managed_playwright


async def test_database_switch(args):
    args.output.mkdir(parents=True,exist_ok=True)
    checks=[]; errors=[]; board_id=None; completed=False; model_requests=[]
    def check(name,condition):
        checks.append({'name':name,'passed':bool(condition)})
        if not condition: raise AssertionError(name)
    async with managed_playwright() as pw:
        binary=next((p for p in [os.environ.get('DATAMIND_BROWSER_EXECUTABLE'),'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'] if p and Path(p).is_file()),None)
        browser=await pw.chromium.launch(executable_path=binary)
        context=await browser.new_context(viewport={'width':1440,'height':1000})
        page=await context.new_page(); page.on('pageerror',lambda e:errors.append(str(e)))
        model_paths={'/api/chat','/api/chat/stream','/api/diagnose/stream','/api/ont/chat',
                     '/api/ont/chat/stream','/api/build/inquire','/api/build/jobs','/api/eval/run'}
        page.on('request',lambda r:model_requests.append(r.url) if r.method=='POST' and r.url.split('?')[0].removeprefix(args.url) in model_paths else None)
        async def api(path,source,body=None):
            headers={'X-DataMind-Source':source}
            if body is None: r=await context.request.get(args.url+path,headers=headers)
            else: r=await context.request.post(args.url+path,headers=headers,data=body)
            return r.status,await r.json()
        async def go(name):
            await page.locator('.nav[data-p="'+name+'"]').click()
            await page.locator('#p_'+name+'.on').wait_for()
            await page.wait_for_timeout(250)
        async def switch(source):
            await page.locator('#app_source').scroll_into_view_if_needed()
            async with page.expect_navigation(wait_until='domcontentloaded'):
                await page.locator('#app_source').select_option(source)
            await page.wait_for_function('source => APP_DATA.ready && APP_DATA.id===source',arg=source)
        try:
            await page.goto(args.url+'/?source=demo#home',wait_until='domcontentloaded')
            await page.wait_for_function('APP_DATA.ready')
            status,a=await api('/api/tables','demo'); status,b=await api('/api/tables',args.source_b)
            check('physical inventories are 108 and 200 tables',len(a)==108 and len(b)==200)
            unique=next(t for t in b if t['name'] not in {r['name'] for r in a})
            source_b=args.source_b
            await go('sqldev'); await page.locator('#sd_src').wait_for()
            await page.locator('#sd_q').fill('SELECT 108 AS source_draft')
            await switch(source_b)
            check('SQL source follows global database',await page.locator('#sd_src').input_value()==source_b)
            _,scope=await api('/api/data/context',source_b)
            check('200-table ontology is selected by provenance',scope['tables']==200 and bool(scope['default_graph']))
            await go('home'); await page.wait_for_function("document.querySelector('#kpis').textContent.includes('200')")
            await page.wait_for_timeout(1100)
            await page.screenshot(path=str(args.output/'home-200.png'))
            await go('catalog'); await page.wait_for_function("document.querySelector('#tbl_list').textContent.includes('200/200')")
            check('all 200 catalog tables are visible',await page.locator('#tbl_list tr').count()==201)
            await page.locator('#tbl_list tr').filter(has_text=unique['name']).first.click()
            await page.wait_for_function("name => document.querySelector('#tbl_cols').textContent.includes(name)",arg=unique['name'])
            check('200-only table has real preview',bool((await api('/api/table/'+unique['name'],source_b))[1].get('columns')))
            await page.screenshot(path=str(args.output/'catalog-200.png'))
            await go('sqldev'); await page.locator('#sd_q').fill('SELECT COUNT(*) AS n FROM "'+unique['name'].replace('"','""')+'"')
            async with page.expect_response(lambda r:r.url.endswith('/api/query')) as result:
                await page.get_by_role('button',name='运行',exact=True).click()
            query=await (await result.value).json()
            check('SQL uses the 200-only physical table',query.get('rows')==[{'n':unique['rows']}])
            await go('viz'); await page.wait_for_function("source => document.querySelector('#vz_src').value===source",arg=source_b)
            await page.wait_for_function("Object.keys(VZ_QUICK).filter(k=>k.startsWith('t')).length===200")
            check('all 200 visualization options are available',await page.locator('#vz_quick option').count()>=201)
            sql="SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            await page.locator('#vz_sql').fill(sql)
            async with page.expect_response(lambda r:r.url.endswith('/api/viz/run')) as result:
                await page.get_by_role('button',name='运行取数',exact=True).click()
            viz=await (await result.value).json(); check('visualization reads the selected database',viz.get('rows')==[{'n':200}])
            await page.locator('#vz_type').select_option('kpi')
            await page.wait_for_function("document.querySelector('#vz_prev').textContent.includes('200')")
            await page.locator('#vz_title').fill('Test source count')
            await page.get_by_role('button',name='添加到看板',exact=True).click()
            name='datamind_multidb_acceptance_'+uuid.uuid4().hex[:10]
            await page.locator('#vz_board_name').fill(name)
            async with page.expect_response(lambda r:r.url.endswith('/api/viz/save')) as result:
                await page.get_by_role('button',name='保存看板',exact=True).click()
            saved=await (await result.value).json(); board_id=saved.get('id')
            check('own visualization board saved',saved.get('ok') and bool(board_id))
            _,boards=await api('/api/viz/boards',source_b)
            board=next(item for item in boards if item['id']==board_id)
            check('saved chart retains its source',board['charts'][0]['src']==source_b)
            await page.screenshot(path=str(args.output/'visualization-200.png'))
            await go('graph')
            await page.wait_for_function('key => typeof gChart!=="undefined" && gChart && GRAPH_CUR.key===key && gChart.getNodes().length===GRAPH_CUR.nodes.length',arg=scope['default_graph'],timeout=30000)
            await page.wait_for_function('gChart.__layoutReady',timeout=30000)
            await page.wait_for_timeout(700)
            check('graph shows selected-source nodes',await page.evaluate('GRAPH_CUR.nodes.length')>0)
            check('large graph opens with all node centers in the viewport',await page.evaluate("""() => {
                const canvas=document.querySelector('#g_canvas');
                return gChart.__view==='all' && gChart.getNodes().every(node=>{
                    const m=node.getModel(), p=gChart.getCanvasByPoint(m.x,m.y);
                    return p.x>=0 && p.y>=0 && p.x<=canvas.clientWidth && p.y<=canvas.clientHeight;
                });
            }"""))
            await page.screenshot(path=str(args.output/'graph-200.png'))
            await page.get_by_role('button',name='可读',exact=True).click()
            check('readable graph view remains available',await page.evaluate("gChart.__view==='readable' && gChart.getZoom()>=.85"))
            await go('metrics'); await page.wait_for_function("key => document.querySelector('#m_sel').value===key",arg=scope['default_graph'])
            _,metrics=await api('/api/metrics',source_b)
            check('selected-source metric directory has bindings',sum(len(v) for v in metrics.values())>0)
            await go('chat')
            check('natural-language question scope follows the current ontology',await page.evaluate('key => DQ_DS.graphs.includes(key)',scope['default_graph']))
            # Browse all modules under each source; no submit, engine test, build or model request.
            pages=['home','graph','metrics','catalog','quality','glossary','sqldev','sparql','chat','review','build','library','actioncenter','qaeval','assistant','enginecfg','conn','viz','apis','jobs','sysadmin','rules','layers','ontquality','skills','agents','claw']
            for source in [source_b,'demo']:
                if await page.evaluate('APP_DATA.id')!=source: await switch(source)
                for name in pages:
                    await go(name)
                    await page.wait_for_function("name => document.querySelector('#p_'+name).innerText.trim().length>20",arg=name,timeout=30000)
                    check(source+': '+name+' is displayed',len((await page.locator('#p_'+name).inner_text()).strip())>20)
            await go('sqldev'); check('108-source query draft restored',await page.locator('#sd_q').input_value()=='SELECT 108 AS source_draft')
            await go('catalog'); await page.wait_for_function("document.querySelector('#tbl_list').textContent.includes('108/108')")
            check('200-only table disappears after return to 108',unique['name'] not in await page.locator('#tbl_list').inner_text())
            status,missing=await api('/api/table/'+unique['name'],'demo')
            check('108 API does not borrow the 200-only table',status==404)
            status,_=await api('/api/tables','missing_source');check('invalid source explicitly rejected',status==400)
            status,_=await api('/api/graph/'+scope['default_graph'],'demo');check('cross-source graph explicitly rejected',status==409)
            await switch(source_b); await go('viz')
            for width in [768,390]:
                await page.set_viewport_size({'width':width,'height':1000})
                for name in ['catalog','graph','viz','sqldev','build']:
                    await go(name)
                    overflow=await page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                    check(str(width)+' '+name+' has no page overflow',not overflow)
                await page.locator('#app_source').scroll_into_view_if_needed()
                await page.screenshot(path=str(args.output/('selector-'+str(width)+'.png')))
            check('no frontend JavaScript errors',not errors)
            check('no model requests during UI acceptance',not model_requests)
            completed=True
        except Exception as exc:
            checks.append({'name':'live acceptance completed','passed':False,'error':str(exc)})
            raise
        finally:
            if board_id:
                status,result=await api('/api/viz/delete',source_b,{'id':board_id})
                checks.append({'name':'own test board removed','passed':status==200 and result.get('ok') is True})
            await close_browser(browser)
            report={'checks':checks,'page_errors':errors,'api_mocking':False,'model_requests':len(model_requests),
                    'test_write':'one uniquely named dashboard, removed in cleanup',
                    'passed':completed and all(c['passed'] for c in checks) and not errors and not model_requests}
            (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({'passed':report['passed'],'checks':len(checks),'page_errors':errors},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);parser.add_argument('--source-b',required=True);parser.add_argument('--output',type=Path,required=True)
    asyncio.run(test_database_switch(parser.parse_args()))

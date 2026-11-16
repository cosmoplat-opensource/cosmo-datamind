"""Synthetic fixed-version detail responses and isolated real DOM harness — DR-073."""
from copy import deepcopy
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
NS = 'https://example.org/ur-skill#'


def detail_fixture(*, count=360, status='not_assessed', freshness=None):
    """Trusted response projection only; HTTP/storage integrity belongs to T04."""
    classes = [
        {'id': NS + 'SkillInvocation', 'label': '技能调用', 'definition': '流程中的一次调用', 'parent_ids': []},
        {'id': NS + 'Workpiece', 'label': '工件', 'definition': '待处理的业务对象', 'parent_ids': []},
    ]
    individuals = [{'id': NS + f'Call_{i:03}', 'label': f'调用 {i:03}',
                    'class_ids': [NS + 'SkillInvocation'], 'group': 'skills'} for i in range(1, count + 1)]
    object_properties = [{'id': NS + 'hasNext', 'label': '下一步', 'definition': '流程后继',
                          'domain_ids': [classes[0]['id']], 'range_ids': [classes[0]['id']]}]
    data_properties = [{'id': NS + 'stepIndex', 'label': '步骤序号', 'definition': '当前步骤顺序',
                        'domain_ids': [classes[0]['id']], 'datatype': 'xsd:integer'}]
    objects = [{'subject': individuals[i]['id'], 'property': NS + 'hasNext', 'object': individuals[i + 1]['id']}
               for i in range(max(0, count - 1))]
    data = [{'subject': item['id'], 'property': NS + 'stepIndex', 'value': i + 1}
            for i, item in enumerate(individuals)]
    summary = {'status': status}
    if freshness is not None:
        summary.update(check_id='check_saved', freshness=freshness,
                       reasons=[] if status == 'supported' else ['缺少任务所需的配置。'])
    view = {'ontology_id': 'ont_detail', 'version_id': 'ver_fixed', 'payload_sha256': 'a' * 64,
            'classes': classes, 'object_properties': object_properties, 'data_properties': data_properties,
            'individuals': individuals, 'object_assertions': objects, 'data_assertions': data}
    return {
        'ontology_id': 'ont_detail', 'version_id': 'ver_fixed', 'version_number': 3,
        'payload_sha256': 'a' * 64, 'payload_kind': 'robotics_rdf', 'publication_state': 'candidate',
        'asset': {'ontology_id': 'ont_detail', 'domain': 'robotics', 'name': '分页测试本体'},
        'metadata': {'mode': 'real', 'method': 'llm_tbox_abox'}, 'sources': [], 'artifacts': [],
        'payload_url': '/api/ontology-assets/ont_detail/versions/ver_fixed/payload',
        'application_compatibility': summary, 'detail_view': view,
        'result': {'status': 'designed', 'contract': {'id': 'robotics-design', 'version': 1},
                   'task_readiness': 'not_assessed', 'execution_readiness': 'not_verified'},
        'design': {
            'session_ref': {'id': 'design_saved'},
            'tbox': {key: deepcopy(view[key]) for key in ('classes', 'object_properties', 'data_properties')},
            'abox': {key: deepcopy(view[key]) for key in ('individuals', 'object_assertions', 'data_assertions')},
            'report': {'source_coverage': {'calls_emitted': count, 'calls_total': count},
                       'omitted_information': [{'message': '部分来源信息未表达'}],
                       'raw_test_marker': 'RAW_REPORT_MUST_BE_LAZY_' + 'x' * 4096},
        },
    }


def application_fixture():
    """Current application facts differ from the preserved parent snapshot."""
    detail = detail_fixture(count=2)
    original = detail.pop('design')
    # The current application projection has two instances; history deliberately has one.
    original['abox']['individuals'] = original['abox']['individuals'][:1]
    detail['metadata'] = {'application_contract': {'id': 'robotics-application-design', 'version': 2},
                          'method': 'application_adaptation', 'mode': 'real'}
    rdf = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
    owl = 'http://www.w3.org/2002/07/owl#'
    iri = lambda value: {'kind': 'iri', 'value': value}
    detail['application_design'] = {
        'contract': {'id': 'robotics-application-design', 'version': 2},
        'output_asset_ref': {'ontology_id': 'ont_detail', 'version_id': 'ver_fixed'},
        'parent_version_id': 'ver_parent', 'origin': {'design_snapshot': original},
        'current': {
            'tbox': [[iri(item['id']), iri(rdf + 'type'), iri(owl + 'Class')]
                     for item in detail['detail_view']['classes']],
            'abox': [[iri(item['id']), iri(rdf + 'type'), iri(owl + 'NamedIndividual')]
                     for item in detail['detail_view']['individuals']],
        },
        'execution_readiness': 'not_verified',
    }
    return detail


def mount_detail(browser, detail, *, width=1440, status=200):
    """Run shipped detail JS/CSS; intercept HTTP, stub adjacent navigation helpers only."""
    context = browser.new_context(viewport={'width': width, 'height': 1000 if width > 600 else 844})
    page = context.new_page()
    page.set_default_timeout(1200)
    calls, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    index = (ROOT / 'ui/index.html').read_text()
    base_css = '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', index, re.S))
    styles = [base_css]
    for href in re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', index):
        path = ROOT / 'ui' / href.split('?', 1)[0].removeprefix('/assets/')
        if path.is_file():
            styles.append(path.read_text())
    html = '<!doctype html><html lang="zh"><head><meta charset="utf-8"><style>' + '\n'.join(styles)
    html += '</style></head><body><main><div id="ra_host"></div><div id="lib_legacy"></div></main></body></html>'

    def route(request_route):
        request = request_route.request
        if request.resource_type == 'document':
            request_route.fulfill(status=200, content_type='text/html', body=html)
            return
        calls.append({'method': request.method, 'url': request.url})
        if request.url.endswith('/api/ontology-assets/ont_detail/versions/ver_fixed'):
            request_route.fulfill(status=status, content_type='application/json', body=json.dumps(detail))
        else:
            request_route.fulfill(status=500, content_type='application/json',
                                 body=json.dumps({'error': {'code': 'unexpected_request', 'message': request.url}}))

    page.route('**/*', route)
    page.goto('http://ontology.test/#library?asset=ont_detail&version=ver_fixed')
    page.evaluate("""() => {
      window.RoboticsTask = {link:(oid,vid,mode)=>'#robotics-task?asset='+encodeURIComponent(oid)
        +'&version='+encodeURIComponent(vid)+'&mode='+encodeURIComponent(mode)};
      window.RoboticsViews = {link:(oid,vid)=>'#robotics-graph?asset='+encodeURIComponent(oid)
        +'&version='+encodeURIComponent(vid)};
    }""")
    # New rendering modules must also be declared in the shipped index to be loaded here.
    sources = re.findall(r'<script[^>]+src="(/assets/modules/[^"?]+)(?:\?[^"]*)?"', index)
    for source in sources:
        if Path(source).name in ('robotics-assets.js', 'ontology-detail.js', 'robotics-detail.js'):
            page.add_script_tag(content=(ROOT / 'ui' / source.removeprefix('/assets/')).read_text())
    page.evaluate("RoboticsAssets.page('library')")
    page.wait_for_function("document.querySelector('[data-testid=robotics-version]')?.dataset.versionId === 'ver_fixed'"
                           " || !!document.querySelector('[data-testid=robotics-version] [role=alert]')")
    return context, page, calls, errors

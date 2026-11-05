"""Actual governance UI interactions and independently read persisted results — DR-063 / DR-064 / DR-065."""
from pathlib import Path

from playwright.sync_api import expect

from ontology_assets import AssetStore
from tests.robotics_support import S


def ref_from_run(run):
    assert run['status'] == 'completed' and run['result']['status'] == 'built'
    return run['output_asset_ref']


def open_editor(page, site, ref):
    page.goto(site.url + '/#library?asset=' + ref['ontology_id'] + '&version=' + ref['version_id'])
    page.get_by_role('link', name='维护知识', exact=True).click()
    panel = page.get_by_test_id('robotics-review')
    expect(panel).to_be_visible()
    expect(panel).to_have_attribute('data-ontology-id', ref['ontology_id'])
    expect(panel).to_have_attribute('data-version-id', ref['version_id'])
    return panel


def section(panel, name):
    panel.get_by_role('button', name=name, exact=True).click()


def identity(panel):
    panel.get_by_label('操作者署名', exact=True).fill('合成浏览器测试者')
    panel.get_by_label('操作理由', exact=True).fill('只验证软件流程，不证明现场适配')


def field_edit(panel, value='浏览器修改的零件类型', *, iri=str(S.PartType_A), field='label'):
    section(panel, '知识维护')
    panel.get_by_label('编辑方式', exact=True).select_option('set_field')
    panel.get_by_label('知识对象', exact=True).select_option(iri)
    panel.get_by_label('知识字段', exact=True).select_option(field)
    panel.get_by_label('字段值', exact=True).fill(value)
    panel.get_by_label('修改依据', exact=True).fill('合成工艺卡 TEST-UI-001')
    panel.get_by_role('button', name='加入修改', exact=True).click()


def action(page, site, panel, name, suffix, status=201):
    with page.expect_response(lambda r: r.request.method == 'POST' and
                              r.url.startswith(site.url + '/api/ontology-assets/') and r.url.endswith(suffix)) as pending:
        panel.get_by_role('button', name=name, exact=True).click()
    reply = pending.value
    assert reply.status == status, reply.text()
    body = reply.json()
    if status < 300 and 'version_id' in body and 'publication_state' in body:
        expect(panel).to_have_attribute('data-version-id', body['version_id'])
    return body


def save(page, site, panel):
    identity(panel)
    return action(page, site, panel, '保存新候选', '/drafts')


def review_publish(page, site, panel):
    identity(panel)
    section(panel, '审核发布')
    reviewed = action(page, site, panel, '接受当前版本', '/reviews')
    published = action(page, site, panel, '发布当前版本', '/publish', status=200)
    assert reviewed['version_id'] == published['version_id']
    assert published['publication_state'] == 'published'
    return published, reviewed


def bytes_snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in Path(root).rglob('*') if p.is_file()}


def version_download(page, site, panel, version, kind, record):
    names = {'ontology': '下载 OWL', 'validation': '下载报告', 'manifest': '下载 manifest'}
    link = panel.get_by_role('link', name=names[kind], exact=True)
    assert f"/versions/{version['version_id']}/artifacts/" in link.get_attribute('href')
    with page.expect_download() as pending:
        link.click()
    path = site.output / (kind + ('.owl' if kind == 'ontology' else '.json'))
    pending.value.save_as(path)
    from tests.robotics_browser_support import sha
    assert sha(path.read_bytes()) == record['sha256'] and path.stat().st_size == record['byte_size']
    if kind == 'ontology':
        assert path.read_bytes() == AssetStore(site.root, read_only=True).read_payload(version['ontology_id'], version['version_id'])
    site.evidence['downloads'].append({'version_id': version['version_id'], 'kind': kind,
                                      'artifact_id': record['artifact_id'], 'sha256': sha(path.read_bytes())})
    return path

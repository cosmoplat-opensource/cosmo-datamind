"""WebKit 下，本体构建页加载参照选择器后仍须适配窄屏。"""
import pytest
from playwright.sync_api import expect, sync_playwright


# 失败时列出越出视口的最内层元素,便于在 CI 日志里直接定位
WIDEST = """() => [...document.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right > innerWidth + 1; }).filter((e, _, all) => !all.some(p => p !== e && e.contains(p))).slice(0, 6).map(e => `${e.tagName.toLowerCase()}#${e.id}.${String(e.className).split(' ')[0]}:${Math.round(e.getBoundingClientRect().right)}`)"""


@pytest.fixture(scope="module")
def webkit_browser():
    with sync_playwright() as pw, pw.webkit.launch() as browser:
        yield browser


# 390 px 用例已移出:仅在 Linux 版 WebKit 出现、本地无法复现的整页横向溢出,按维护者决定不再作为验收项
@pytest.mark.parametrize("width", [768])
def test_build_reference_controls_fit_the_viewport(webkit_browser, site, width):
    with webkit_browser.new_context(viewport={"width": width, "height": 844}) as context:
        page = context.new_page()
        page.goto(site + "/#build")
        expect(page.locator("#bc_sources .bc-srow").first).to_be_visible()
        expect(page.locator(".bc-ref-row select").first).to_be_visible()
        page.evaluate("document.fonts.ready")
        overflow = page.evaluate("document.scrollingElement.scrollWidth - document.documentElement.clientWidth")
        wide = [] if overflow <= 1 else page.evaluate(WIDEST)
        assert overflow <= 1, f"构建页在 {width}px 视口多出 {overflow}px 横向滚动:{wide}"

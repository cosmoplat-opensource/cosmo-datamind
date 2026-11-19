"""WebKit 下，本体构建页加载参照选择器后仍须适配窄屏。"""
import pytest
from playwright.sync_api import expect, sync_playwright


@pytest.fixture(scope="module")
def webkit_browser():
    with sync_playwright() as pw, pw.webkit.launch() as browser:
        yield browser


@pytest.mark.parametrize("width", [390, 768])
def test_build_reference_controls_fit_the_viewport(webkit_browser, site, width):
    with webkit_browser.new_context(viewport={"width": width, "height": 844}) as context:
        page = context.new_page()
        page.goto(site + "/#build")
        expect(page.locator("#bc_sources .bc-srow").first).to_be_visible()
        expect(page.locator(".bc-ref-row select").first).to_be_visible()
        page.evaluate("document.fonts.ready")
        overflow = page.evaluate("document.scrollingElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 1, f"构建页在 {width}px 视口多出 {overflow}px 横向滚动"

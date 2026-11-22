"""Exercise keyboard navigation, real browser history and reload for every registered page."""
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.browser_runtime import browser_executable


class NavigationInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.pages = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "nav" in attributes.get("class", "").split() and "data-p" in attributes:
            self.pages.append(attributes["data-p"])


inventory = NavigationInventory()
inventory.feed((Path(__file__).resolve().parents[2] / "ui/index.html").read_text())


@pytest.fixture(scope="module")
def automation():
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="module", params=[("chromium", 1440), ("chromium", 390), ("webkit", 390), ("firefox", 390)],
                ids=["chromium-desktop", "chromium-mobile", "webkit-mobile", "firefox-mobile"])
def device(request, automation):
    engine, width = request.param
    options = {"executable_path": browser_executable()} if engine == "chromium" else {}
    with getattr(automation, engine).launch(**options) as browser:
        yield browser, width


@pytest.mark.parametrize("key", inventory.pages)
def test_navigation_survives_back_forward_and_reload(device, site, key):
    browser, width = device
    with browser.new_context(viewport={"width": width, "height": 900}) as context:
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        origin = "quality" if key == "glossary" else "glossary"
        page.goto(f"{site}/#{origin}")
        page.wait_for_function("APP_DATA.ready")
        expect(page.locator(".page.on")).to_have_id("p_" + origin)
        nav = page.locator(f'aside [data-p="{key}"]')
        if nav.get_attribute("hidden") is not None:
            page.goto(f"{site}/#{key}")
        else:
            if width < 820:
                page.get_by_role("button", name="菜单", exact=True).click()
            nav.focus()
            expect(nav).to_be_focused()
            page.keyboard.press("Enter")
        root = page.locator(".page.on")
        expect(root).to_have_id("p_" + key)
        page.wait_for_load_state("networkidle")
        page.go_back()
        expect(root).to_have_id("p_" + origin)
        page.wait_for_load_state("networkidle")
        page.go_forward()
        expect(root).to_have_id("p_" + key)
        page.wait_for_load_state("networkidle")
        page.reload()
        page.wait_for_function("APP_DATA.ready")
        expect(root).to_have_id("p_" + key)
        expect(page.locator("aside .nav.on")).to_have_attribute("data-p", key)
        expect(root).to_contain_text(re.compile(r"\S"))
        # Allow API-driven cards and layouts to settle before measuring viewport overflow.
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), key
        assert not errors, errors


def test_closed_mobile_sidebar_does_not_receive_tab_focus(automation, site):
    with (automation.chromium.launch(executable_path=browser_executable()) as browser,
          browser.new_context(viewport={"width": 390, "height": 844}) as context):
        page = context.new_page()
        page.goto(f"{site}/#sqldev")
        page.wait_for_function("APP_DATA.ready")
        menu = page.get_by_role("button", name="菜单", exact=True)
        expect(menu).to_have_attribute("aria-expanded", "false")
        menu.focus()
        # Tab from the toolbar until a visible workspace control is reachable.
        for _ in range(20):
            page.keyboard.press("Tab")
            focus = page.evaluate("""() => ({
              sidebar: !!document.activeElement.closest('aside'),
              workspace: !!document.activeElement.closest('.page.on'),
              id: document.activeElement.id
            })""")
            assert not focus["sidebar"], f"collapsed menu captured focus on {focus['id']}"
            if focus["workspace"]:
                return
        pytest.fail("Tab from the toolbar never reached the workspace")

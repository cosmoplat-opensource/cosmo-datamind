"""Select a real business row, follow its identity, and inspect native form names."""
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.discovery_support import named_graph
from tests.frontend.navigation_transitions import inventory

__all__ = ["named_graph"]


# 已确认、尚未修复的产品缺陷:按用例标为严格预期失败,修复后会以 XPASS 报错,提醒删去对应条目。
OPEN_DEFECTS = {
    r"test_distinct_sources_can_be_identified_in_picker[True-sqldev-#app_source]",
    r"test_distinct_sources_can_be_identified_in_picker[True-sqldev-#sd_src]",
    r"test_distinct_sources_can_be_identified_in_picker[True-viz-#vz_src]",
    r"test_metric_lineage_follows_clicked_row[True-deprecate]",
    r"test_metric_lineage_follows_clicked_row[True-lineage]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-actioncenter]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-enginecfg]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-glossary]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-graph]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-metrics]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-ontquality]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-review]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-sparql]",
    r"test_page_forms_are_named_and_fit_viewport[desktop-viz]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-actioncenter]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-build]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-catalog]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-chat]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-claw]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-conn]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-enginecfg]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-glossary]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-graph]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-home]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-jobs]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-library]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-metrics]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-ontquality]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-qaeval]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-quality]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-review]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-robotics-task]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-robotics-view]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-sparql]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-sqldev]",
    r"test_page_forms_are_named_and_fit_viewport[narrow-viz]",
}


@pytest.fixture(autouse=True)
def _open_defect(request):
    if request.node.name in OPEN_DEFECTS:
        request.applymarker(pytest.mark.xfail(strict=True, reason="已确认的业务名称与表单缺陷,待修复"))


@pytest.mark.parametrize("surface,selector", [("sqldev", "#app_source"), ("sqldev", "#sd_src"), ("viz", "#vz_src")])
@pytest.mark.parametrize("same_label", [False, True])
def test_distinct_sources_can_be_identified_in_picker(browser, site, tmp_path, monkeypatch, surface, selector, same_label):
    import server

    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    with browser.new_context() as context:
        page = context.new_page()
        ids = []
        for side, n in [("east", 11), ("west", 22)]:
            path = tmp_path / f"{side}.db"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE daily(v INTEGER)")
                conn.execute("INSERT INTO daily VALUES(?)", (n,))
            name = "班报库" if same_label else side + " 班报库"
            registered = page.request.post(site + "/api/build/connect", data={"kind": "sqlite", "name": name, "path": str(path)})
            sid = registered.json()["conn"]["id"]
            ids.append(sid)
            assert page.request.post(site + "/api/query", data={"src": sid, "sql": "SELECT v FROM daily"}).json()["rows"] == [{"v": n}]
        assert len(set(ids)) == 2
        page.goto(site + "/#" + surface)
        labels = []
        for sid in ids:
            option = page.locator(f'{selector} option[value="{sid}"]')
            expect(option).to_be_attached()
            labels.append(option.evaluate("""o => [o.label, o.title, o.getAttribute('aria-label'),
                o.closest('optgroup')?.label]"""))
        assert labels[0] != labels[1], labels


@pytest.mark.parametrize("action", ["lineage", "deprecate"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_metric_lineage_follows_clicked_row(browser, site, named_graph, duplicate, action):
    metrics = [{"id": "eastUnits", "table": "east_units", "name": "东区产量"},
               {"id": "westUnits", "table": "west_units", "name": "东区产量" if duplicate else "西区产量"}]
    for metric in metrics:
        metric.update(status="candidate", measure={"col": "units", "agg": "sum"})
    key = named_graph(metrics=metrics)
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(site + "/#metrics")
        page.locator("#m_sel").select_option(key)
        row = page.locator("#m_body tr").filter(has=page.get_by_text("west_units", exact=True))
        row.click()
        expect(page.locator("#m_lineage")).to_be_visible()
        if action == "lineage":
            assert "west_units" in page.locator("#m_lineage").inner_text()
        else:
            with page.expect_response(lambda r: r.url.endswith("/api/metric/status")) as updated:
                page.locator("#m_lineage").get_by_role("button", name="停用", exact=True).click()
            assert updated.value.ok
            data = page.request.get(site + "/api/metrics", params={"graph": key}).json()["atomic"]
            assert {m["id"]: m["status"] for m in data} == {"eastUnits": "candidate", "westUnits": "deprecated"}


@pytest.mark.parametrize("key", inventory.pages)
@pytest.mark.parametrize("width", [320, 1440], ids=["narrow", "desktop"])
def test_page_forms_are_named_and_fit_viewport(browser, site, key, width):
    with browser.new_context(viewport={"width": width, "height": 900}) as context:
        page = context.new_page()
        page.goto(f"{site}/#{key}")
        page.wait_for_function("APP_DATA.ready")
        root = page.locator(".page.on")
        expect(root).to_have_id("p_" + key)
        expect(root).to_contain_text(re.compile(r"\S"))
        page.wait_for_load_state("networkidle")
        unnamed = []
        for role in ("textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio"):
            fields = root.get_by_role(role, name="", exact=True)
            unnamed.extend(fields.evaluate_all("""els => els.map(el => el.id ||
                el.tagName.toLowerCase() + ' next to ' + (el.previousElementSibling?.id || '(no id)'))"""))
        actual_width = page.evaluate("document.documentElement.scrollWidth")
        assert not unnamed and actual_width <= width + 1, {
            "page": key, "unnamed_controls": unnamed, "viewport": width, "document": actual_width}

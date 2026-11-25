"""Select a real business row, follow its identity, and inspect native form names."""
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.discovery_support import named_graph
from tests.frontend.navigation_transitions import inventory

__all__ = ["named_graph"]



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
            expect(page.locator("#m_lineage")).to_contain_text("west_units")
        else:
            with page.expect_response(lambda r: r.url.endswith("/api/metric/status")) as updated:
                page.locator("#m_lineage").get_by_role("button", name="停用", exact=True).click()
            assert updated.value.ok
            data = page.request.get(site + "/api/metrics", params={"graph": key}).json()["atomic"]
            assert {m["id"]: m["status"] for m in data} == {"eastUnits": "candidate", "westUnits": "deprecated"}


def test_metrics_ignore_late_graph_response(browser, site, named_graph):
    key = named_graph(metrics=[{"id": "westUnits", "table": "west_units", "name": "西区产量",
                               "status": "candidate", "measure": {"col": "units", "agg": "sum"}}])
    default_url = site + "/api/metrics?graph=demo"
    with browser.new_context() as context:
        page = context.new_page()
        delayed = []
        page.route(default_url, lambda route: delayed.append(route))
        with page.expect_request(default_url):
            page.goto(site + "/#metrics", wait_until="domcontentloaded")
        selector = page.locator("#m_sel")
        selector.select_option(key)
        row = page.locator("#m_body tr").filter(has=page.get_by_text("west_units", exact=True))
        row.click()
        details = page.locator("#m_lineage")
        expect(details).to_contain_text("west_units")
        summary = page.locator("#m_kpi").inner_text()

        assert len(delayed) == 1
        with page.expect_response(default_url) as previous:
            delayed[0].continue_()
        assert previous.value.ok
        page.wait_for_load_state("networkidle")
        expect(selector).to_have_value(key)
        expect(row).to_be_visible()
        expect(page.locator("#m_kpi")).to_have_text(summary)
        expect(details).to_contain_text("west_units")


@pytest.mark.parametrize("operation", ["lineage", "status"])
def test_metric_details_stay_with_current_selection(browser, site, named_graph, operation):
    key = named_graph(metrics=[{"id": "westUnits", "table": "west_units", "name": "西区产量",
                               "status": "candidate", "measure": {"col": "units", "agg": "sum"}}])
    default_url = site + "/api/metrics?graph=demo"
    operation_url = site + "/api/metric/" + operation
    if operation == "lineage":
        operation_url += f"?graph={key}&name=westUnits"
    with browser.new_context() as context:
        page = context.new_page()
        with page.expect_response(default_url):
            page.goto(site + "/#metrics")
        selector = page.locator("#m_sel")
        selector.select_option(key)
        row = page.locator("#m_body tr").filter(has=page.get_by_text("west_units", exact=True))
        details = page.locator("#m_lineage")
        action = row
        if operation == "status":
            row.click()
            action = details.get_by_role("button", name="停用", exact=True)
        delayed_action = []
        page.route(operation_url, lambda route: delayed_action.append(route))
        with page.expect_request(operation_url) as submitted:
            action.click()

        delayed_list = []
        page.route(default_url, lambda route: delayed_list.append(route))
        with page.expect_request(default_url):
            selector.select_option("demo")
        expect(row).to_have_count(0)
        expect(page.locator("#m_kpi")).to_be_empty()
        assert len(delayed_list) == 1
        with page.expect_response(default_url) as current:
            delayed_list[0].continue_()
        assert current.value.ok
        expect(page.locator("#m_body table").first).to_be_visible()

        assert len(delayed_action) == 1
        with page.expect_response(operation_url) as previous:
            delayed_action[0].continue_()
        assert previous.value.ok
        page.wait_for_load_state("networkidle")
        expect(selector).to_have_value("demo")
        expect(details).to_be_hidden()
        expect(row).to_have_count(0)
        if operation == "status":
            assert submitted.value.post_data_json["graph"] == key
            data = page.request.get(site + "/api/metrics", params={"graph": key}).json()
            assert data["atomic"][0]["status"] == "deprecated"


def test_metric_status_refresh_preserves_newer_detail_in_same_graph(browser, site, named_graph):
    key = named_graph(metrics=[{"id": side, "table": side + "_units", "name": side,
                               "status": "candidate", "measure": {"col": "units", "agg": "sum"}}
                              for side in ("east", "west")])
    status_url = site + "/api/metric/status"
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(site + "/#metrics")
        page.locator("#m_sel").select_option(key)
        page.get_by_text("west_units", exact=True).click()
        details = page.locator("#m_lineage")
        expect(details).to_contain_text("west_units")
        held = []
        page.route(status_url, lambda route: held.append(route), times=1)
        with page.expect_request(status_url):
            details.get_by_role("button", name="停用", exact=True).click()
        page.get_by_text("east_units", exact=True).click()
        expect(details).to_contain_text("east_units")
        details.locator("#ml_reason").fill("当前指标的未提交意见")
        with page.expect_response(status_url) as previous:
            held[0].continue_()
        assert previous.value.ok
        west = page.locator("#m_body tr").filter(has=page.get_by_text("west_units", exact=True))
        expect(west).to_contain_text("已停用")
        expect(details).to_contain_text("east_units")
        expect(details.locator("#ml_reason")).to_have_value("当前指标的未提交意见")


@pytest.mark.parametrize("operation", ["graphs", "metrics", "lineage", "contract"])
def test_metric_read_timeout_can_retry_current_view(browser, site, named_graph, operation):
    key = named_graph(metrics=[{"id": "westUnits", "table": "west_units", "name": "西区产量",
                               "status": "candidate", "measure": {"col": "units", "agg": "sum"}}])
    list_endpoint = operation in ("graphs", "metrics")
    endpoint = {"graphs": "/api/graphs", "metrics": "/api/metrics?graph=demo"}
    url = site + (endpoint[operation] if list_endpoint else f"/api/metric/{operation}?graph={key}&name=westUnits")
    with browser.new_context() as context:
        page = context.new_page()
        if not list_endpoint:
            page.goto(site + "/#metrics")
            page.locator("#m_sel").select_option(key)
        page.clock.install()
        held = []
        page.route(url, lambda route: held.append(route), times=1)
        with page.expect_request(url):
            if list_endpoint:
                page.goto(site + "/#metrics", wait_until="domcontentloaded")
            else:
                page.get_by_text("west_units", exact=True).click()
        area = page.locator("#m_body" if list_endpoint else "#m_lineage")
        page.clock.fast_forward(30001)
        if operation == "graphs":
            expect(page.locator("#app_toast")).to_contain_text("超时")
            expect(area.get_by_role("alert")).to_contain_text("加载失败")
        else:
            expect(area.get_by_role("alert")).to_contain_text("超时")
        assert len(held) == 1
        held[0].abort()
        area.get_by_role("button", name="重试读取", exact=True).click()
        if list_endpoint:
            expect(page.locator("#m_body table").first).to_be_visible()
        else:
            expect(area).to_contain_text("west_units")


@pytest.mark.parametrize("operation", ["status", "adjudicate"])
def test_metric_write_timeout_reports_unknown_result_without_retry(browser, site, named_graph, operation):
    key = named_graph(metrics=[{"id": "westUnits", "table": "west_units", "name": "西区产量",
                               "status": "candidate", "measure": {"col": "units", "agg": "sum"}}])
    url = site + "/api/metric/" + operation
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(site + "/#metrics")
        page.locator("#m_sel").select_option(key)
        page.get_by_text("west_units", exact=True).click()
        button = page.get_by_role("button", name="停用" if operation == "status" else "重跑核验", exact=True)
        page.clock.install()
        held = []
        page.route(url, lambda route: held.append(route))
        with page.expect_request(url):
            button.click()
        expect(button).to_be_disabled()
        page.clock.fast_forward(30001)
        expect(page.locator("#app_toast")).to_contain_text("结果待确认")
        expect(button).to_be_enabled()
        assert len(held) == 1
        held[0].abort()


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

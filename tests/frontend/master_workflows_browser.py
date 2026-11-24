"""Accept the outcome of the latest user interaction in three browser engines."""
import json
from threading import Event
from time import monotonic

from flask import request
import pytest
from playwright.sync_api import expect

import server
from tests.frontend.result_scope_browser import page, scope_browser
from tests.result_scope_support import action_ledger
from tests.scenarios.fixtures import shift_metrics

__all__ = ["action_ledger", "page", "scope_browser"]


def wait_for_delivery(page, event):
    deadline = monotonic() + 8
    while not event.is_set() and monotonic() < deadline:
        page.wait_for_timeout(20)
    assert event.is_set(), "未观察到请求结束"
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


@pytest.mark.parametrize("newer", [199, 200])
def test_unknown_action_can_be_resolved_from_the_status_filter(page, site, action_ledger, newer):
    path = action_ledger(newer)
    rows = json.loads(path.read_text())
    rows[-1]["status"] = "unknown"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert page.request.get(site + "/api/actions").json()["unknown"] == 1
    page.goto(site + "/#actioncenter")
    page.locator("#acf_status").select_option("unknown")
    resolve = page.locator("#ac_log").get_by_role("button", name="处置", exact=True)
    expect(resolve).to_have_count(1)
    resolve.click()
    page.locator("#acm_op").fill("核对员")
    page.locator("#acm_p_outcome").select_option("failed")
    page.locator("#acm_p_comment").fill("本地测试回执已核对")
    with page.expect_response(lambda r: r.url.endswith("/api/action/resolve")) as saved:
        page.locator("#acm_ok").click()
    assert saved.value.ok
    assert page.request.get(site + "/api/actions").json()["unknown"] == 0
    record = next(row for row in json.loads(path.read_text()) if row["id"] == "evt_0")
    assert (record["status"], record["resolved_by"]) == ("failed", "核对员")


@pytest.mark.parametrize("surface,endpoint,textarea,button_text,cell", [
    ("sqldev", "query", "#sd_q", "运行", "#sd_out td"),
    ("sparql", "sparql", "#sq_q", "执行", "#sq_out td"),
    ("viz", "viz_run", "#vz_sql", "运行取数", "#vz_prev td"),
])
@pytest.mark.parametrize("late", [False, True], ids=["ordered", "out-of-order"])
def test_last_submitted_query_owns_the_display(page, site, monkeypatch, surface, endpoint, textarea, button_text, cell, late):
    page.goto(site + "/#" + surface)
    page.wait_for_load_state("networkidle")
    if surface == "viz":
        page.locator("#vz_type").select_option("table")
    queries = (["SELECT ?n WHERE { VALUES ?n { 11 } }", "SELECT ?n WHERE { VALUES ?n { 22 } }"]
               if surface == "sparql" else ["SELECT 11 AS n", "SELECT 22 AS n"])
    url = {"sqldev": "/api/query", "sparql": "/api/sparql", "viz": "/api/viz/run"}[surface]
    field = "query" if surface == "sparql" else "sql"
    started, release, settled = Event(), Event(), Event()
    original = server.app.view_functions[endpoint]

    def run():
        response = original()
        if late and request.get_json().get(field) == queries[0]:
            started.set()
            release.wait(15)
        return response

    monkeypatch.setitem(server.app.view_functions, endpoint, run)

    def observe(req):
        if req.url.endswith(url) and req.method == "POST" and req.post_data_json[field] == queries[0]:
            settled.set()

    page.on("requestfinished", observe)
    page.on("requestfailed", observe)
    run_button = page.locator("#p_" + surface).get_by_role("button", name=button_text, exact=True)
    result = page.locator(cell).first
    try:
        page.locator(textarea).fill(queries[0])
        run_button.click()
        if late:
            assert started.wait(5)
            if run_button.is_disabled():
                release.set()
                expect(run_button).to_be_enabled()
        else:
            expect(result).to_have_text("11")
        page.locator(textarea).fill(queries[1])
        run_button.click()
        expect(result).to_have_text("22")
    finally:
        release.set()
    wait_for_delivery(page, settled)
    assert result.inner_text().strip() == "22", {"editor": page.locator(textarea).input_value(),
                                               "displayed": result.inner_text()}


@pytest.mark.parametrize("select_one", [False, True])
def test_all_tables_checkbox_matches_selection_before_confirm(page, site, select_one):
    page.goto(site + "/#chat")
    page.locator("#dq_src_chip").click()
    page.locator("#dstab_table").click()
    card = page.locator("#ds_tgrid [data-t='dim_customer']")
    expect(card).to_be_visible()
    if select_one:
        card.click()
    count = page.locator("#ds_tgrid .sel").count()
    checked = page.locator("#ds_notable").is_checked()
    assert (count, checked) == (int(select_one), not select_one), {"selected_cards": count, "all_tables": checked}


def test_keyboard_user_can_identify_the_selected_table_and_cancel(page, site):
    page.goto(site + "/#chat")
    opener = page.locator("#dq_src_chip")
    expect(opener).to_have_attribute("role", "button")
    page.wait_for_load_state("networkidle")
    opener.focus()
    opener.press("Enter")
    dialog = page.get_by_role("dialog").filter(has=page.locator("#dstab_table"))
    expect(dialog).to_be_visible()
    page.locator("#dstab_table").press("Enter")
    card = page.locator("#ds_tgrid [data-t='dim_customer']")
    expect(card).to_have_attribute("tabindex", "0")
    card.focus()
    before = card.aria_snapshot()
    card.press("Enter")
    expect(page.locator("#ds_tgrid .sel")).to_have_count(1)
    after = card.aria_snapshot()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(opener).to_be_focused()
    opener.press("Enter")
    page.locator("#dstab_table").press("Enter")
    expect(card).to_be_visible()
    expect(page.locator("#ds_tgrid .sel")).to_have_count(0)
    assert after != before, {"unselected_accessibility_tree": before, "selected_accessibility_tree": after}


@pytest.mark.parametrize("double_click", [False, True])
def test_saving_new_board_once_creates_one_persisted_board(page, site, monkeypatch, tmp_path, double_click):
    monkeypatch.setattr(server, "_VIZ_BOARDS_F", str(tmp_path / "boards.json"))
    page.goto(site + "/#viz")
    page.wait_for_load_state("networkidle")
    page.locator("#vz_type").select_option("table")
    page.locator("#vz_sql").fill("SELECT 11 AS units")
    page.get_by_role("button", name="运行取数").click()
    expect(page.locator("#vz_prev td")).to_have_text("11")
    page.get_by_role("button", name="添加到看板").click()
    expect(page.locator("#vz_grid td")).to_have_text("11")
    page.locator("#vz_board_name").fill("班次看板")
    original = server.app.view_functions["viz_save"]
    release = Event()

    def save():
        response = original()
        if double_click:
            release.wait(8)
        return response

    monkeypatch.setitem(server.app.view_functions, "viz_save", save)
    button = page.get_by_role("button", name="保存看板", exact=True)
    try:
        with page.expect_request(lambda req: req.url.endswith("/api/viz/save")):
            if double_click:
                button.dblclick()
            else:
                button.click()
    finally:
        release.set()
    expect(page.locator("#vz_board_sel option").filter(has_text="班次看板").first).to_be_attached()
    page.wait_for_load_state("networkidle")
    saved = page.request.get(site + "/api/viz/boards").json()
    assert [(b["name"], len(b["charts"])) for b in saved] == [("班次看板", 1)], {
        "saved": [(b["name"], b["id"]) for b in saved]}


def open_shift_metrics(page, site, tmp_path, monkeypatch, same_label):
    monkeypatch.setattr(server, "WORK", str(tmp_path))
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    db = tmp_path / "shifts.db"
    ir, totals = shift_metrics(db, same_label)
    registered = page.request.post(site + "/api/build/connect", data={
        "kind": "sqlite", "name": "班次库", "path": str(db)})
    assert registered.ok
    source = registered.json()["conn"]["id"]
    ir["build_manifest"] = {"source": {"id": source}}
    (tmp_path / "built_shifts.json").write_text(json.dumps(ir), encoding="utf-8")
    headers = {"X-DataMind-Source": source}
    contract = page.request.get(site + "/api/metric/contract", params={"name": "west_total"}, headers=headers)
    assert contract.ok and contract.json()["metric"]["id"] == "west_total", contract.text()
    expected = page.request.post(site + "/api/viz/run", data={"src": source, "sql": contract.json()["compiled_sql"]})
    assert expected.ok and list(expected.json()["rows"][0].values()) == [totals["west_total"]]
    page.goto(site + "/?source=" + source + "#viz")
    options = page.locator("#vz_quick optgroup").filter(has=page.locator("option[value='m1']"))
    expect(options.locator("option")).to_have_count(2)
    return expected.json()["rows"]


@pytest.mark.parametrize("same_label", [False, True])
def test_chart_quick_metric_applies_the_selected_calculation(page, site, tmp_path, monkeypatch, same_label):
    expected = open_shift_metrics(page, site, tmp_path, monkeypatch, same_label)
    page.locator("#vz_quick").select_option("m1")
    page.wait_for_function("document.getElementById('vz_sql').value.includes('west_units')")
    with page.expect_response(lambda r: r.url.endswith("/api/viz/run")) as ran:
        page.get_by_role("button", name="运行取数").click()
    actual = ran.value.json()
    assert actual["rows"] == expected, {"selected": "west_total", "expected": expected, "actual": actual["rows"]}


@pytest.mark.parametrize("late", [False, True], ids=["ordered", "out-of-order"])
def test_quick_metric_changes_keep_the_last_selected_contract(page, site, tmp_path, monkeypatch, late):
    expected = open_shift_metrics(page, site, tmp_path, monkeypatch, same_label=False)
    started, release, settled = Event(), Event(), Event()
    endpoint = "metrics.metric_contract_view"
    original = server.app.view_functions[endpoint]

    def get_contract():
        response = original()
        if late and request.args.get("name") in {"班次产量", "east_total"}:
            started.set()
            release.wait(15)
        return response

    monkeypatch.setitem(server.app.view_functions, endpoint, get_contract)
    picker = page.locator("#vz_quick")
    try:
        with page.expect_request(lambda req: "/api/metric/contract?" in req.url) as first:
            picker.select_option("m0")
        if late:
            assert started.wait(5)
            def observe(req):
                if req.url == first.value.url:
                    settled.set()
            page.on("requestfinished", observe)
            page.on("requestfailed", observe)
            if picker.is_disabled():
                release.set()
                expect(picker).to_be_enabled()
        else:
            page.wait_for_function("document.getElementById('vz_sql').value.includes('east_units')")
        picker.select_option("m1")
        page.wait_for_function("document.getElementById('vz_sql').value.includes('west_units')")
    finally:
        release.set()
    if late:
        wait_for_delivery(page, settled)
    with page.expect_response(lambda r: r.url.endswith("/api/viz/run")) as ran:
        page.get_by_role("button", name="运行取数").click()
    actual = ran.value.json()["rows"]
    assert actual == expected, {"picker": picker.input_value(), "sql": page.locator("#vz_sql").input_value(),
                                "actual": actual, "expected": expected}

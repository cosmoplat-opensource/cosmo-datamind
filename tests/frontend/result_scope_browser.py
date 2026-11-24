"""Exercise the displayed selection and compare it with real requests, downloads and edits."""
import csv
import json
import re
import sqlite3
from threading import Event
from time import monotonic

from flask import request
import pytest
from playwright.sync_api import expect, sync_playwright

import server
from tests.browser_runtime import browser_executable
from tests.frontend.navigation_transitions import inventory
from tests.result_scope_support import action_ledger, review_graph

__all__ = ["action_ledger", "review_graph"]


@pytest.fixture(scope="module", params=["chromium", "firefox", "webkit"])
def scope_browser(request):
    options = {"executable_path": browser_executable()} if request.param == "chromium" else {}
    with sync_playwright() as pw, getattr(pw, request.param).launch(**options) as browser:
        yield browser


@pytest.fixture
def page(scope_browser):
    with scope_browser.new_context() as context:
        yield context.new_page()


@pytest.mark.parametrize("key", inventory.pages)
def test_link_and_reload_keep_the_database_shown_on_each_page(page, site, tmp_path, monkeypatch, key):
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "sources.json"))
    path = tmp_path / "two_tables.db"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE daily(v INTEGER); CREATE TABLE monthly(v INTEGER);")
    source = page.request.post(site + "/api/build/connect", data={
        "kind": "sqlite", "name": "范围核对库", "path": str(path)}).json()["conn"]["id"]
    for reload in (False, True):
        with page.expect_response(lambda r: r.url.endswith("/api/data/context")) as context:
            if reload:
                page.reload()
            else:
                page.goto(f"{site}/?source={source}#{key}")
        assert context.value.ok and context.value.request.headers.get("x-datamind-source") == source
        assert context.value.json()["tables"] == 2
        expect(page.locator(".page.on")).to_have_id("p_" + key)
        expect(page.locator("#app_source")).to_have_value(source)
        expect(page.locator("#app_source_status")).to_contain_text("2 表")


@pytest.mark.parametrize("recent", [199, 200])
def test_pending_approval_is_reachable_from_the_action_center(page, site, action_ledger, recent):
    action_ledger(recent)
    assert page.request.get(site + "/api/actions").json()["pending"] == 1
    page.goto(site + "/#actioncenter")
    expect(page.locator("#acf_status")).to_be_visible()
    page.wait_for_load_state("networkidle")
    assert page.locator("#ac_pending").get_by_role("button", name="驳回").is_visible(), {
        "completed_before_pending": recent, "pending_kpi": 1, "queue": page.locator("#ac_pending").inner_text()}


@pytest.mark.parametrize("status,keyword,expected", [
    ("", "", ["班次 2", "班次 1", "班次 0"]),
    ("pending", "", ["班次 0"]),
    ("", "工位 1", ["班次 1"]),
    ("", "缺席", []),
])
def test_action_csv_matches_filtered_rows(page, site, action_ledger, status, keyword, expected):
    action_ledger(2)
    page.goto(site + "/#actioncenter")
    expect(page.locator("#acf_status")).to_be_visible()
    page.wait_for_load_state("networkidle")
    page.locator("#acf_status").select_option(status)
    page.locator("#acf_kw").fill(keyword)
    visible = page.locator("#ac_log tr td:nth-child(2)").all_text_contents()
    assert visible == expected
    button = page.locator("#ac_log").get_by_role("button", name=re.compile("导出"))
    if not expected and button.is_disabled():
        return
    with page.expect_download() as event:
        button.click()
    with event.value.path().open(encoding="utf-8-sig", newline="") as infile:
        reader = csv.reader(infile)
        next(reader)
        names = [row[1] for row in reader]
    label = (button.inner_text() + " " + (button.get_attribute("title") or "")).strip()
    assert names == expected or ("全部" in label and names == ["班次 2", "班次 1", "班次 0"]), {
        "screen": visible, "export": names, "label": label}


@pytest.mark.parametrize("transaction", ["one", "all", "cancel"])
def test_question_uses_the_confirmed_table_selection(page, site, transaction):
    page.goto(site + "/#chat")
    chip = page.locator("#dq_src_chip")
    modal = page.locator("#dq_dsmodal")

    def open_tables():
        chip.click()
        page.locator("#dstab_table").click()
        expect(page.locator("#ds_tgrid [data-t='dim_customer']")).to_be_visible()

    open_tables()
    page.locator("#ds_tgrid [data-t='dim_customer']").click()
    modal.get_by_role("button", name="确定", exact=True).click()
    if transaction != "one":
        open_tables()
        page.locator("#ds_notable").check()
        modal.get_by_role("button", name="确定" if transaction == "all" else "取消", exact=True).click()
    page.locator("#chat_q").fill("dim_product有多少条记录？")
    with page.expect_request(lambda r: r.url.endswith("/api/chat/stream") and r.method == "POST") as sent:
        page.locator("#chat_btn").click()
    assert sent.value.post_data_json["tables"] == ([] if transaction == "all" else ["dim_customer"]), {
        "scope_label": page.locator("#dq_src_lbl").inner_text(), "sent_tables": sent.value.post_data_json["tables"]}


@pytest.mark.parametrize("size", [50, 51])
def test_review_page_select_all_reflects_the_visible_checkboxes(page, site, review_graph, size):
    key = review_graph("built_scope_review", "班次", size)
    page.goto(site + "/#review")
    page.locator("#rv_sel").select_option(key)
    body = page.locator("#rv_body")
    expect(body.locator(".rv-table input[type=checkbox]")).to_have_count(50)
    if size > 50:
        body.get_by_role("button", name="再显示 1 条").click()
    body.get_by_role("button", name="全选本页").click()
    body.locator(".rv-table input[type=checkbox]").first.uncheck()
    lookup = body.get_by_role("textbox", name="筛选关系")
    lookup.fill("unit_0")
    lookup.fill("")
    checks = body.locator(".rv-table input[type=checkbox]")
    assert checks.count() == 50 and body.locator(".rv-table input:checked").count() == 49
    assert body.get_by_role("button", name="全选本页", exact=True).is_visible(), body.locator(".rv-bar").inner_text()


@pytest.mark.parametrize("reordered", [False, True])
def test_review_saves_the_graph_whose_relationship_was_displayed(page, site, review_graph, monkeypatch, reordered):
    a = review_graph("built_scope_east", "东区", 1)
    b = review_graph("built_scope_west", "西区", 1)
    page.goto(site + "/#review")
    page.wait_for_load_state("networkidle")
    start, release, settled = Event(), Event(), Event()
    original = server.app.view_functions["ont_review"]

    def load_review():
        if reordered and request.args.get("graph") == a:
            start.set()
            release.wait(10)
        return original()

    monkeypatch.setitem(server.app.view_functions, "ont_review", load_review)
    def observe_slow_request(req):
        if req.url.endswith("/api/ont/review?graph=" + a):
            settled.set()

    page.on("requestfinished", observe_slow_request)
    page.on("requestfailed", observe_slow_request)
    select = page.locator("#rv_sel")
    body = page.locator("#rv_body .rv-table")
    try:
        if reordered:
            select.select_option(a)
            assert start.wait(5)
        select.select_option(b)
        expect(body).to_contain_text("西区")
        if reordered:
            release.set()
            # A fixed client may either discard the late response or cancel its request.
            deadline = monotonic() + 5
            while not settled.is_set() and monotonic() < deadline:
                page.wait_for_timeout(20)
            assert settled.is_set(), "未观察到先选图谱的请求结束"
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    finally:
        release.set()
    page.wait_for_load_state("networkidle")
    shown = body.inner_text()
    body.get_by_role("button", name="通过", exact=True).click()
    page.locator("#rvm_who").fill("范围核对员")
    with page.expect_response(lambda r: r.url.endswith("/api/ont/apply")) as applied:
        page.locator("#rvm_ok").click()
    assert applied.value.ok, applied.value.text()
    approved = []
    for key in [a, b]:
        row = page.request.get(site + "/api/ont/review", params={"graph": key}).json()["rows"][0]
        if row["human_review"] == "approved":
            approved.append(key)
    assert ("西区" in shown, approved) == (True, [b]), {"rendered": shown, "selected": select.input_value(), "approved": approved}


@pytest.mark.parametrize("size", [200, 201])
def test_history_replays_the_latest_row_and_total_of_the_live_answer(page, site, size):
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE daily(seq INTEGER, units INTEGER)")
        db.executemany("INSERT INTO daily VALUES(?, ?)", [(i, i * 2) for i in range(1, size + 1)])
        rows = [{"seq": n, "units": v} for n, v in db.execute("SELECT seq, units FROM daily ORDER BY seq")]
    sql = "SELECT seq, units FROM daily ORDER BY seq"
    answer = {"type": "done", "steps": [{"step": "query", "ok": True}], "summary": "班次记录",
              "results": [{"title": "班次记录", "result_id": "scope-daily", "sql": sql,
                           "chart": {"type": "line", "x": "seq", "y": ["units"]},
                           "data": {"columns": ["seq", "units"], "rows": rows}}]}
    page.route("**/api/chat/stream", lambda route: route.fulfill(
        content_type="text/event-stream", body="data: " + json.dumps(answer) + "\n\n"))
    question = "按顺序列出班次记录"
    page.goto(site + "/#chat")
    page.locator("#chat_q").fill(question)
    page.locator("#chat_btn").click()
    result = page.locator("#dq_stream .dq-res").last

    def read_card():
        result.locator("details").filter(has_text="数据预览").locator("summary").click()
        return (result.locator(".ov-row").first.inner_text().strip(),
                result.locator(".dq-prev tr").last.locator("td").all_text_contents())

    expect(result).to_be_visible()
    live = read_card()
    assert re.search(rf"{size}$", live[0]) and live[1] == [str(size), str(size * 2)]
    page.reload()
    page.locator("#dq_hist .it").filter(has_text=question).click()
    expect(page.locator("#dq_stream").get_by_text("历史回放", exact=True)).to_be_visible()
    replay = read_card()
    assert replay == live, {"live": live, "replay": replay}

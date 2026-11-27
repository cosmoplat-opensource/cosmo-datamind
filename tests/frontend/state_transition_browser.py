"""User-visible state after a source switch, failed mutations, refresh and export."""
import csv
import io
import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


@pytest.fixture
def page(browser):
    with browser.new_context(viewport={"width": 1440, "height": 900}) as context:
        yield context.new_page()


def open_page(page, site, name):
    page.goto(f"{site}/#{name}")
    expect(page.locator("#p_" + name)).to_be_visible()
    page.wait_for_function("typeof APP_DATA !== 'undefined' && APP_DATA.ready")


def next_paint(page):
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


@pytest.mark.parametrize("switch", ["unchanged", "after-result", "before-result"])
def test_sql_results_belong_to_the_displayed_source(page, site, make_sqlite, tmp_path, monkeypatch, switch):
    import server

    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    sources = []
    for value in [31415, 92653]:
        db = make_sqlite({"meter": ("reading INTEGER", [(value,)])}, fname=f"meter-{value}.db")
        response = page.request.post(site + "/api/build/connect",
                                     data={"name": f"meter-{value}", "kind": "sqlite", "path": str(db)})
        assert response.ok
        sources.append(response.json()["conn"]["id"])
    open_page(page, site, "sqldev")
    select = page.locator("#sd_src")
    select.select_option(sources[0])
    page.locator("#sd_q").fill("SELECT reading FROM meter")
    run = page.locator("#p_sqldev").get_by_role("button", name="运行", exact=True)
    if switch == "before-result":
        pending = []
        page.route("**/api/query*", lambda route: pending.append(route), times=1)
        with page.expect_request(lambda r: "/api/query" in r.url):
            run.click()
        page.wait_for_function("true")
        route, = pending
        response = route.fetch()
        assert response.json()["rows"] == [{"reading": 31415}]
        if select.is_enabled():
            select.select_option(sources[1])
        route.fulfill(response=response)
        expect(run).to_be_enabled()
        expect(page.locator("#sd_info")).not_to_contain_text("执行中")
        if select.input_value() != sources[1]:
            select.select_option(sources[1])
    else:
        run.click()
        expect(page.locator("#sd_out td")).to_have_text("31415")
        if switch == "after-result":
            select.select_option(sources[1])
    next_paint(page)
    if switch != "unchanged":
        result = page.locator("#sd_out").inner_text()
        provenance = page.locator("#sd_info").inner_text() + result
        assert "31415" not in result or "meter-31415" in provenance or sources[0] in provenance, (
            f"selected meter-92653 but result and source attribution were {provenance!r}")
        run.click()
        expect(page.locator("#sd_out td")).to_have_text("92653")


@pytest.mark.parametrize("operation,fail", [("new", False), ("new", True), ("delete", False), ("delete", True)])
def test_chat_mutation_keeps_transcript_until_success(page, site, tmp_path, monkeypatch, operation, fail):
    import server

    monkeypatch.setattr(server, "CHATS_F", str(tmp_path / "chats.json"))
    title = "会话回读-" + uuid4().hex[:8]
    cid = page.request.post(site + "/api/ont/chats/new", data={"graph": "demo"}).json()["id"]
    reply = page.request.post(site + "/api/ont/chat", data={"id": cid, "graph": "demo", "message": title})
    assert reply.ok
    open_page(page, site, "claw")
    entry = page.locator("#claw_sessions").get_by_role("button", name=re.compile(title))
    entry.click()
    transcript = page.locator("#claw_log")
    expect(transcript).to_contain_text(title)
    path = "/api/ont/chats/" + operation
    if fail:
        page.route("**" + path + "*", lambda r: r.fulfill(status=503, json={"error": "暂时无法保存"}), times=1)
    button = page.locator("#p_claw").get_by_role("button", name="新会话" if operation == "new" else "清空会话")
    with page.expect_response(lambda r: path in r.url) as completed:
        button.click()
    assert completed.value.status == (503 if fail else 200)
    expect(button).to_be_enabled()
    if fail:
        assert page.request.get(site + "/api/ont/chats/" + cid).ok
        expect(transcript).to_contain_text(title, timeout=1800)
        expect(page.get_by_text(re.compile("暂时无法保存|失败|重试")).filter(visible=True).first).to_be_visible()
        button.click()
    expect(transcript).not_to_contain_text(title)
    if operation == "delete":
        assert page.request.get(site + "/api/ont/chats/" + cid).status == 404
        expect(entry).to_have_count(0, timeout=2000)
    else:
        assert page.request.get(site + "/api/ont/chats/" + cid).ok


@pytest.mark.parametrize("name,path,panel,recovered_text", [
    ("quality", "/api/quality", "#quality_list", "规则"),
    ("qaeval", "/api/eval/results", "#qe_body", "尚未运行评测"),
    ("viz", "/api/viz/boards", "#vz_board_sel", "新建看板"),
    ("actioncenter", "/api/action/log", "#ac_log", "动作记录"),
    ("claw", "/api/ont/chats", "#claw_sessions", "无会话"),
], ids=["quality", "qaeval", "viz", "actioncenter", "claw"])
def test_list_distinguishes_service_failure_from_empty_and_recovers(
    page, site, name, path, panel, recovered_text, tmp_path, monkeypatch
):
    if name == "claw":
        import server

        monkeypatch.setattr(server, "CHATS_F", str(tmp_path / "chats.json"))
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route(re.compile(re.escape(path) + r"(?:\?.*)?$"), lambda r: r.fulfill(status=503, json={"error": "暂时不可用"}))
    with page.expect_response(lambda r: r.url.split("?")[0].endswith(path) and r.status == 503):
        open_page(page, site, name)
    next_paint(page)
    failure = page.get_by_text(re.compile("暂时不可用|加载失败|重试")).filter(visible=True)
    healthy = page.locator(panel)
    visible_failure = failure.count() > 0
    failed_text = healthy.inner_text()
    page.unroute_all()
    with page.expect_response(lambda r: r.url.split("?")[0].endswith(path) and r.ok):
        page.reload()
    expect(healthy).to_contain_text(recovered_text)
    assert visible_failure and not errors, f"after 503, panel={failed_text!r}, uncaught={errors!r}"


def test_action_csv_has_one_header_for_each_exported_field(page, site):
    record = {"id": "sample", "status": "executed", "action_cn": '质检,"A"', "operator": "甲",
              "params": {"备注": "第一行\n第二行"}, "effects": [], "ts": "2026-11-22"}
    page.route("**/api/action/log*", lambda r: r.fulfill(json={"items": [record]}))
    open_page(page, site, "actioncenter")
    expect(page.locator("#ac_log")).to_contain_text(record["action_cn"])
    with page.expect_download() as result:
        page.get_by_role("button", name="导出 CSV").click()
    rows = list(csv.reader(io.StringIO(result.value.path().read_text(encoding="utf-8-sig"))))
    headers, values = rows
    assert len(headers) == len(values), (headers, values)
    fields = dict(zip(headers, values))
    assert fields["动作"] == record["action_cn"] and fields["操作人"] == record["operator"]
    assert "第一行\n第二行" in fields["参数"]


def test_metadata_review_count_tracks_selected_graph_and_revisits(page, site):
    # Keep genuine completeness payloads and vary only the known review-count fixture.
    page.goto(site + "/#home")
    page.wait_for_function("APP_DATA.ready")
    keys = [g["id"] for g in page.request.get(site + "/api/graphs").json()][:2]
    assert len(keys) == 2

    def stats(route):
        response = route.fetch()
        data = response.json()
        key = route.request.url.split("?")[0].rsplit("/", 1)[1]
        data["relations"].update(semanticReviewed=3 if key == keys[0] else 0, semanticDisputed=0)
        route.fulfill(response=response, json=data)

    page.route("**/api/ont/completeness/**", stats)
    page.locator('[data-p="ontquality"]').click()
    select = page.locator("#oq_sel")
    select.select_option(keys[0])
    count = page.locator("#p_ontquality").get_by_text(re.compile(r"语义评审.*3 条已审"))
    expect(count.first).to_be_visible()
    with page.expect_response(lambda r: "/api/ont/completeness/" + keys[1] in r.url):
        select.select_option(keys[1])
    next_paint(page)
    expect(count).to_have_count(0, timeout=1800)
    select.select_option(keys[0])
    expect(count).to_have_count(1)


@pytest.mark.parametrize("late_status", [200, 503], ids=["success", "failure"])
def test_metadata_coverage_ignores_late_response(page, site, late_status):
    open_page(page, site, "home")
    old, current = [g["id"] for g in page.request.get(site + "/api/graphs").json()][:2]
    old_url = site + "/api/ont/completeness/" + old
    current_url = site + "/api/ont/completeness/" + current
    held = []

    def current_stats(route):
        response = route.fetch()
        data = response.json()
        data["score"] = 27
        data["relations"]["semanticReviewed"] = 2
        route.fulfill(response=response, json=data)

    page.route(old_url, lambda route: held.append(route), times=1)
    page.route(current_url, current_stats)
    with page.expect_request(old_url):
        page.locator('[data-p="ontquality"]').click()
    select = page.locator("#oq_sel")
    select.select_option(current)
    score = page.locator("#oq_kpis .kpi .n").first
    review = page.locator("#oq_sem")
    expect(score).to_have_text("27%")
    expect(review).to_contain_text("2 条已审")

    route, = held
    with page.expect_event("requestfinished", predicate=lambda req: req.url == old_url):
        if late_status == 200:
            response = route.fetch()
            data = response.json()
            data["score"] = 73
            data["relations"]["semanticReviewed"] = 3
            route.fulfill(response=response, json=data)
        else:
            route.fulfill(status=late_status, json={"error": "此前图谱读取失败"})
    next_paint(page)
    expect(select).to_have_value(current)
    expect(score).to_have_text("27%")
    expect(review).to_contain_text("2 条已审")


def test_metadata_coverage_times_out_and_refreshes(page, site):
    open_page(page, site, "ontquality")
    scores = page.locator("#oq_kpis .kpi .n")
    expect(scores).to_have_count(5)
    select = page.locator("#oq_sel")
    old = select.input_value()
    current = next(g["id"] for g in page.request.get(site + "/api/graphs").json() if g["id"] != old)
    url = site + "/api/ont/completeness/" + current
    expected = page.request.get(url).json()["score"]
    page.clock.install()
    held = []
    page.route(url, lambda route: held.append(route), times=1)
    with page.expect_request(url):
        select.select_option(current)
    panel = page.locator("#p_ontquality")
    try:
        expect(panel.get_by_role("status")).to_contain_text("正在读取")
        for selector in ("#oq_bfo", "#oq_mat", "#oq_gaps"):
            expect(panel.locator(selector)).to_be_empty()
        expect(panel.locator("#oq_sem")).to_have_count(0)
        page.clock.fast_forward(30001)
        expect(panel.get_by_role("alert")).to_contain_text("刷新重试")
        expect(scores).to_have_count(0)
        assert len(held) == 1
    finally:
        for route in held:
            route.abort()
    panel.get_by_role("button", name="刷新", exact=True).click()
    expect(select).to_have_value(current)
    expect(scores.first).to_have_text(f"{expected}%")


def test_overview_survives_unavailable_data_and_recovers_exact_totals(page, site):
    kpis = page.request.get(site + "/api/overview", headers={"X-DataMind-Source": "demo"}).json()["kpi"]
    page.route("**/api/overview*", lambda r: r.fulfill(status=503, json={"error": "总览暂不可用"}))
    open_page(page, site, "home")
    expect(page.locator("#kpis")).to_contain_text("总览暂不可用")
    page.unroute_all()
    page.reload()
    cells = page.locator("#kpis .kpi .n")
    expect(cells).to_have_count(5)
    assert [re.sub(r"[^0-9]", "", s) for s in cells.all_text_contents()] == [
        str(kpis[k]) for k in ("tables", "rows", "metrics", "objects", "links")]


def test_glossary_combined_filters_and_clear_restore_inventory(page, site):
    with page.expect_response(lambda r: "/api/glossary" in r.url and r.ok) as response:
        open_page(page, site, "glossary")
    terms = response.value.json()["terms"]
    term = next(t for t in terms if t["en"] and t["type"] == "表名")
    summary = page.locator("#gl_info")
    expect(summary).to_contain_text(f"共 {len(terms)} 条术语")
    query = page.locator("#gl_q")
    page.locator("#gl_type").select_option(term["type"])
    query.fill(term["en"].upper())
    expect(page.locator("#gl_list")).to_contain_text(term["cn"])
    query.fill("no-matching-glossary-entry-7924")
    expect(summary).to_contain_text("筛出 0")
    expect(page.locator("#gl_list td")).to_have_count(0)
    query.fill("")
    page.locator("#gl_type").select_option("")
    expect(summary).to_contain_text(f"筛出 {len(terms)}")


def test_sparql_replaces_previous_answer_after_empty_or_invalid_query(page, site):
    open_page(page, site, "sparql")
    query = page.locator("#sq_q")
    run = page.locator("#p_sparql").get_by_role("button", name="执行")
    for expression, result in [("ASK { ?s ?p ?o }", "是"),
                               ("ASK { <urn:absent:1> <urn:absent:2> <urn:absent:3> }", "否")]:
        query.fill(expression)
        run.click()
        expect(page.locator("#sq_out td")).to_have_text(result)
    query.fill("SELECT WHERE {")
    with page.expect_response(lambda r: "/api/sparql" in r.url) as error:
        run.click()
    assert error.value.status == 400
    expect(page.locator("#sq_info")).to_contain_text(error.value.json()["error"])
    expect(page.locator("#sq_out td")).to_have_count(0)
    query.fill("SELECT ?s WHERE { ?s ?p ?o } LIMIT 2")
    run.click()
    expect(page.locator("#sq_out td")).to_have_count(2)

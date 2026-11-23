"""Inspect the values an analyst can actually read, from physical rows to saved cards."""
from decimal import Decimal, InvalidOperation
import re
import sqlite3

import pytest
from playwright.sync_api import expect


@pytest.fixture
def page(browser):
    with browser.new_context(viewport={"width": 1440, "height": 900}) as context:
        yield context.new_page()


@pytest.fixture
def source(page, site, tmp_path, monkeypatch):
    import server

    db = tmp_path / "readings.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE daily(seq INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO daily VALUES (?)", [(i,) for i in range(1, 502)])
        conn.execute("CREATE TABLE readings(seq INTEGER PRIMARY KEY, work_id INTEGER, reading REAL, note TEXT)")
        conn.executemany("INSERT INTO readings VALUES(?,?,?,?)",
                         [(1, 9007199254740992, 123456.1234561, None),
                          (2, 9007199254740993, 123456.1234562, "")])
    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    registered = page.request.post(site + "/api/build/connect",
                                   data={"kind": "sqlite", "name": "读数对照库", "path": str(db)})
    assert registered.ok
    return registered.json()["conn"]["id"]


def open_page(page, site, name, source="demo"):
    page.goto(f"{site}/?source={source}#{name}")
    page.wait_for_function("APP_DATA.ready")
    expect(page.locator("#p_" + name)).to_be_visible()


@pytest.mark.parametrize(("engine", "custom_port", "expected_port"), [
    ("doris", None, "9030"),
    ("hive", None, "10000"),
    ("doris", "19030", "19030"),
])
def test_saved_connection_uses_selected_engine_port(page, site, engine, custom_port, expected_port):
    open_page(page, site, "conn")
    expect(page.locator("#cn_n")).to_contain_text(re.compile(r"\d"))
    page.get_by_role("button", name="+ 新建连接").click()
    page.locator("#cn_kind").select_option(engine)
    page.locator("#cn_name").fill("端口回归")
    page.locator("#cn_host").fill("warehouse.invalid")
    page.locator("#cn_db").fill("fixture")
    if custom_port:
        page.locator("#cn_port").fill(custom_port)
    with page.expect_response(lambda r: r.url.endswith("/api/build/connect") and r.request.method == "POST") as saved:
        page.get_by_role("button", name="保存连接", exact=True).click()
    assert saved.value.ok
    source_id = saved.value.json()["conn"]["id"]
    sources = page.request.get(site + "/api/build/sources").json()["sources"]
    stored = next(source for source in sources if source["id"] == source_id)
    scheme = "hive2" if engine == "hive" else "mysql"
    assert stored["kind"] == engine
    assert stored["dsn"] == f"jdbc:{scheme}://warehouse.invalid:{expected_port}/fixture"


def test_unverified_hive_connection_describes_its_availability(page, site):
    response = page.request.post(site + "/api/build/connect", data={
        "name": "待验证的 Hive", "kind": "hive", "dsn": "jdbc:hive2://warehouse.invalid:10000/availability",
    })
    assert response.ok
    open_page(page, site, "conn")
    row = page.locator("#cn_list .cn-row").filter(has_text="待验证的 Hive")
    expect(row.locator(".mt")).to_be_visible()
    status = row.locator(".mt").inner_text()
    assert re.search(r"登记|待验|未验|未连|离线|命令行|CLI|暂未|不支持|未接入|未测试", status, re.I), status


def result_table(page, site, source, surface):
    open_page(page, site, surface, source)
    sql = "SELECT seq,work_id,reading,note FROM readings ORDER BY seq"
    if surface == "sqldev":
        expect(page.locator("#sd_src")).to_have_value(source)
        page.locator("#sd_q").fill(sql)
        page.locator("#p_sqldev").get_by_role("button", name="运行", exact=True).click()
        table = page.locator("#sd_out table")
    elif surface == "viz":
        expect(page.locator("#vz_src")).to_have_value(source)
        page.locator("#vz_type").select_option("table")
        page.locator("#vz_sql").fill(sql)
        page.get_by_role("button", name="运行取数").click()
        table = page.locator("#vz_prev table")
    elif surface == "catalog":
        page.locator("#tbl_list").get_by_text("readings", exact=True).click()
        table = page.locator("#tbl_prev table")
    else:
        page.locator("#cn_list").get_by_text("读数对照库", exact=True).click()
        page.locator("#cnb_list tr").filter(has_text="readings").get_by_role("link").click()
        table = page.locator("#cn_preview table")
    expect(table.locator("td")).to_have_count(8)
    return table


def representations(cell):
    return [s for s in [cell.inner_text(), cell.get_attribute("title"), cell.get_attribute("aria-label")]
            if s is not None]


@pytest.mark.parametrize("surface", ["sqldev", "viz", "catalog", "conn"])
@pytest.mark.parametrize("field,expected", [
    ("seq", ["1", "2"]),
    ("work_id", ["9007199254740992", "9007199254740993"]),
    ("reading", ["123456.1234561", "123456.1234562"]),
], ids=["ordinary", "64-bit-id", "close-readings"])
def test_each_table_retains_inspectable_source_values(page, site, source, surface, field, expected):
    table = result_table(page, site, source, surface)
    index = table.locator("th").all_text_contents().index(field)
    rows = table.locator("tr").filter(has=page.locator("td"))
    shown = [representations(row.locator("td").nth(index)) for row in rows.all()]

    def exact(values, original):
        for label in values:
            try:
                if Decimal(label.strip().replace(",", "")) == Decimal(original):
                    return True
            except InvalidOperation:
                pass
        return False

    assert all(exact(labels, target) for labels, target in zip(shown, expected)), (surface, expected, shown)


@pytest.mark.parametrize("surface", ["sqldev", "catalog", "viz", "conn"])
def test_null_text_is_distinguishable_from_an_empty_string(page, site, source, surface):
    table = result_table(page, site, source, surface)
    cells = table.locator("tr:has(td) td:last-child").all()
    missing, empty = ({s.strip() for s in representations(cell) if s.strip()} for cell in cells)
    assert missing != empty, (surface, missing, empty)


@pytest.mark.parametrize("many", [False, True], ids=["single-row", "two-periods"])
def test_saved_kpi_identifies_the_same_observation_as_its_preview(page, site, monkeypatch, tmp_path, many):
    import server

    monkeypatch.setattr(server, "_VIZ_BOARDS_F", str(tmp_path / "boards.json"))
    open_page(page, site, "viz")
    expect(page.locator("#vz_src")).to_have_value("demo")
    sql = "SELECT '2026-Q1' AS period, 41 AS total"
    if many:
        sql += " UNION ALL SELECT '2026-Q2', 42"
    page.locator("#vz_type").select_option("kpi")
    page.locator("#vz_sql").fill(sql)
    page.get_by_role("button", name="运行取数").click()
    expected = "42" if many else "41"
    preview = page.locator("#vz_prev")
    expect(preview).to_contain_text(expected)
    if many:
        expect(preview).to_contain_text("2026-Q2")
    page.get_by_role("button", name="添加到看板").click()
    page.locator("#vz_board_name").fill("季度验收")
    page.get_by_role("button", name="保存看板").click()
    selected = page.locator("#vz_board_sel")
    expect(selected).not_to_have_value("")
    ident = selected.input_value()
    page.reload()
    selected.select_option(ident)
    card = page.locator("#vz_grid .vz-card")
    expect(card).to_contain_text(expected)
    if many:
        expect(card).to_contain_text("2026-Q2", timeout=1800)


def test_navigation_exposes_the_current_destination_after_keyboard_activation(page, site):
    open_page(page, site, "catalog")
    names = page.locator("aside .nav[data-p]").evaluate_all(
        "nodes => nodes.filter(n => !n.hidden && n.getClientRects().length).map(n => n.dataset.p)")
    assert len(names) > 1
    missing = []
    for key in names:
        nav = page.locator(f'aside [data-p="{key}"]')
        nav.focus()
        page.keyboard.press("Enter")
        expect(page.locator(".page.on")).to_have_id("p_" + key)
        current = page.locator('aside [aria-current]:not([aria-current="false"]):not([aria-current=""]), '
                               'aside [aria-selected="true"][data-p], aside [aria-pressed="true"][data-p]')
        if current.count() == 1 and current.first.evaluate("(e,k) => e.closest('[data-p]')?.dataset.p === k", key):
            continue
        if not re.search("当前页|当前页面|current page", nav.aria_snapshot(), re.I):
            missing.append(key)
    assert not missing, f"没有向辅助技术说明当前位置的菜单项 {missing}"


@pytest.mark.parametrize("count", [500, 501])
def test_last_observation_kpi_discloses_a_partial_result(page, site, source, count):
    open_page(page, site, "viz", source)
    expect(page.locator("#vz_src")).to_have_value(source)
    page.locator("#vz_type").select_option("kpi")
    page.locator("#vz_sql").fill(f"SELECT seq AS period, seq AS reading FROM daily WHERE seq <= {count} ORDER BY seq")
    page.get_by_role("button", name="运行取数").click()
    preview = page.locator("#vz_prev")
    expect(preview).to_contain_text("reading")
    text = preview.inner_text() + " " + page.locator("#vz_info").inner_text()
    assert str(count) in preview.inner_text() or re.search(r"截断|仅显示|前\s*500|部分结果|有更多|至少", text), text


@pytest.mark.parametrize("kind", ["mysql", "postgres"])
def test_live_sql_source_is_also_selectable_for_a_chart(page, site, tmp_path, monkeypatch, kind):
    import server

    monkeypatch.setattr(server, "_BUILD_CONN_F", str(tmp_path / "connections.json"))
    monkeypatch.setattr(server, "_ext_query", lambda conn, sql: {"columns": ["n"], "rows": [{"n": 33}]})
    monkeypatch.setattr(server, "_ext_tables", lambda conn: [])
    saved = page.request.post(site + "/api/build/connect", data={
        "name": "外部验收库", "kind": kind, "dsn": kind + "://localhost/fixture"})
    assert saved.ok
    ident = saved.json()["conn"]["id"]
    for path in ["/api/query", "/api/viz/run"]:
        data = page.request.post(site + path, data={"src": ident, "sql": "SELECT 33 AS n"}).json()
        assert data["rows"] == [{"n": 33}]
    open_page(page, site, "sqldev")
    page.locator("#sd_src").select_option(ident)
    open_page(page, site, "viz")
    expect(page.locator("#vz_src option[value=demo]")).to_be_attached()
    expect(page.locator(f'#vz_src option[value="{ident}"]')).to_be_attached(timeout=1800)
    page.locator("#vz_src").select_option(ident)
    page.locator("#vz_type").select_option("kpi")
    page.locator("#vz_sql").fill("SELECT 33 AS n")
    page.get_by_role("button", name="运行取数").click()
    expect(page.locator("#vz_prev")).to_contain_text("33")

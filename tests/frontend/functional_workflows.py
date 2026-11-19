"""跨模块浏览器回归。python -m pytest tests/frontend/functional_workflows.py"""
import base64
import re

import pytest
from playwright.sync_api import expect


@pytest.fixture
def page(browser):
    with browser.new_context(viewport={"width": 1440, "height": 900}) as context:
        yield context.new_page()


def open_page(page, site, key):
    page.goto(f"{site}/#{key}")
    expect(page.locator("#p_" + key)).to_be_visible()
    expect(page.locator("#app_source")).to_be_enabled()


def test_missing_chart_value_stays_distinct_from_zero(page, site):
    open_page(page, site, "viz")
    expect(page.locator("#vz_src")).to_have_value("demo")
    sql = "SELECT 'A' AS period, 1 AS value UNION ALL SELECT 'B', NULL UNION ALL SELECT 'C', 0"
    page.locator("#vz_sql").fill(sql)
    page.get_by_role("button", name="运行取数").click()
    expect(page.locator("#vz_info")).to_contain_text("3 行")
    preview = page.locator("#vz_prev")
    missing = {}
    for kind in ("line", "bar", "area", "pie"):
        page.locator("#vz_type").select_option(kind)
        expect(preview.locator("canvas")).to_be_visible()
        values = preview.evaluate("el => echarts.getInstanceByDom(el).getOption().series[0].data")
        if kind == "pie":
            slices = {point["name"]: point["value"] for point in values}
            assert slices["A"] == 1 and slices["C"] == 0
            missing[kind] = slices.get("B")  # 饼图也可省略无观测的扇区。
        else:
            assert values[0] == 1 and values[2] == 0
            missing[kind] = values[1]
    assert all(value in (None, "-") for value in missing.values()), f"各图对缺失观测的取值为 {missing}"


@pytest.mark.parametrize("edited", [False, True], ids=["unchanged", "edited"])
def test_adding_an_edited_query_uses_a_verified_chart_binding(page, site, edited):
    open_page(page, site, "viz")
    expect(page.locator("#vz_src")).to_have_value("demo")
    page.locator("#vz_type").select_option("kpi")
    page.locator("#vz_sql").fill("SELECT 11 AS old_measure")
    page.get_by_role("button", name="运行取数").click()
    expect(page.locator("#vz_prev")).to_contain_text("11")
    if edited:
        page.locator("#vz_sql").fill("SELECT 22 AS new_measure")
    button = page.get_by_role("button", name="添加到看板")
    if edited and not button.is_enabled():
        return  # 已编辑未运行时，允许禁止添加。
    button.click()
    card = page.locator("#vz_grid .vz-card")
    if edited:
        warning = page.get_by_text(re.compile("重新.*取数|重新.*运行|先.*取数")).filter(visible=True)
        expect(card.or_(warning).first).to_be_visible()
        if not card.count():
            return
    # 可保留最后成功预览，也可重新取数并重绑坐标。两者都应能读出指标。
    binding = r"old_measure\s*11|new_measure\s*22" if edited else r"old_measure\s*11"
    expect(card.locator(".vc")).to_have_text(re.compile(binding))


@pytest.mark.parametrize("size", [256, 256 * 1024], ids=["small", "256-KiB"])
def test_ontology_chat_attachment_reaches_the_stream_intact(page, site, size):
    open_page(page, site, "claw")
    expect(page.locator("#claw_graph")).to_have_value("demo")
    content = b"x" * size
    page.locator("#claw_file").set_input_files({"name": "notes.txt", "mimeType": "text/plain", "buffer": content})
    page.locator("#claw_q").fill("请读取随附的测试说明。")
    sent = []
    page.on("request", lambda request: sent.append(request.post_data_json)
            if request.method == "POST" and request.url.endswith("/api/ont/chat/stream") else None)
    page.locator("#claw_btn").click()
    expect(page.locator("#claw_log .msg-ai").last).not_to_contain_text("处理中", timeout=20000)
    expect(page.locator("#claw_btn")).to_be_enabled(timeout=20000)
    assert len(sent) == 1, page.locator("#claw_log .msg-ai").last.inner_text()
    attachment = sent[0]["attachments"][0]
    assert attachment["name"] == "notes.txt" and base64.b64decode(attachment["b64"]) == content


def test_ontology_chat_history_can_be_opened_with_keyboard(page, site):
    # 用正常 API 准备一条可识别的历史，再从 UI 回到这条记录。
    title = "历史键盘回放验收"
    request = page.request
    cid = request.post(site + "/api/ont/chats/new", data={"graph": "demo"}).json()["id"]
    response = request.post(site + "/api/ont/chat", data={"id": cid, "graph": "demo", "message": title})
    assert response.ok and response.json().get("reply")
    open_page(page, site, "claw")
    entry = page.locator("#claw_sessions").get_by_text(title, exact=False)
    expect(entry).to_be_visible()
    page.locator("#p_claw").get_by_role("button", name="新会话").focus()
    # 同一站点可能已经有其他会话，逐个 Tab，直到目标项或离开历史列表。
    for _ in range(page.locator("#claw_sessions *").count() + 1):
        page.keyboard.press("Tab")
        in_list = page.locator("#claw_sessions").evaluate("el => el.contains(document.activeElement)")
        assert in_list, "Tab 略过了历史会话列表"
        if entry.evaluate("el => el.contains(document.activeElement) || document.activeElement.contains(el)"):
            break
    else:
        pytest.fail("Tab 无法选中历史会话")
    page.keyboard.press("Enter")
    expect(page.locator("#claw_log .msg-user")).to_have_text(title)


def test_mobile_menu_exposes_expanded_and_collapsed_state(page, site):
    page.set_viewport_size({"width": 390, "height": 844})
    open_page(page, site, "home")
    toggle = page.get_by_role("button", name="菜单", exact=True)
    target = page.locator('aside [data-p="sqldev"]')
    expect(target).to_be_hidden()
    toggle.focus()
    page.keyboard.press("Enter")
    expect(target).to_be_visible()
    expect(toggle).to_have_attribute("aria-expanded", "true")
    target.press("Enter")
    expect(page.locator("#p_sqldev")).to_be_visible()
    expect(target).to_be_hidden()
    expect(toggle).to_have_attribute("aria-expanded", "false")

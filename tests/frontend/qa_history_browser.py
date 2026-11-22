"""IR-002 深度问数历史。单独运行 python -m pytest tests/frontend/qa_history_browser.py。"""
import re

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import expect

CUSTOMERS = "dim_customer有多少条记录？"
PRODUCTS = "dim_product有多少条记录？"


@pytest.fixture
def page(browser, site):
    with browser.new_context() as context:
        tab = context.new_page()
        tab.goto(site + "/#chat")
        expect(tab.locator("#chat_q")).to_be_visible()
        yield tab


def ask(page, question):
    page.locator("#chat_q").fill(question)
    page.locator("#chat_btn").click()
    card = page.locator("#dq_stream .dq-card").last
    expect(card.get_by_text("本次实时分析", exact=True)).to_be_visible(timeout=30000)
    expect(card.locator(".dq-res").first).to_be_visible()
    expect(page.locator("#chat_btn")).to_be_enabled()
    return card.locator(".dq-insight").inner_text()


def history_entry(page, title):
    return page.locator("#dq_hist .it").filter(has_text=title)


def test_completed_turns_replay_after_reload(page):
    answers = [ask(page, question) for question in (CUSTOMERS, PRODUCTS)]
    page.reload()
    history_entry(page, CUSTOMERS).click(timeout=5000)
    expect(page.locator("#dq_stream .dq-usermsg")).to_have_text([CUSTOMERS, PRODUCTS])
    expect(page.locator("#dq_stream .dq-insight")).to_have_text(answers)
    expect(page.locator("#dq_stream").get_by_text("历史回放", exact=True)).to_have_count(2)


def test_history_replays_with_tab_and_enter(page):
    answer = ask(page, CUSTOMERS)
    page.reload()
    entry = history_entry(page, CUSTOMERS)
    expect(entry).to_be_visible()
    page.locator(".dq-hist-hd a").focus()
    page.keyboard.press("Tab")
    assert entry.evaluate("el => el.contains(document.activeElement)"), "Tab 应进入刚保存的会话"
    page.keyboard.press("Enter")
    expect(page.locator("#dq_stream .dq-insight")).to_have_text(answer)
    expect(page.locator("#dq_stream").get_by_text("历史回放", exact=True)).to_be_visible()


def test_replay_preserves_the_original_result_reference(page):
    ask(page, CUSTOMERS)
    reference = page.locator("#dq_stream .evc-b").get_by_text(re.compile(r"^结果\s+\S+$"))
    expect(reference).to_be_visible()
    original = reference.inner_text()
    page.reload()
    history_entry(page, CUSTOMERS).click(timeout=5000)
    expect(page.locator("#dq_stream").get_by_text("历史回放", exact=True)).to_be_visible()
    expect(reference).to_have_text(original)


def test_completed_chat_is_listed_when_returning_from_another_page(page):
    answer = ask(page, CUSTOMERS)
    page.locator('.nav[data-p="library"]').click()
    expect(page.locator("#p_library")).to_be_visible()
    page.locator('.nav[data-p="chat"]').click()
    expect(history_entry(page, CUSTOMERS)).to_be_visible()
    history_entry(page, CUSTOMERS).click()
    expect(page.locator("#dq_stream .dq-insight")).to_have_text(answer)


def test_saved_chat_replays_when_metrics_are_unavailable(page):
    answer = ask(page, CUSTOMERS)
    page.route("**/api/metrics", lambda route: route.fulfill(status=503, json={"error": "指标服务暂不可用"}))
    with page.expect_response(lambda response: response.url.endswith("/api/metrics")) as metrics:
        page.reload()
    assert metrics.value.status == 503
    expect(history_entry(page, CUSTOMERS)).to_be_visible()
    history_entry(page, CUSTOMERS).click()
    expect(page.locator("#dq_stream .dq-insight")).to_have_text(answer)


def test_storage_quota_cannot_silently_drop_a_completed_chat(page):
    previous = ask(page, CUSTOMERS)
    page.locator(".dq-hist-hd a").click()
    expect(history_entry(page, CUSTOMERS)).to_be_visible()
    # 只占用本用例的同源空间，直到浏览器连一个字符也写不下。
    page.evaluate("""() => {
        const key = 'test-quota-filler';
        let value = '';
        for (let size = 1024 * 1024; size >= 1; size /= 2) {
            const block = 'x'.repeat(size);
            while (true) {
                try { localStorage.setItem(key, value + block); }
                catch (err) { if (err.name !== 'QuotaExceededError') throw err; break; }
                value += block;
            }
        }
    }""")
    newest = ask(page, PRODUCTS)
    # 实现可选择备用存储，也可在用户离开、有机会保留答案时说明保存失败。
    warning = page.get_by_text(re.compile(
        r"(?:历史|会话|保存|存储).*(?:失败|已满|不足|未保存|无法)|(?:无法|未能|未|不能)保存"
    )).filter(visible=True)
    try:
        warning.first.wait_for(timeout=2000)
        warned = True
    except PlaywrightTimeout:
        warned = False
    page.reload()
    history_entry(page, CUSTOMERS).click(timeout=5000)
    expect(page.locator("#dq_stream .dq-insight")).to_have_text(previous)
    latest = history_entry(page, PRODUCTS)
    if not warned:
        expect(latest, "浏览器配额已满，新会话既无法重开，离开前也没有保存失败提示").to_be_visible()
    if latest.is_visible():
        latest.click()
        expect(page.locator("#dq_stream .dq-insight")).to_have_text(newest)


def test_failed_followup_can_be_saved_again_without_losing_the_previous_turn(page):
    previous = ask(page, CUSTOMERS)
    page.evaluate("""() => {
        const write = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
            if (key === 'dq_convs') {
                Storage.prototype.setItem = write;
                throw new DOMException('Storage full', 'QuotaExceededError');
            }
            return write.call(this, key, value);
        };
    }""")
    newest = ask(page, PRODUCTS)
    assert page.evaluate("JSON.parse(localStorage.getItem('dq_convs'))[0].turns.length") == 1
    retry = page.get_by_role("button", name="重试保存")
    retry.click()
    expect(retry).to_be_hidden()
    page.reload()
    history_entry(page, CUSTOMERS).click()
    expect(page.locator("#dq_stream .dq-insight")).to_have_text([previous, newest])

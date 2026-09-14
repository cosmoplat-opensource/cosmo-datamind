"""Real Chromium regression with fixture HTTP responses; no server or real data is used.

Run: .venv/bin/python tests/frontend/build-browser.py --output /tmp/datamind-build-ui
"""
import argparse
import asyncio
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[2]
LONG_NAME = "订单与客户跨组织的本体构建示例_" + "long_name_" * 12
PROFILE = {"version": 1, "industry": {"id": "none", "mode": "reference"},
           "ontology_standard": {"id": "bfo_iof", "mode": "reference"}}


async def main(output, base_url=None):
    output.mkdir(parents=True, exist_ok=True)
    failures = []
    requests = []
    response_mode = "truncated"

    async def serve(route):
        parsed = urlparse(route.request.url)
        name = parsed.path
        if base_url and name not in ("/api/build/inquire", "/api/graph/built_test"):
            return await route.continue_()
        if name == "/":
            return await route.fulfill(path=str(ROOT / "ui/index.html"), content_type="text/html")
        if name.startswith("/assets/") or name.startswith("/vendor/"):
            relative = name.removeprefix("/assets/") if name.startswith("/assets/") else name.lstrip("/")
            file = ROOT / "ui" / relative
            if file.is_file():
                return await route.fulfill(path=str(file), content_type=mimetypes.guess_type(str(file))[0] or "text/plain")
        if name == "/api/build/inquire":
            requests.append(route.request.post_data_json)
            if response_mode == "http_error":
                return await route.fulfill(status=429, json={"error": "当前构建名额已满"})
            events = [{"type": "status", "text": "正在构建测试本体"}]
            if response_mode == "success":
                events += [{"type": "done", "graph_key": "built_test", "name": LONG_NAME,
                            "stats": {"objects": 1, "links": 0}, "elapsed": 1,
                            "summary": "需要人工复核候选定义。", "quality": {"result": "review"}}]
            body = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n" for event in events)
            return await route.fulfill(content_type="text/event-stream", body=body)
        fixtures = {
            "/api/ont/runtimes": {"current": "test", "runtimes": []},
            "/api/build/defaults": {"build_query": "补充订单与客户关系", "build_name": "测试本体"},
            "/api/build/sources": {"sources": [{"id": "demo", "name": "隔离测试源", "kind": "sqlite", "tables": 2, "ready": True, "builtin": True}], "assets": []},
            "/api/build/skills": [{"name": "ontology-semi-auto", "desc": "先提出候选，再逐项验证。", "builtin": True}],
            "/api/build/built": [{"key": "built_test", "name": LONG_NAME, "objects": 1, "events": 0, "links": 0, "verified": 0, "ts": "2026-09-07"}],
            "/api/build/references": {"version": 1, "industries": [{"id": "none", "name": "不选择行业参照"}], "ontology_standards": [{"id": "bfo_iof", "name": "BFO / IOF", "asset": {"ready": True}}], "default": PROFILE},
            "/api/graph/built_test": {"nodes": [{"id": "orders", "name": "订单", "kind": "object"}], "edges": []},
            "/api/ui/version": {"version": "test"},
        }
        if name in fixtures:
            return await route.fulfill(json=fixtures[name])
        return await route.fulfill(status=404, json={"error": "fixture route not found: " + name})

    async with async_playwright() as pw:
        candidates = [os.environ.get("DATAMIND_BROWSER_EXECUTABLE"),
                      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                      "/usr/bin/google-chrome", "/usr/bin/chromium"]
        executable = next((value for value in candidates if value and Path(value).is_file()), None)
        browser = await pw.chromium.launch(executable_path=executable)
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: failures.append(str(error)))
        await page.route("**/*", serve)
        await page.goto((base_url or "http://datamind-ui.test").rstrip("/") + "/#build", wait_until="networkidle")
        await page.locator("#bc_skills .bc-skcard").wait_for()
        assert await page.locator("#bc_q").get_attribute("aria-label")
        await page.locator("#bc_q").fill("补充订单与客户关系")
        await page.locator("#bc_send").click()
        await page.locator(".bc-failure").wait_for()
        assert await page.locator("#bc_q").input_value() == "补充订单与客户关系"
        assert "未收到完成" in await page.locator(".bc-failure").inner_text()
        assert await page.locator("#bc_log").get_attribute("aria-busy") == "false"
        response_mode = "http_error"
        await page.locator("#bc_send").click()
        await page.wait_for_function("document.querySelectorAll('.bc-failure').length===2")
        assert "429" in await page.locator(".bc-failure").last.inner_text()
        response_mode = "success"
        await page.locator("#bc_send").click()
        await page.locator(".bc-result canvas").wait_for()
        await page.locator("#bc_q").fill("追加第二轮")
        await page.locator("#bc_send").click()
        await page.wait_for_function("document.querySelectorAll('.bc-result canvas').length===2")
        ids = await page.locator(".bc-viz").evaluate_all("els=>els.map(e=>e.id)")
        assert len(ids) == len(set(ids)) == 2
        assert len(requests) == 4
        # Replayed untrusted fields must render as text, including numeric-looking statistics.
        await page.evaluate("""() => {const value='<img src=x onerror=window.__uiXss=1>';
            document.querySelector('#bc_log').insertAdjacentHTML('beforeend',bcResultHTML({name:value,elapsed:value,stats:{objects:value},graph_key:'safe'}));}""")
        assert await page.locator("#bc_log img").count() == 0
        await page.locator(".bc-result").last.evaluate("element=>element.remove()")
        for width in [1440, 768, 390]:
            await page.set_viewport_size({"width": width, "height": 1000})
            await page.wait_for_timeout(100)
            geometry = await page.evaluate("""() => ({page:document.documentElement.scrollWidth,viewport:innerWidth,
                grid:document.querySelector('.bc-grid').getBoundingClientRect().width,
                input:document.querySelector('#bc_q').getBoundingClientRect().width})""")
            assert geometry["page"] <= width + 1, geometry
            assert geometry["input"] >= 150, geometry
            assert await page.locator(".bc-viz canvas").evaluate_all("els=>els.every(e=>Math.abs(e.getBoundingClientRect().width-e.closest('.bc-viz').clientWidth)<3)"), "graph canvas did not follow container resize"
            if width <= 820:
                assert await page.locator("#bc_built").evaluate("e=>e.getBoundingClientRect().height<=281"), "built list pushed the composer down on mobile"
                if await page.locator("#bc_built").evaluate("e=>e.scrollHeight>e.clientHeight"):
                    await page.locator("#bc_built").focus()
                    await page.keyboard.press("ArrowDown")
                    await page.wait_for_function("document.querySelector('#bc_built').scrollTop>0")
                    await page.locator("#bc_built").evaluate("e=>e.scrollTop=0")
            await page.locator("#bc_q").scroll_into_view_if_needed()
            await page.screenshot(path=str(output / f"build-{width}.png"), full_page=True)
        await page.locator("#bc_built .x").first.focus()
        await page.keyboard.press("Enter")
        assert await page.get_by_text("再点一次 ✕ 确认删除该构建产物", exact=True).count() == 1
        assert not failures, failures
        await browser.close()
    report = {"passed": ["truncated SSE recovery", "HTTP 429 detail", "CRLF completion", "two independent preview canvases",
                         "result XSS escaping", "1440/768/390px no page overflow", "graph canvas follows resize", "mobile built list capped at 280px with keyboard scrolling", "keyboard delete confirmation", "no page errors"],
              "fixture_requests": len(requests), "screenshots": str(output)}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/tmp/datamind-build-ui"))
    parser.add_argument("--url", help="Optional isolated service; only build responses and their preview graph are mocked")
    args = parser.parse_args()
    asyncio.run(main(args.output, args.url))

"""Actual browser CSV upload/error recovery with a disposable owned fixture."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.browser_runtime import close_browser, managed_playwright  # noqa: E402


async def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    table = "codex_acceptance_ui_" + uuid.uuid4().hex[:8]
    name = table + ".csv"
    checks, errors = [], []

    def check(label, condition):
        checks.append({"name": label, "passed": bool(condition)})
        print(f"{'PASS' if condition else 'FAIL'} {label}", flush=True)
        if not condition:
            raise AssertionError(label)

    async with managed_playwright() as pw:
        executable = next((p for p in [os.environ.get("DATAMIND_BROWSER_EXECUTABLE"),
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome"] if p and Path(p).is_file()), None)
        browser = await pw.chromium.launch(executable_path=executable)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def accept(dialog):
            await dialog.accept()

        page.on("dialog", accept)
        try:
            await page.goto(args.url + "/#build", wait_until="domcontentloaded")
            await page.locator("#p_build.on").wait_for()
            for valid, data in [(True, b"id,amount\n1,10\n2,20\n"), (False, b"id,id\n3,99\n")]:
                async with page.expect_response(lambda r: r.url.endswith("/api/build/upload") and r.request.method == "POST") as pending:
                    await page.locator("#bc_files").set_input_files({"name": name, "mimeType": "text/csv", "buffer": data})
                response = await pending.value
                result = await response.json()
                if valid:
                    check("browser upload materializes rows", name in result["saved"] and result["tables"][0]["rows"] == 2)
                    await page.locator("#bc_assets .bc-chip").filter(has_text=name).wait_for()
                    check("uploaded asset visible", True)
                else:
                    check("invalid refresh rejected", not result["saved"] and bool(result["tables"][0].get("error")))
                    await page.get_by_text("上传失败：", exact=False).wait_for()
                    check("failure is shown to user", True)
                    check("only the latest upload notice is visible", await page.locator("#app_toast").count() == 1)
                    await page.wait_for_timeout(250)
                    await page.screenshot(path=str(args.output / "upload-error-preserved.png"), full_page=True)
            response = await context.request.post(args.url + "/api/query", data={"src": "uploads", "sql": f'SELECT * FROM "{table}" ORDER BY id'})
            data = await response.json()
            check("old rows survive rejected refresh", data.get("rows") == [{"id": "1", "amount": "10"}, {"id": "2", "amount": "20"}])
            async with page.expect_response(lambda r: r.url.endswith("/api/build/asset/delete") and r.request.method == "POST") as pending:
                await page.locator("#bc_assets .bc-chip").filter(has_text=name).locator(".x").click()
            response = await pending.value
            check("own asset deleted through UI", response.ok and (await response.json()).get("ok"))
            await page.locator("#bc_assets .bc-chip").filter(has_text=name).wait_for(state="detached")
            check("own asset disappears from UI", True)
            check("no page script errors", not errors)
        except Exception as exc:
            checks.append({"name": "workflow", "passed": False, "error": str(exc)[:400]})
        finally:
            # Exact random fixture only; safe if UI cleanup already removed it.
            await context.request.post(args.url + "/api/build/asset/delete", data={"name": name})
            await close_browser(browser)
    report = {"passed": all(c["passed"] for c in checks), "checks": checks, "page_errors": errors, "filename": name, "mocked_routes": False}
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return int(not report["passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
